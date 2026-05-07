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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from media_archivist.models.api import ArchiveRequest, Task

LOG = logging.getLogger("media_archivist.server.scheduler")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tasks_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".tasks.json")


class TaskStore:
    """JSON-backed map of task id → :class:`Task`."""

    def __init__(self, db_path: str) -> None:
        self.path = _tasks_path(db_path)
        self.tasks: Dict[str, Task] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.tasks = {}
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            LOG.exception("failed to load %s", self.path)
            self.tasks = {}
            return
        self.tasks = {tid: Task.model_validate(blob) for tid, blob in data.items()}

    def save(self) -> None:
        payload = {tid: t.model_dump(mode="json") for tid, t in self.tasks.items()}
        self.path.write_text(json.dumps(payload, indent=2))

    def add(self, request: ArchiveRequest) -> Task:
        task = Task(id=uuid.uuid4().hex, request=request)
        self.tasks[task.id] = task
        self.save()
        return task

    def update(self, task: Task) -> None:
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
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        # Re-queue anything still pending from a previous run.
        for t in self.store.pending():
            t.status = "queued"
            t.started = None
            self.store.update(t)
            self._queue.put_nowait(t)

    def submit(self, request: ArchiveRequest) -> Task:
        task = self.store.add(request)
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
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
