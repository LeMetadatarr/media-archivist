"""HTTP server — exercised via FastAPI's TestClient (no live network)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=a"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=a",
        "videoId": "a",
        "title": "Hello YouTube",
        "duration": 240,
        "tags": ["x"],
    }
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "Hello Bandcamp",
        "artist": "Foo",
        "duration": 200,
        "stream": "https://x.bandcamp.com/stream.mp3",
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def test_get_entries_returns_pydantic_shape(client):
    r = client.get("/entries", params={"limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    titles = sorted(e["title"] for e in body["entries"])
    assert titles == ["Hello Bandcamp", "Hello YouTube"]


def test_source_filter(client):
    r = client.get("/entries", params={"source": "bandcamp"})
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_has_stream_filter(client):
    r = client.get("/entries", params={"has_stream": True})
    assert r.json()["total"] == 1


def test_where_returns_400_on_bad_syntax(client):
    r = client.get("/entries", params={"where": "import os"})
    assert r.status_code == 400
    assert "where" in r.json()["detail"].lower()


def test_get_entry_by_id(client):
    listing = client.get("/entries").json()
    entry_id = listing["entries"][0]["id"]
    r = client.get(f"/entries/{entry_id}")
    assert r.status_code == 200
    assert r.json()["id"] == entry_id


def test_get_entry_404(client):
    r = client.get("/entries/notarealid")
    assert r.status_code == 404


def test_feed_rss(client):
    r = client.get("/feed.rss")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/rss+xml")
    assert "<rss" in r.text and "Hello" in r.text


def test_feed_rss_escapes_ampersands_in_urls(tmp_path):
    """Regression: an unescaped ``&`` in a video URL produced invalid XML.

    YouTube watch URLs routinely carry more than one query parameter (e.g.
    ``...&list=...``); the raw ``&`` broke every RSS/podcast client that
    actually parses the feed as XML instead of eyeballing it.
    """
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=a&list=PL1"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=a&list=PL1",
        "videoId": "a",
        "title": "Rock & Roll",
        "duration": 240,
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.get("/feed.rss")
    assert r.status_code == 200
    assert "v=a&list=PL1" not in r.text, "raw & must not appear in the XML body"
    assert "v=a&amp;list=PL1" in r.text
    assert "Rock &amp; Roll" in r.text

    import xml.etree.ElementTree as ET
    ET.fromstring(r.text)  # raises if the feed isn't well-formed XML


def test_m3u(client):
    r = client.get("/m3u", params={"has_stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/x-mpegurl")
    assert r.text.startswith("#EXTM3U")
    assert "https://x.bandcamp.com/stream.mp3" in r.text


def test_stats(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["source_mix"] == {"youtube": 1, "bandcamp": 1}


def test_strm_returns_stream_url(client):
    listing = client.get("/entries", params={"source": "bandcamp"}).json()
    entry_id = listing["entries"][0]["id"]
    r = client.get(f"/strm/{entry_id}")
    assert r.status_code == 200
    assert r.text.strip() == "https://x.bandcamp.com/stream.mp3"


def test_strm_falls_back_to_watch_url(client):
    listing = client.get("/entries", params={"source": "youtube"}).json()
    entry_id = listing["entries"][0]["id"]
    r = client.get(f"/strm/{entry_id}")
    assert r.status_code == 200
    assert r.text.strip() == "https://www.youtube.com/watch?v=a"


def test_strm_404_for_unknown_id(client):
    r = client.get("/strm/notarealid")
    assert r.status_code == 404


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body and "db_path" in body


def test_providers_lists_registry(client):
    r = client.get("/providers")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    names = {p["name"] for p in body["providers"]}
    assert "musicbrainz" in names or "wikidata" in names
    for p in body["providers"]:
        assert isinstance(p["available"], bool)
        assert isinstance(p["media"], list)
        assert isinstance(p["modality"], list)


def test_canonicalize_unknown_provider_400(client):
    r = client.post("/canonicalize", json={"providers": ["does_not_exist"]})
    assert r.status_code == 400
    assert "unknown provider" in r.json()["detail"]


def test_quarantine_list_empty(client):
    r = client.get("/quarantine")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["entries"] == []


def test_quarantine_accept_unknown_404(client):
    r = client.post("/quarantine/notarealrow/accept")
    assert r.status_code == 404


def test_quarantine_reject_unknown_404(client):
    r = client.post("/quarantine/notarealrow/reject")
    assert r.status_code == 404


def test_post_archive_returns_task(client, monkeypatch):
    """POST /archive returns a queued task; we don't wait for it."""
    r = client.post("/archive", json={"url": "https://www.youtube.com/@x"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"queued", "running", "ok", "error"}
    # Task is fetchable
    r2 = client.get(f"/tasks/{body['id']}")
    assert r2.status_code == 200
