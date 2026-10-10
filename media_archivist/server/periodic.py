"""Recurring work inside ``serve``: subscription syncs and metadata fills.

Every ``tick`` seconds the server queues, on its task scheduler:

* a ``sync`` task for each subscription whose interval has passed since its
  ``last_synced_at`` (kept in the subscriptions sidecar, so a restart picks
  the schedule up where it was), unless one is already queued or running;
* an ``enrich`` task when YouTube rows lack a duration, channel or upload
  date, at most once per ``enrich_every`` seconds, never while the yt-dlp
  limiter is in its bot-check back-off and never beside another enrich.

Both run as ordinary tasks, so they appear in ``GET /tasks`` and take
their turn with archive and download jobs.

``MEDIA_ARCHIVIST_SCHEDULED_SYNC=0`` and ``MEDIA_ARCHIVIST_REENRICH=0`` turn
the two parts off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, List, Optional

from media_archivist.models.api import EnrichRequest, SyncRequest, Task

LOG = logging.getLogger("media_archivist.server.periodic")

DEFAULT_TICK_S = 60.0
DEFAULT_ENRICH_EVERY_S = 3600.0
_FALSY = {"0", "false", "no", "off"}


def _env_on(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in _FALSY


class Periodic:
    def __init__(self, db_path: str, scheduler, *, tick: float = DEFAULT_TICK_S,
                 sync_enabled: Optional[bool] = None,
                 enrich_enabled: Optional[bool] = None,
                 enrich_every: float = DEFAULT_ENRICH_EVERY_S,
                 enrich_limit: int = 100,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.db_path = db_path
        self.scheduler = scheduler
        self.tick = tick
        self.sync_enabled = _env_on("MEDIA_ARCHIVIST_SCHEDULED_SYNC") if sync_enabled is None else sync_enabled
        self.enrich_enabled = _env_on("MEDIA_ARCHIVIST_REENRICH") if enrich_enabled is None else enrich_enabled
        self.enrich_every = enrich_every
        self.enrich_limit = enrich_limit
        self._clock = clock
        self._last_enrich: Optional[float] = None
        self._task: Optional[asyncio.Task] = None

    def _pending(self, kind: str) -> List[Task]:
        return [t for t in self.scheduler.store.pending() if t.kind == kind]

    def queue_due_syncs(self) -> List[Task]:
        from media_archivist import subscriptions as subs_mod

        if not self.sync_enabled:
            return []
        busy = {t.request.url for t in self._pending("sync")}
        queued = []
        for sub in subs_mod.due_subscriptions(self.db_path):
            if sub.url in busy:
                continue
            queued.append(self.scheduler.submit(SyncRequest(url=sub.url)))
            busy.add(sub.url)
        return queued

    def queue_enrich(self) -> Optional[Task]:
        from media_archivist import ytmeta

        if not self.enrich_enabled or ytmeta.LIMITER.blocked or self._pending("enrich"):
            return None
        now = self._clock()
        if self._last_enrich is not None and now - self._last_enrich < self.enrich_every:
            return None
        if not ytmeta.pending_count(self.db_path):
            return None
        self._last_enrich = now
        return self.scheduler.submit(EnrichRequest(limit=self.enrich_limit))

    def run_once(self) -> List[Task]:
        """Queue whatever is due now; returns the tasks queued."""
        queued: List[Task] = []
        try:
            queued.extend(self.queue_due_syncs())
        except asyncio.QueueFull:
            LOG.warning("task queue full; scheduled syncs wait for the next tick")
        except Exception:
            LOG.exception("scheduling subscription syncs failed")
        try:
            task = self.queue_enrich()
            if task is not None:
                queued.append(task)
        except asyncio.QueueFull:
            LOG.warning("task queue full; metadata fill waits for the next tick")
        except Exception:
            LOG.exception("scheduling the metadata fill failed")
        return queued

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.tick)
            await asyncio.to_thread(self.run_once)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not (self.sync_enabled or self.enrich_enabled):
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
