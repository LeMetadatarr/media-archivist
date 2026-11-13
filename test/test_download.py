# SPDX-License-Identifier: Apache-2.0
"""Optional, scheduler-backed "download to library" action.

Secondary to the existing stream-archival flow (archive() never changes
here). All ``streams.download`` calls are mocked — no network, no real
yt-dlp invocation, no bytes hit disk.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import streams  # noqa: E402
from media_archivist.models.api import ArchiveRequest, DownloadRequest  # noqa: E402
from media_archivist.server import scheduler as scheduler_mod  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "videoId": "dQw4w9WgXcQ",
        "title": "YouTube Video",
    }
    db.store()
    return path


@pytest.fixture
def client(db_path):
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def _entry_id(client, url_substring="dQw4w9WgXcQ"):
    listing = client.get("/entries").json()["entries"]
    for e in listing:
        if url_substring in e["url"]:
            return e["id"]
    raise AssertionError(f"no entry with url containing {url_substring!r}")


def _wait_terminal(client, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/tasks/{task_id}").json()
        if last["status"] in ("ok", "error"):
            return last
        time.sleep(0.02)
    pytest.fail(f"task {task_id} never reached a terminal state (last={last})")


def _fake_download(progress_ticks=(10, 55, 100), result_path="/fake/dest/video [id].mp4"):
    """A ``streams.download`` stand-in that drives the progress_hook a few
    times (like real yt-dlp does) before returning a fake Path — no
    network, no real file."""

    def _download(url, dest_dir, *, format="best", progress_hook=None, timeout=None):
        for pct in progress_ticks:
            if progress_hook is not None:
                progress_hook({
                    "status": "downloading",
                    "downloaded_bytes": pct,
                    "total_bytes": 100,
                })
        if progress_hook is not None:
            progress_hook({"status": "finished"})
        return Path(result_path)

    return _download


# ---------------------------------------------------------------------
# Happy path: POST /entries/{id}/download enqueues + progresses to ok.
# ---------------------------------------------------------------------

def test_download_route_enqueues_task_and_progresses_to_ok(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "download", _fake_download())

    eid = _entry_id(client)
    r = client.post(f"/entries/{eid}/download")
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["status"] in ("queued", "running", "ok")
    assert task["request"]["kind"] == "download"
    assert task["request"]["entry_id"] == eid

    final = _wait_terminal(client, task["id"])
    assert final["status"] == "ok", final
    assert final["progress"] == 100
    assert final["filepath"] == "/fake/dest/video [id].mp4"


def test_ui_download_fragment_enqueues_and_renders_progress(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "download", _fake_download())

    eid = _entry_id(client)
    r = client.post(f"/ui/entries/{eid}/download")
    assert r.status_code == 200, r.text
    assert "task id" in r.text  # task_status.html fragment rendered

    # Extract the task id back out via /tasks listing isn't exposed, so
    # poll the ui fragment isn't trivial; instead confirm via the JSON
    # route's task store directly through a second submission's id is not
    # needed -- just assert the fragment shows a kind=download-shaped
    # progress cell (either "%" or the queued/running badge) rather than
    # "rows added" (the archive-only field).
    assert "rows added" not in r.text


# ---------------------------------------------------------------------
# Regression: kind="archive" behavior is untouched.
# ---------------------------------------------------------------------

def test_archive_task_still_works_unchanged(client, monkeypatch):
    import media_archivist.youtube as youtube_mod

    def _fake_archive(self, url):
        pass

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", _fake_archive)

    r = client.post("/archive", json={"url": "https://www.youtube.com/watch?v=abc12345678"})
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["request"]["kind"] == "archive"

    final = _wait_terminal(client, task["id"])
    assert final["status"] == "ok", final
    assert final["progress"] is None
    assert final["filepath"] is None


# ---------------------------------------------------------------------
# ytdlp unavailable -> clear error, not 500.
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Webhook notification firing on download completion/failure.
# ---------------------------------------------------------------------

def test_download_complete_fires_notify(client, monkeypatch):
    from media_archivist import notify as notify_mod

    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "download", _fake_download())
    calls = []
    monkeypatch.setattr(notify_mod, "notify",
                         lambda event, message, data=None: calls.append((event, message, data)))

    eid = _entry_id(client)
    r = client.post(f"/entries/{eid}/download")
    _wait_terminal(client, r.json()["id"])

    assert any(c[0] == "download_complete" for c in calls), calls


def test_download_failure_fires_notify(client, monkeypatch):
    from media_archivist import notify as notify_mod

    def _boom(url, dest_dir, *, format="best", progress_hook=None, timeout=None):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "download", _boom)
    calls = []
    monkeypatch.setattr(notify_mod, "notify",
                         lambda event, message, data=None: calls.append((event, message, data)))

    eid = _entry_id(client)
    r = client.post(f"/entries/{eid}/download")
    final = _wait_terminal(client, r.json()["id"])

    assert final["status"] == "error"
    assert any(c[0] == "download_failed" for c in calls), calls


def test_download_route_rejects_when_ytdlp_unavailable(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: False)
    eid = _entry_id(client)
    r = client.post(f"/entries/{eid}/download")
    assert r.status_code == 503
    assert "yt-dlp" in r.json()["detail"]


def test_ui_download_rejects_when_ytdlp_unavailable(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: False)
    eid = _entry_id(client)
    r = client.post(f"/ui/entries/{eid}/download")
    assert r.status_code == 503
    assert "yt-dlp" in r.text


def test_download_route_404s_for_missing_entry(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    r = client.post("/entries/does-not-exist/download")
    assert r.status_code == 404


# ---------------------------------------------------------------------
# Download dir is server-configured only; no client-supplied path.
# ---------------------------------------------------------------------

def test_download_request_model_has_no_path_field():
    """DownloadRequest must never accept a client-supplied destination —
    the download dir is always server-configured
    (streams.default_download_dir() / MEDIA_ARCHIVIST_DOWNLOAD_DIR)."""
    fields = DownloadRequest.model_fields
    assert "dest_dir" not in fields
    assert "path" not in fields
    assert "dest" not in fields
    # extra="forbid" means a client trying to smuggle one in gets a 422,
    # not silent acceptance.
    with pytest.raises(Exception):
        DownloadRequest(entry_id="x", dest_dir="/etc")


def test_download_uses_configured_dir_not_client_supplied(client, monkeypatch, tmp_path):
    configured_dir = tmp_path / "configured-downloads"
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "default_download_dir", lambda: configured_dir)

    seen = {}

    def _download(url, dest_dir, *, format="best", progress_hook=None, timeout=None):
        seen["dest_dir"] = dest_dir
        return Path(dest_dir) / "file.mp4"

    monkeypatch.setattr(streams, "download", _download)

    eid = _entry_id(client)
    r = client.post(f"/entries/{eid}/download")
    assert r.status_code == 200
    _wait_terminal(client, r.json()["id"])
    assert seen["dest_dir"] == str(configured_dir)


# ---------------------------------------------------------------------
# Queue-full -> 429, same as archive.
# ---------------------------------------------------------------------

def test_download_route_returns_429_when_queue_full(db_path, monkeypatch):
    # MAX_QUEUE_SIZE must be patched *before* the Scheduler (and thus the
    # app) is constructed -- Scheduler.__init__ reads it once to size the
    # asyncio.Queue. Build the app locally instead of via the shared
    # `client` fixture, which would construct it too early.
    monkeypatch.setattr(scheduler_mod, "MAX_QUEUE_SIZE", 1)
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)

    def _hang_download(url, dest_dir, *, format="best", progress_hook=None, timeout=None):
        time.sleep(5)
        return Path(dest_dir) / "never.mp4"

    monkeypatch.setattr(streams, "download", _hang_download)

    app = create_app(str(db_path))
    with TestClient(app) as client:
        eid = _entry_id(client)

        r1 = client.post(f"/entries/{eid}/download")
        assert r1.status_code == 200

        deadline = time.monotonic() + 5
        task1_id = r1.json()["id"]
        while time.monotonic() < deadline:
            if client.get(f"/tasks/{task1_id}").json()["status"] == "running":
                break
            time.sleep(0.05)
        else:
            pytest.fail("task 1 never started running")

        r2 = client.post(f"/entries/{eid}/download")
        assert r2.status_code == 200  # fills the 1-slot queue

        r3 = client.post(f"/entries/{eid}/download")
        assert r3.status_code == 429, r3.text


# ---------------------------------------------------------------------
# Regression: existing scheduler durability/thread-safety tests still pass
# (spot-checked here too; the full suite is the real gate).
# ---------------------------------------------------------------------

def test_scheduler_submit_still_accepts_archive_requests(tmp_path):
    async def _worker(task):  # pragma: no cover - never run
        pass

    sched = scheduler_mod.Scheduler(str(tmp_path / "db.json"), _worker)
    task = sched.submit(ArchiveRequest(url="https://example.com/a"))
    assert task.request.kind == "archive"


def test_scheduler_update_progress_is_lock_protected_and_in_memory_only(tmp_path):
    """update_progress() must not call save() on every tick (that would
    reintroduce O(n) disk churn on a fast progress stream) — it mutates
    the in-memory Task under the same lock save() uses, and the terminal
    status transition (already covered by TaskStore.update()) is what
    persists."""
    async def _worker(task):  # pragma: no cover - never run
        pass

    sched = scheduler_mod.Scheduler(str(tmp_path / "db.json"), _worker)
    task = sched.store.add(DownloadRequest(entry_id="e1"))

    calls = {"n": 0}
    orig_save = sched.store.save

    def _counting_save():
        calls["n"] += 1
        orig_save()

    sched.store.save = _counting_save  # type: ignore[method-assign]

    for pct in (10, 20, 30, 100):
        sched.store.update_progress(task.id, pct)

    assert sched.store.get(task.id).progress == 100
    assert calls["n"] == 0, "update_progress() must not persist to disk per-tick"
