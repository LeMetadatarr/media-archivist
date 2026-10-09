# SPDX-License-Identifier: Apache-2.0
"""GET /tasks: scheduler task counts per status."""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

import media_archivist.youtube as youtube_mod  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402

EMPTY = {"queued": 0, "running": 0, "ok": 0, "error": 0, "total": 0}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "db.json")


def _wait(client, pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/tasks").json()
        if pred(body):
            return body
        time.sleep(0.02)
    pytest.fail(f"condition never met (last={body})")


def test_no_tasks_all_zero(db_path):
    with TestClient(create_app(db_path)) as c:
        r = c.get("/tasks")
    assert r.status_code == 200
    assert r.json() == EMPTY


def test_counts_follow_task_lifecycle(db_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def _archive(self, url):
        if "fail" in url:
            raise RuntimeError("boom")
        started.set()
        release.wait(5)

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", _archive)

    with TestClient(create_app(db_path)) as c:
        c.post("/archive", json={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"})
        assert started.wait(5)
        c.post("/archive", json={"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"})
        c.post("/archive", json={"url": "https://www.youtube.com/watch?v=fail0000000"})

        body = _wait(c, lambda b: b["running"] == 1 and b["queued"] == 2)
        assert body == {**EMPTY, "running": 1, "queued": 2, "total": 3}

        release.set()
        body = _wait(c, lambda b: b["ok"] + b["error"] == 3)
        assert body == {**EMPTY, "ok": 2, "error": 1, "total": 3}


def test_counts_include_tasks_from_previous_run(db_path, monkeypatch):
    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", lambda self, url: None)
    with TestClient(create_app(db_path)) as c:
        c.post("/archive", json={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"})
        _wait(c, lambda b: b["ok"] == 1)
    with TestClient(create_app(db_path)) as c:
        assert c.get("/tasks").json()["total"] == 1


def test_tasks_listing_does_not_shadow_task_lookup(db_path):
    with TestClient(create_app(db_path)) as c:
        assert c.get("/tasks/nope").status_code == 404
