"""Recurring work inside ``serve``: the subscription index, syncs and metadata fills.

Every ``tick`` seconds the server queues, on its task scheduler:

* one ``index`` task when the working index has work due (a listing, a
  feed, a Playlists tab, playlist videos, pictures or per-video details; see
  :mod:`media_archivist.ytindex.engine`), unless one is already queued;
* a ``sync`` task for each subscription whose interval has passed since its
  ``last_synced_at`` (kept in the subscriptions sidecar, so a restart picks
  the schedule up where it was), unless one is already queued or its thread is still running (a sync the
  scheduler timed out on keeps running until it ends);
* an ``enrich`` task when YouTube rows lack a duration, channel or upload
  date, at most once per ``enrich_every`` seconds, never while the yt-dlp
  limiter is in its bot-check back-off and never beside another enrich.

All run as ordinary tasks, so they appear in ``GET /tasks`` and take
their turn with archive and download jobs.

While the index is on, YouTube subscriptions are kept current by it alone:
the sidecar's ``sync`` tasks are queued only for the other backends, so a
channel is never listed twice.

``MEDIA_ARCHIVIST_SCHEDULED_SYNC=0`` turns off all scheduled subscription
work, ``MEDIA_ARCHIVIST_SCHEDULED_INDEX=0`` only the index (YouTube
subscriptions then go back to the sidecar's syncs), and
``MEDIA_ARCHIVIST_REENRICH=0`` the metadata fill.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, List, Optional

from media_archivist.models.api import EnrichRequest, IndexRequest, SyncRequest, Task

LOG = logging.getLogger("media_archivist.server.periodic")

DEFAULT_TICK_S = 60.0
INDEX_TIMEOUT_S = 3600
DEFAULT_ENRICH_EVERY_S = 3600.0
_FALSY = {"0", "false", "no", "off"}


def _env_on(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in _FALSY


class Periodic:
    def __init__(self, db_path: str, scheduler, *, tick: float = DEFAULT_TICK_S,
                 sync_enabled: Optional[bool] = None,
                 enrich_enabled: Optional[bool] = None,
                 index_enabled: Optional[bool] = None,
                 enrich_every: float = DEFAULT_ENRICH_EVERY_S,
                 enrich_limit: int = 100,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.db_path = db_path
        self.scheduler = scheduler
        self.tick = tick
        self.sync_enabled = _env_on("MEDIA_ARCHIVIST_SCHEDULED_SYNC") if sync_enabled is None else sync_enabled
        self.enrich_enabled = _env_on("MEDIA_ARCHIVIST_REENRICH") if enrich_enabled is None else enrich_enabled
        self.index_enabled = _env_on("MEDIA_ARCHIVIST_SCHEDULED_INDEX") if index_enabled is None else index_enabled
        # The YouTube client and gate the indexer uses; None means tutubo
        # and the installed gate.
        self.index_client = None
        self.index_gate = None
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
        busy = {t.request.url for t in self._pending("sync")} | subs_mod.syncing_urls()
        queued = []
        for sub in subs_mod.due_subscriptions(self.db_path):
            if sub.url in busy or (self.index_enabled and sub.backend == "youtube"):
                continue
            queued.append(self.scheduler.submit(SyncRequest(url=sub.url)))
            busy.add(sub.url)
        return queued

    def indexer(self):
        from media_archivist.indexdb import open_index
        from media_archivist.ytindex.engine import Indexer

        return Indexer(open_index(self.db_path), client=self.index_client, gate=self.index_gate)

    def queue_index(self) -> Optional[Task]:
        """One ``index`` task when anything in the index is due and none is queued."""
        if not (self.sync_enabled and self.index_enabled):
            return None
        if any(t.request.subscription_id is None for t in self._pending("index")):
            return None
        if not self.indexer().due():
            return None
        return self.scheduler.submit(IndexRequest())

    def queue_listing(self, subscription_id: str) -> Task:
        """A full listing of one subscription now (a new subscription, a refresh)."""
        for t in self._pending("index"):
            if t.request.subscription_id == subscription_id and t.status == "queued":
                return t
        return self.scheduler.submit(IndexRequest(subscription_id=subscription_id))

    async def run_index_task(self, task: Task) -> None:
        indexer = self.indexer()
        sid = task.request.subscription_id
        if sid is None:
            done = await asyncio.wait_for(asyncio.to_thread(indexer.run_due),
                                          timeout=INDEX_TIMEOUT_S)
            task.detail = ", ".join(f"{k} {v}" for k, v in sorted(done.items())) or "nothing due"
            return
        outcome = await asyncio.wait_for(asyncio.to_thread(indexer.refresh, sid),
                                         timeout=INDEX_TIMEOUT_S)
        if outcome == "paused":
            task.detail = "waiting: listings are paused after a bot check; it runs when the pause ends"
        elif outcome == "gone":
            task.detail = "the subscription no longer exists"
        elif outcome == "unsupported":
            task.detail = "only YouTube channels and playlists are indexed"
        elif outcome != "ok":
            row = indexer.index.execute("SELECT last_error FROM subscriptions WHERE id = ?",
                                        (sid,)).fetchone()
            raise RuntimeError((row and row["last_error"]) or f"listing {outcome}")

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
            task = self.queue_index()
            if task is not None:
                queued.append(task)
        except asyncio.QueueFull:
            LOG.warning("task queue full; indexing waits for the next tick")
        except Exception:
            LOG.exception("scheduling the subscription index failed")
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
