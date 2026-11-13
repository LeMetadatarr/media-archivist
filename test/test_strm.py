"""Jellyfin / Kodi ``.strm`` export."""
from __future__ import annotations

import xml.etree.ElementTree as ET

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


def _seed_two(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "stream": "https://x/y.mp3"}
    db["b"] = {"source": "youtube", "url": "https://www.youtube.com/watch?v=b",
               "videoId": "b", "title": "Demo", "author": "Bar Channel"}
    db.store()
    return db_path


def test_default_layout_is_unchanged_back_compat(tmp_path):
    """No ``layout`` kwarg reproduces the pre-``layout`` output exactly."""
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    n = export_strm(str(db_path), str(out))
    assert n == 2
    assert (out / "bandcamp" / "Foo" / "Hello.strm").exists()
    assert (out / "youtube" / "Bar Channel" / "Demo.strm").exists()


def test_layout_flat_puts_everything_in_output_dir(tmp_path):
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    n = export_strm(str(db_path), str(out), layout="flat")
    assert n == 2
    files = sorted(p.name for p in out.glob("*.strm"))
    assert files == ["Demo.strm", "Hello.strm"]
    assert not any(p.is_dir() for p in out.iterdir())


def test_layout_by_source(tmp_path):
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    export_strm(str(db_path), str(out), layout="by-source")
    assert (out / "bandcamp" / "Hello.strm").exists()
    assert (out / "youtube" / "Demo.strm").exists()


def test_layout_by_artist(tmp_path):
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    export_strm(str(db_path), str(out), layout="by-artist")
    assert (out / "Foo" / "Hello.strm").exists()
    assert (out / "Bar Channel" / "Demo.strm").exists()


def test_layout_rejects_unknown_value(tmp_path):
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    import pytest
    with pytest.raises(ValueError):
        export_strm(str(db_path), str(out), layout="nonsense")


def test_filename_collisions_get_unique_names(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Same Title",
               "artist": "Artist One", "stream": "https://x/1.mp3"}
    db["b"] = {"source": "bandcamp", "url": "b", "title": "Same Title",
               "artist": "Artist Two", "stream": "https://x/2.mp3"}
    db.store()
    out = tmp_path / "library"
    n = export_strm(str(db_path), str(out), layout="flat")
    assert n == 2
    files = sorted(p.name for p in out.glob("*.strm"))
    assert files == ["Same Title-2.strm", "Same Title.strm"]


def test_nfo_sidecar_written_next_to_strm(tmp_path):
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    n = export_strm(str(db_path), str(out), nfo=True)
    assert n == 2
    strm = out / "bandcamp" / "Foo" / "Hello.strm"
    nfo = out / "bandcamp" / "Foo" / "Hello.nfo"
    assert strm.exists()
    assert nfo.exists()
    root = ET.fromstring(nfo.read_text())
    assert root.tag == "musicvideo"
    assert root.findtext("title") == "Hello"
    assert root.findtext("artist") == "Foo"


def test_nfo_false_writes_no_sidecar(tmp_path):
    db_path = _seed_two(tmp_path)
    out = tmp_path / "library"
    export_strm(str(db_path), str(out), nfo=False)
    assert not list(out.rglob("*.nfo"))
