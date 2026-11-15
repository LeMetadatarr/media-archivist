# SPDX-License-Identifier: Apache-2.0
"""/health/streams JSON API, /ui/health page, and the re-resolve routes.

No real network — ``requests`` and ``streams.resolve_stream`` are mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import streams  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db["https://x.bandcamp.com/track/live"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/live",
        "title": "Alive",
        "stream": "https://cdn.example/live.mp4",
    }
    db["https://x.bandcamp.com/track/dead"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/dead",
        "title": "Dead",
        "stream": "https://cdn.example/dead.mp4",
    }
    db.store()
    return str(path)


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    with TestClient(app) as c:
        yield c


def _resp(status_code):
    r = MagicMock()
    r.status_code = status_code
    r.close = MagicMock()
    return r


def _mixed(url, **kwargs):
    return _resp(200) if "live" in url else _resp(500)


def test_health_streams_json_shape(client):
    with patch("requests.head", side_effect=_mixed), \
         patch("requests.get", side_effect=_mixed):
        r = client.get("/health/streams")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["counts"]["ok"] == 1
    assert body["counts"]["dead"] == 1
    statuses = {e["title"]: e["status"] for e in body["entries"]}
    assert statuses["Alive"] == "ok"
    assert statuses["Dead"] == "dead"


def test_ui_health_renders(client):
    r = client.get("/ui/health")
    assert r.status_code == 200
    assert "Stream health" in r.text


def test_ui_health_table_renders(client):
    with patch("requests.head", side_effect=_mixed), \
         patch("requests.get", side_effect=_mixed):
        r = client.get("/ui/health/table")
    assert r.status_code == 200
    assert "Dead" in r.text


def test_reresolve_route_updates_seeded_entry(client, db_path):
    from media_archivist.index import Index

    idx = Index(db_path)
    dead_entry = next(e for e in idx.view() if e.title == "Dead")

    fresh = streams.ResolvedStream(url="https://cdn.example/dead-fresh.mp4")
    with patch.object(streams, "resolve_stream", return_value=fresh):
        r = client.post(f"/entries/{dead_entry.id}/health/reresolve")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["new_stream"] == "https://cdn.example/dead-fresh.mp4"

    idx2 = Index(db_path)
    updated = idx2.get(dead_entry.id)
    assert updated.stream == "https://cdn.example/dead-fresh.mp4"


def test_reresolve_route_404_for_unknown_entry(client):
    r = client.post("/entries/nope/health/reresolve")
    assert r.status_code == 404


def test_health_streams_json_reports_gone_youtube_entry(tmp_path):
    from media_archivist.server.app import create_app

    path = tmp_path / "db2.json"
    db = EnvelopeJsonStorage(str(path))
    db["https://www.youtube.com/watch?v=deleted"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=deleted",
        "videoId": "deleted",
        "title": "Deleted Video",
    }
    db.store()
    app = create_app(str(path))
    with TestClient(app) as c, patch("requests.get", return_value=_resp(404)):
        r = c.get("/health/streams")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["gone"] == 1
    assert body["entries"][0]["status"] == "gone"


def test_remove_gone_entry_route(tmp_path):
    from media_archivist.index import Index
    from media_archivist.server.app import create_app

    path = tmp_path / "db3.json"
    db = EnvelopeJsonStorage(str(path))
    db["https://www.youtube.com/watch?v=deleted"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=deleted",
        "videoId": "deleted",
        "title": "Deleted Video",
    }
    db.store()
    app = create_app(str(path))
    idx = Index(str(path))
    entry = next(iter(idx.view()))
    with TestClient(app) as c:
        r = c.delete(f"/entries/{entry.id}/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert Index(str(path)).get(entry.id) is None


def test_ui_reresolve_fragment_updates_entry(client, db_path):
    from media_archivist.index import Index

    idx = Index(db_path)
    dead_entry = next(e for e in idx.view() if e.title == "Dead")

    fresh = streams.ResolvedStream(url="https://cdn.example/dead-fresh2.mp4")
    with patch.object(streams, "resolve_stream", return_value=fresh):
        r = client.post(f"/ui/health/{dead_entry.id}/reresolve")

    assert r.status_code == 200
    assert "dead-fresh2.mp4" in r.text

    idx2 = Index(db_path)
    assert idx2.get(dead_entry.id).stream == "https://cdn.example/dead-fresh2.mp4"
