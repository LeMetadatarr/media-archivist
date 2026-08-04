"""Tiny in-process task scheduler.

Persists task state to a sidecar ``<db>.tasks.json`` so a restart
re-queues anything that was still pending. Tasks run sequentially via
``asyncio.create_task``; this is good enough for a single-tenant local
service, with no external dependencies.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Union

from media_archivist.models.api import ArchiveRequest, DownloadRequest, Task

TaskRequest = Union[ArchiveRequest, DownloadRequest]

LOG = logging.getLogger("media_archivist.server.scheduler")

# Cap the pending-submission backlog so an abusive/looping caller can't grow
# memory (and the O(n) JSON rewrite on every TaskStore.save()) without
# bound. Past this many *queued* tasks, submit() raises asyncio.QueueFull,
# which routes.py/web.py translate into HTTP 429.
MAX_QUEUE_SIZE = 1000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tasks_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".tasks.json")


class TaskStore:
    """JSON-backed map of task id → :class:`Task`."""

    def __init__(self, db_path: str) -> None:
        self.path = _tasks_path(db_path)
        self.tasks: Dict[str, Task] = {}
        # add()/update() can be called concurrently from multiple
        # FastAPI threadpool worker threads (sync `def` handlers) as well
        # as from the event-loop thread running the scheduler's worker.
        # Without serializing save(), two threads racing on the shared
        # ``<path>.tmp`` filename can have one thread's os.replace() lose
        # to the other, raising FileNotFoundError and dropping a write.
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.tasks = {}
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            LOG.exception("failed to load %s; trying backup", self.path)
            data = self._load_backup()
            if data is None:
                self.tasks = {}
                return
        self.tasks = {tid: Task.model_validate(blob) for tid, blob in data.items()}

    def _load_backup(self) -> Optional[dict]:
        bak_path = self.path.with_suffix(self.path.suffix + ".bak")
        if not bak_path.exists():
            return None
        try:
            return json.loads(bak_path.read_text())
        except Exception:
            LOG.exception("backup %s is also corrupt", bak_path)
            return None

    def save(self) -> None:
        # Serialize the whole write-then-rename sequence: concurrent
        # callers must not share the same ``.tmp`` path mid-flight (see
        # __init__ comment).
        with self._lock:
            payload = {tid: t.model_dump(mode="json") for tid, t in self.tasks.items()}
            text = json.dumps(payload, indent=2)
            # Write-then-rename so a crash/OOM mid-write can never leave a
            # truncated/corrupt <db>.tasks.json on disk: os.replace() is
            # atomic on POSIX (same filesystem), so readers only ever see
            # the fully-written old file or the fully-written new file,
            # never a partial one. Without this, load() would hit
            # JSONDecodeError on the torn file and silently reset the
            # whole task ledger to {}.
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(text)
            # Best-effort backup of the last-known-good file, used by
            # load() to recover if the primary file is ever found
            # corrupt.
            if self.path.exists():
                try:
                    self.path.replace(self.path.with_suffix(self.path.suffix + ".bak"))
                except OSError:
                    LOG.exception("failed to back up %s before replace", self.path)
            os.replace(tmp_path, self.path)

    def update_progress(self, task_id: str, progress: int) -> None:
        """Thread-safe, in-memory-only progress update.

        Called from arbitrary worker threads (e.g. a yt-dlp
        ``progress_hook`` running inside ``asyncio.to_thread``), so it
        must not race the other mutators of ``self.tasks``. It
        deliberately does *not* call :meth:`save` — a download can fire
        this many times a second, and persisting the full O(n) task
        ledger to disk on every tick would defeat the point of the
        lock-protected, atomic ``save()`` (needless disk I/O + lock
        contention). The final status transition (``ok``/``error``) is
        still persisted via :meth:`update`, same as today; a mid-flight
        crash just means the resumed progress% is stale, which is
        cosmetic — the task itself is correctly re-queued either way.
        """
        with self._lock:
            task = self.tasks.get(task_id)
            if task is not None:
                task.progress = max(0, min(100, progress))

    def add(self, request: TaskRequest) -> Task:
        task = Task(id=uuid.uuid4().hex, request=request)
        with self._lock:
            self.tasks[task.id] = task
        self.save()
        return task

    def update(self, task: Task) -> None:
        with self._lock:
            self.tasks[task.id] = task
        self.save()

    def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def pending(self) -> List[Task]:
        return [t for t in self.tasks.values() if t.status in {"queued", "running"}]


class Scheduler:
    """Sequential async runner over a :class:`TaskStore`."""

    def __init__(self, db_path: str,
                 worker: Callable[[Task], Awaitable[None]]) -> None:
        self.db_path = db_path
        self.store = TaskStore(db_path)
        self._worker = worker
        self._queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Re-queue anything still pending from a previous run.
        for t in self.store.pending():
            t.status = "queued"
            t.started = None
            self.store.update(t)
            self._queue.put_nowait(t)

    def submit(self, request: TaskRequest) -> Task:
        # Bound the backlog: an unbounded queue lets a runaway/abusive
        # caller grow memory (and the O(n) JSON rewrite on every
        # TaskStore.save()) without limit. Check-then-enqueue is advisory
        # (a concurrent submit could slip in between the check and the
        # actual put), which is fine — the goal is a sane ceiling, not
        # exact admission control — and the underlying
        # ``self._queue.put_nowait`` still raises ``asyncio.QueueFull`` as
        # the hard backstop if it ever does race over the limit.
        if self._queue.full():
            raise asyncio.QueueFull(
                f"archive queue full ({MAX_QUEUE_SIZE} pending tasks)"
            )
        task = self.store.add(request)
        # ``submit`` may be called from a FastAPI sync (threadpool) handler,
        # i.e. from a thread other than the one running the event loop.
        # asyncio.Queue is not thread-safe: put_nowait() touches an
        # asyncio.Future owned by the loop, and calling it from a foreign
        # thread races with the loop and can raise or corrupt scheduler
        # state. Marshal the enqueue onto the loop thread when we know it;
        # the synchronous store.add() above still makes the task visible to
        # GET /tasks/{id} immediately regardless.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, task)
        else:
            self._queue.put_nowait(task)
        return task

    async def _run(self) -> None:
        while True:
            task = await self._queue.get()
            task.status = "running"
            task.started = _utcnow()
            self.store.update(task)
            try:
                await self._worker(task)
                task.status = "ok"
            except Exception as exc:
                LOG.exception("task %s failed", task.id)
                task.status = "error"
                task.error = repr(exc)
            task.finished = _utcnow()
            self.store.update(task)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
