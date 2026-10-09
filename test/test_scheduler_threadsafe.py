"""Regression test for thread-safe task submission.

``POST /archive`` (routes.py) and the WebUI archive form (web.py) are
declared as *sync* ``def`` handlers, so FastAPI/Starlette dispatches them
on a threadpool worker thread, never on the event-loop thread. Both call
``Scheduler.submit()``, which historically called
``asyncio.Queue.put_nowait()`` directly. ``asyncio.Queue`` is documented as
not thread-safe: ``put_nowait`` touches an ``asyncio.Future`` owned by the
event loop, and driving it from a foreign thread races with the loop.

This test exercises the fix two ways:

1. A deterministic, non-flaky assertion that ``submit()`` marshals the
   queue write through ``loop.call_soon_threadsafe`` (rather than calling
   ``put_nowait`` directly from the calling thread) whenever a loop has
   been registered via ``Scheduler.start()``. This is the specific
   invariant the fix guarantees and is reproducible on every run,
   regardless of GIL/scheduler timing.
2. An end-to-end smoke test that fires many concurrent ``POST /archive``
   requests from multiple threads via a real ``TestClient`` and asserts no
   submission is lost and every task is retrievable.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.server.scheduler import Scheduler  # noqa: E402
from media_archivist.models.api import ArchiveRequest  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def test_submit_marshals_queue_write_onto_loop_thread(tmp_path):
    """Once a loop is registered, submit() must not call put_nowait()
    directly from the calling thread — it must hand the call to
    ``loop.call_soon_threadsafe`` so the asyncio.Queue is only ever
    touched from the event-loop thread.

    This mirrors production: the event loop runs on its own thread (like
    uvicorn's main thread) while ``submit()`` is invoked from a *different*
    thread (like a FastAPI threadpool worker running a sync ``def``
    handler)."""

    async def _worker(task):  # pragma: no cover - never actually run
        pass

    scheduler = Scheduler(str(tmp_path / "db.json"), _worker)

    loop = asyncio.new_event_loop()
    loop_thread_box = {}

    def _run_loop():
        asyncio.set_event_loop(loop)
        loop_thread_box["thread"] = threading.current_thread()
        loop.run_forever()

    runner = threading.Thread(target=_run_loop, daemon=True)
    runner.start()
    # Wait for the loop to actually be running before registering it.
    while "thread" not in loop_thread_box:
        pass

    try:
        loop.call_soon_threadsafe(scheduler.start, loop)

        calls = []
        real_put_nowait = scheduler._queue.put_nowait

        def spy_put_nowait(task):
            calls.append(threading.current_thread())
            return real_put_nowait(task)

        scheduler._queue.put_nowait = spy_put_nowait

        submitting_thread = threading.current_thread()
        assert submitting_thread is not loop_thread_box["thread"]

        # submit() is called from this (non-loop) thread, exactly as a
        # FastAPI sync `def` handler would call it from a threadpool
        # worker thread.
        task = scheduler.submit(ArchiveRequest(url="https://example.com/x"))

        # submit() must return the Task synchronously, from the caller's
        # own thread, without waiting on the loop.
        assert task.id
        assert task.status == "queued"

        # Give the loop thread a beat to run the call_soon_threadsafe
        # callback that performs the actual enqueue.
        fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.2), loop)
        fut.result(timeout=5)

        assert len(calls) == 1, "queue was not written to exactly once"
        assert calls[0] is loop_thread_box["thread"], (
            "put_nowait did not run on the event-loop thread — it should "
            "have been marshalled there via call_soon_threadsafe instead "
            "of being called directly from the submitting thread"
        )
        assert calls[0] is not submitting_thread
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=5)
        loop.close()


def test_concurrent_post_archive_no_lost_tasks(client):
    """Fire many concurrent POST /archive submissions from separate OS
    threads (as FastAPI's threadpool would for sync handlers) and confirm
    every one returns a valid Task and is retrievable afterwards."""
    n = 40

    def _submit(i):
        r = client.post(
            "/archive",
            json={"url": f"https://www.youtube.com/watch?v=item{i}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("id"), body
        return body["id"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        task_ids = list(pool.map(_submit, range(n)))

    assert len(task_ids) == n
    assert len(set(task_ids)) == n, "duplicate/collided task ids"

    for tid in task_ids:
        r = client.get(f"/tasks/{tid}")
        assert r.status_code == 200, tid
        assert r.json()["id"] == tid
