# SPDX-License-Identifier: Apache-2.0
"""/collections JSON API, /collections/{name}/m3u, and /ui/collections WebUI.

No network -- collections filter a locally-seeded DB.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import collections as coll_mod  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db["a"] = {"source": "youtube", "url": "a", "videoId": "aaaaaaaaaaa",
               "title": "Big Buck Bunny", "author": "Blender Foundation",
               "duration": 596, "stream": "https://x/bbb.mp4"}
    db["c"] = {"source": "bandcamp", "url": "c", "title": "Some Album",
               "artist": "Some Band", "duration": 200, "stream": "https://x/c.mp3"}
    db.store()
    return str(path)


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

def test_post_collections_adds(client, db_path):
    r = client.post("/collections", json={"name": "YT only", "source": "youtube"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "YT only"
    assert body["count"] == 1
    assert coll_mod.list_collections(db_path)[0].name == "YT only"


def test_post_collections_bad_where_400(client):
    r = client.post("/collections", json={"name": "Bad", "where": "def("})
    assert r.status_code == 400


def test_get_collections_lists_with_counts(client):
    client.post("/collections", json={"name": "YT only", "source": "youtube"})
    r = client.get("/collections")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["collections"][0]["count"] == 1


def test_delete_collections_removes(client):
    client.post("/collections", json={"name": "YT only", "source": "youtube"})
    r = client.request("DELETE", "/collections", json={"name": "YT only"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_delete_collections_missing_404(client):
    r = client.request("DELETE", "/collections", json={"name": "nope"})
    assert r.status_code == 404


def test_get_collection_entries(client):
    client.post("/collections", json={"name": "YT only", "source": "youtube"})
    r = client.get("/collections/YT only")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["entries"][0]["title"] == "Big Buck Bunny"


def test_get_collection_entries_missing_404(client):
    r = client.get("/collections/nope")
    assert r.status_code == 404


def test_get_collection_m3u_returns_playlist(client):
    client.post("/collections", json={"name": "YT only", "source": "youtube"})
    r = client.get("/collections/YT only/m3u")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/x-mpegurl")
    assert r.text.startswith("#EXTM3U")
    assert "Big Buck Bunny" in r.text


def test_get_collection_m3u_missing_404(client):
    r = client.get("/collections/nope/m3u")
    assert r.status_code == 404


def test_get_collection_m3u_bad_where_400(client, db_path):
    # A where that passes add's syntax check but fails at eval time --
    # the m3u endpoint must surface a 400, never a raw 500 traceback.
    coll_mod.add_collection(db_path, "Bad field", where="nonexistent_field==1")
    r = client.get("/collections/Bad field/m3u")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# WebUI
# ---------------------------------------------------------------------------

def test_ui_collections_page_renders(client):
    r = client.get("/ui/collections")
    assert r.status_code == 200
    assert "Collections" in r.text


def test_ui_collections_table_empty(client):
    r = client.get("/ui/collections/table")
    assert r.status_code == 200
    assert "No collections" in r.text


def test_ui_collections_post_adds_and_renders_row(client):
    r = client.post("/ui/collections", data={"name": "YT only", "source": "youtube"})
    assert r.status_code == 200
    assert "YT only" in r.text


def test_ui_collections_delete_removes_row(client):
    client.post("/ui/collections", data={"name": "YT only", "source": "youtube"})
    r = client.request("DELETE", "/ui/collections", data={"name": "YT only"})
    assert r.status_code == 200
    assert "No collections" in r.text


def test_ui_collections_preview_renders_entries(client):
    client.post("/ui/collections", data={"name": "YT only", "source": "youtube"})
    r = client.get("/ui/collections/YT only/preview")
    assert r.status_code == 200
    assert "Big Buck Bunny" in r.text
