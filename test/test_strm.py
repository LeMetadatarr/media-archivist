"""Jellyfin / Kodi ``.strm`` export."""
from __future__ import annotations

from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.strm import _safe, export_strm


def test_safe_keeps_useful_chars():
    assert _safe("Foo Bar") == "Foo Bar"
    assert _safe("Foo / Bar*?") == "Foo _ Bar"
    assert _safe("") == "_unknown"


def test_export_writes_layout(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "stream": "https://x/y.mp3"}
    db["https://www.youtube.com/watch?v=b"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=b",
        "videoId": "b",
        "title": "Demo",
        "author": "Foo Channel",
    }
    db.store()
    out = tmp_path / "library"
    n = export_strm(str(db_path), str(out))
    assert n == 2
    assert (out / "bandcamp" / "Foo" / "Hello.strm").read_text().strip() == \
           "https://x/y.mp3"
    assert "watch?v=b" in (out / "youtube" / "Foo Channel" / "Demo.strm").read_text()


def test_export_uses_base_url(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "T", "artist": "A",
               "stream": "https://x/y.mp3"}
    db.store()
    out = tmp_path / "lib"
    export_strm(str(db_path), str(out), base_url="http://nas.local:8000")
    body = (out / "bandcamp" / "A" / "T.strm").read_text().strip()
    assert body.startswith("http://nas.local:8000/strm/")


def test_export_dry_run(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "T", "artist": "A",
               "stream": "s"}
    db.store()
    out = tmp_path / "lib"
    n = export_strm(str(db_path), str(out), dry_run=True)
    assert n == 1
    assert not list(out.rglob("*.strm"))


def test_export_filters_by_source(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "T", "artist": "A",
               "stream": "s"}
    db["b"] = {"source": "youtube", "url": "b", "videoId": "b", "title": "U"}
    db.store()
    out = tmp_path / "lib"
    n = export_strm(str(db_path), str(out), source="bandcamp")
    assert n == 1
    assert list(out.rglob("*.strm")) == [out / "bandcamp" / "A" / "T.strm"]
