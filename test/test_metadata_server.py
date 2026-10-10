# SPDX-License-Identifier: Apache-2.0
"""Server side of the YouTube metadata fill: on read, as a task, on schedule.

yt-dlp is replaced by the recorded single-video response
(``yt_dlp_video_dQw4w9WgXcQ.json``); the shared limiter is swapped for one
without spacing so each test controls when reads are allowed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import ytmeta  # noqa: E402
from media_archivist.models.canonical import stable_id  # noqa: E402
from media_archivist.models.raw import Source  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_INFO = json.loads((FIXTURES / "yt_dlp_video_dQw4w9WgXcQ.json").read_text())
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
ENTRY_ID = stable_id(Source.YOUTUBE, URL)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db.json")
    db = EnvelopeJsonStorage(path)
    db[URL] = {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ",
               "title": "Never Gonna Give You Up", "published": "DUST",
               "duration": None, "author": None}
    db.store()
    return path


@pytest.fixture
def reads(monkeypatch):
    calls = []

    def extract(video_id, **kw):
        calls.append(video_id)
        return dict(VIDEO_INFO)

    monkeypatch.setattr(ytmeta, "extract_video", extract)
    monkeypatch.setattr(ytmeta, "LIMITER", ytmeta.MetadataLimiter(0.0))
    return calls


def _row(db_path):
    return EnvelopeJsonStorage(db_path)[URL]


def test_get_entry_fills_missing_metadata_once(db_path, reads):
    with TestClient(create_app(db_path)) as c:
        body = c.get(f"/entries/{ENTRY_ID}").json()
        assert (body["duration"], body["artist"], body["published"]) == (213.0, "Rick Astley", "2009-10-25")
        c.get(f"/entries/{ENTRY_ID}")
    assert reads == ["dQw4w9WgXcQ"]
    assert _row(db_path)["metadata_checked"]


def test_get_entry_serves_without_fill_during_back_off(db_path, reads):
    ytmeta.LIMITER.bot_check()
    with TestClient(create_app(db_path)) as c:
        r = c.get(f"/entries/{ENTRY_ID}")
    assert r.status_code == 200
    assert r.json()["published"] is None and r.json()["duration"] is None
    assert reads == []


def test_get_entry_after_bot_check_leaves_fields_empty(db_path, monkeypatch):
    def bot(video_id, **kw):
        raise ytmeta.BotCheckError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(ytmeta, "LIMITER", ytmeta.MetadataLimiter(0.0))
    monkeypatch.setattr(ytmeta, "extract_video", bot)
    with TestClient(create_app(db_path)) as c:
        r = c.get(f"/entries/{ENTRY_ID}")
    assert r.status_code == 200 and r.json()["duration"] is None
    assert ytmeta.LIMITER.blocked
    assert not _row(db_path).get("metadata_checked")


def test_entry_detail_page_fills_metadata(db_path, reads):
    with TestClient(create_app(db_path)) as c:
        r = c.get(f"/ui/entries/{ENTRY_ID}")
    assert r.status_code == 200
    assert reads == ["dQw4w9WgXcQ"]
    assert _row(db_path)["author"] == "Rick Astley"


def _wait_task(c, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = c.get(f"/tasks/{task_id}").json()
        if t["status"] in ("ok", "error"):
            return t
        time.sleep(0.02)
    pytest.fail("task never finished")


def test_enrich_task_fills_and_reports(db_path, reads):
    with TestClient(create_app(db_path)) as c:
        task = c.post("/entries/enrich", json={"limit": 10}).json()
        assert task["request"]["kind"] == "enrich"
        done = _wait_task(c, task["id"])
    assert done["status"] == "ok"
    assert done["rows_updated"] == 2  # one cleaned ("DUST"), one filled
    assert "1 filled" in done["detail"]
    row = _row(db_path)
    assert (row["duration"], row["author"], row["published"]) == (213.0, "Rick Astley", "2009-10-25")


def test_enrich_task_notes_the_bot_check(db_path, monkeypatch):
    def bot(video_id, **kw):
        raise ytmeta.BotCheckError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(ytmeta, "LIMITER", ytmeta.MetadataLimiter(0.0))
    monkeypatch.setattr(ytmeta, "extract_video", bot)
    with TestClient(create_app(db_path)) as c:
        done = _wait_task(c, c.post("/entries/enrich").json()["id"])
    assert done["status"] == "ok"
    assert "bot check" in done["detail"]
    assert _row(db_path)["duration"] is None


def test_enrich_request_is_validated(db_path):
    with TestClient(create_app(db_path)) as c:
        assert c.post("/entries/enrich", json={"limit": 0}).status_code == 422
        assert c.post("/entries/enrich", json={"bogus": 1}).status_code == 422


def test_periodic_queues_enrich_only_when_needed(db_path, reads):
    with TestClient(create_app(db_path)) as c:
        periodic = c.app.state.periodic
        periodic.sync_enabled = False
        periodic.enrich_enabled = True
        first = periodic.run_once()
        assert [t.kind for t in first] == ["enrich"]
        _wait_task(c, first[0].id)
        periodic._last_enrich = None
        assert periodic.run_once() == []  # nothing left to fill


def test_periodic_skips_enrich_during_back_off(db_path, reads):
    ytmeta.LIMITER.bot_check()
    with TestClient(create_app(db_path)) as c:
        periodic = c.app.state.periodic
        periodic.sync_enabled = False
        periodic.enrich_enabled = True
        assert periodic.run_once() == []
