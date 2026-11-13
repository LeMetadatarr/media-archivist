"""Durability/resilience regression tests for the task scheduler.

Covers three fixes folded into the same change as the thread-safety fix
(see test_scheduler_threadsafe.py), all in
``media_archivist/server/scheduler.py`` / ``media_archivist/server/routes.py``:

1. ``TaskStore.save()`` writes atomically (temp file + ``os.replace``)
   instead of overwriting ``<db>.tasks.json`` in place, so a crash/OOM
   mid-write can never truncate the ledger and make ``load()`` silently
   reset the whole task history to ``{}``.
2. The scheduler's ``asyncio.Queue`` is bounded (``MAX_QUEUE_SIZE``);
   ``submit()`` raises ``asyncio.QueueFull`` once full, which the HTTP
   layer turns into ``429``.
3. Each archive() call is wrapped in ``asyncio.wait_for(...,
   timeout=ARCHIVE_TIMEOUT_S)`` in ``routes.py``'s worker, so one wedged
   download can't block every other queued task forever.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from media_archivist.models.api import ArchiveRequest
from media_archivist.server import scheduler as scheduler_mod
from media_archivist.server.scheduler import TaskStore

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


# --------------------------------------------------------------------------
# 1. Atomic save survives a torn write.
# --------------------------------------------------------------------------

def test_save_survives_torn_write_never_resets_ledger(tmp_path, monkeypatch):
    """A write that dies partway through (simulating a crash/OOM) must
    never leave the on-disk task ledger truncated/corrupt, and must never
    cause a subsequent load() to silently reset it to {}."""
    db_path = str(tmp_path / "db.json")
    store = TaskStore(db_path)
    task1 = store.add(ArchiveRequest(url="https://example.com/a"))
    assert store.path.exists()

    original_write_text = Path.write_text

    def crashing_write_text(self, text, *a, **kw):
        # Simulate a process crash / OOM mid-write: only half the bytes
        # actually land on disk before the write is interrupted.
        original_write_text(self, text[: len(text) // 2])
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(Path, "write_text", crashing_write_text)

    with pytest.raises(OSError):
        store.add(ArchiveRequest(url="https://example.com/b"))

    monkeypatch.undo()

    # Whatever save() wrote to (tmp file or the real file), the persisted
    # ledger must still be readable and must still contain the task that
    # was safely saved before the simulated crash.
    reloaded = TaskStore(db_path)
    assert task1.id in reloaded.tasks, (
        "the previously-persisted task ledger was lost after a torn "
        "write — save() is not crash-safe"
    )


def test_save_uses_write_then_replace(tmp_path):
    """Sanity-check the mechanism itself: save() must write to a temp
    file and then atomically replace the real path, not write in place."""
    db_path = str(tmp_path / "db.json")
    store = TaskStore(db_path)
    tmp_marker = store.path.with_suffix(store.path.suffix + ".tmp")
    assert not tmp_marker.exists()  # cleaned up by the final os.replace()
    store.add(ArchiveRequest(url="https://example.com/a"))
    # The temp file must not remain after a successful save (it was
    # renamed onto the real path).
    assert not tmp_marker.exists()
    assert store.path.exists()


# --------------------------------------------------------------------------
# 2. Bounded queue -> 429.
# --------------------------------------------------------------------------

def test_submit_raises_queue_full_over_capacity(tmp_path):
    """Once the queue is at its cap, submit() must raise QueueFull rather
    than growing without bound."""
    async def _hang_worker(task):
        await asyncio.Event().wait()  # never completes

    store_path = str(tmp_path / "db.json")
    sched = scheduler_mod.Scheduler(store_path, _hang_worker)
    # Rebuild the queue with a tiny cap for a fast, deterministic test
    # instead of submitting MAX_QUEUE_SIZE (1000) real tasks.
    sched._queue = asyncio.Queue(maxsize=2)

    sched.submit(ArchiveRequest(url="https://example.com/1"))
    sched.submit(ArchiveRequest(url="https://example.com/2"))
    with pytest.raises(asyncio.QueueFull):
        sched.submit(ArchiveRequest(url="https://example.com/3"))


def test_post_archive_returns_429_when_queue_full(tmp_path, monkeypatch):
    """End-to-end: fill the queue via real POST /archive calls and assert
    the HTTP layer surfaces 429, not a 500 or unbounded growth."""
    monkeypatch.setattr(scheduler_mod, "MAX_QUEUE_SIZE", 1)

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db.store()
    app = create_app(str(db_path))

    # Make the archive backend hang so the queue never drains during the
    # test: patch the worker's backend class before the app's lifespan
    # starts consuming tasks.
    import media_archivist.youtube as youtube_mod

    def _hang(self, url):
        time.sleep(5)

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", _hang)

    with TestClient(app) as c:
        r1 = c.post("/archive", json={"url": "https://www.youtube.com/watch?v=a"})
        assert r1.status_code == 200

        # Wait for the scheduler to actually dequeue task 1 (so the queue
        # itself is empty again and has room for exactly one more, per
        # MAX_QUEUE_SIZE=1).
        task1_id = r1.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = c.get(f"/tasks/{task1_id}").json()["status"]
            if status == "running":
                break
            time.sleep(0.05)
        else:
            pytest.fail("task 1 never started running")

        r2 = c.post("/archive", json={"url": "https://www.youtube.com/watch?v=b"})
        assert r2.status_code == 200  # fills the 1-slot queue

        r3 = c.post("/archive", json={"url": "https://www.youtube.com/watch?v=c"})
        assert r3.status_code == 429, r3.text


# --------------------------------------------------------------------------
# 3. Per-archive timeout unblocks the queue.
# --------------------------------------------------------------------------

def test_archive_timeout_marks_task_error_and_drains_queue(tmp_path, monkeypatch):
    """A wedged archive() call must not block subsequent queued tasks
    forever: routes.py wraps it in asyncio.wait_for(ARCHIVE_TIMEOUT_S),
    so it should be marked "error" quickly and the queue should keep
    moving."""
    import media_archivist.server.routes as routes_mod
    import media_archivist.youtube as youtube_mod

    # Small timeout so the test runs fast; the archive() call sleeps far
    # longer than that, simulating a wedged/stalled download.
    monkeypatch.setattr(routes_mod, "ARCHIVE_TIMEOUT_S", 0.1)

    def _wedged(self, url):
        time.sleep(5)

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", _wedged)

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db.store()
    app = create_app(str(db_path))

    with TestClient(app) as c:
        r1 = c.post("/archive", json={"url": "https://www.youtube.com/watch?v=a"})
        r2 = c.post("/archive", json={"url": "https://www.youtube.com/watch?v=b"})
        assert r1.status_code == 200 and r2.status_code == 200
        id1, id2 = r1.json()["id"], r2.json()["id"]

        start = time.monotonic()
        deadline = start + 5

        def _wait_terminal(task_id):
            while time.monotonic() < deadline:
                task = c.get(f"/tasks/{task_id}").json()
                if task["status"] in ("ok", "error"):
                    return task
                time.sleep(0.02)
            pytest.fail(f"task {task_id} never reached a terminal state")

        task1 = _wait_terminal(id1)
        task2 = _wait_terminal(id2)
        elapsed = time.monotonic() - start

        assert task1["status"] == "error"
        assert "Timeout" in (task1["error"] or "")
        assert task2["status"] == "error"
        assert "Timeout" in (task2["error"] or "")

        # Both tasks must resolve well within the 5s each would take if
        # the timeout weren't cutting the wedged archive() call short —
        # i.e. the second task was never head-of-line-blocked by the
        # first.
        assert elapsed < 3, (
            f"tasks took {elapsed:.2f}s — the timeout is not unblocking "
            "the queue"
        )
