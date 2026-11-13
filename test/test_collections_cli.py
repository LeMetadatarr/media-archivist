# SPDX-License-Identifier: Apache-2.0
"""``media-archivist collection add/remove/export`` and `collections` CLI.

No network -- collections filter a locally-seeded DB.
"""
from __future__ import annotations

from media_archivist import collections as coll_mod
from media_archivist.cli import main
from media_archivist.storage import EnvelopeJsonStorage


def _seed(path):
    db = EnvelopeJsonStorage(str(path))
    db["a"] = {"source": "youtube", "url": "a", "videoId": "aaaaaaaaaaa",
               "title": "Big Buck Bunny", "duration": 596, "stream": "https://x/a.mp4"}
    db["c"] = {"source": "bandcamp", "url": "c", "title": "Some Album",
               "artist": "Some Band", "duration": 200, "stream": "https://x/c.mp3"}
    db.store()
    return str(path)


def test_collection_add(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["collection-add", "YT only", "--source", "youtube", "--db-file", db_path])
    assert rc == 0
    colls = coll_mod.list_collections(db_path)
    assert len(colls) == 1
    assert colls[0].name == "YT only"
    assert "saved" in capsys.readouterr().err


def test_collection_add_bad_where_errors(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["collection-add", "Bad", "--where", "def(", "--db-file", db_path])
    assert rc == 1
    assert "error" in capsys.readouterr().err


def test_collections_lists_with_counts(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["collection-add", "YT only", "--source", "youtube", "--db-file", db_path])
    rc = main(["collections", "--db-file", db_path])
    assert rc == 0
    out = capsys.readouterr()
    assert "YT only" in out.out
    assert "matches=1" in out.out


def test_collection_remove(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["collection-add", "YT only", "--source", "youtube", "--db-file", db_path])
    rc = main(["collection-remove", "YT only", "--db-file", db_path])
    assert rc == 0
    assert coll_mod.list_collections(db_path) == []


def test_collection_remove_missing_errors(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["collection-remove", "nope", "--db-file", db_path])
    assert rc == 1


def test_collection_export_writes_strm_and_m3u(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["collection-add", "YT only", "--source", "youtube", "--db-file", db_path])
    out_dir = tmp_path / "lib"
    rc = main([
        "collection-export", "YT only", "--db-file", db_path,
        "--output-dir", str(out_dir), "--m3u",
    ])
    assert rc == 0
    assert list(out_dir.rglob("*.strm"))
    assert list(out_dir.glob("*.m3u"))
    err = capsys.readouterr().err
    assert "wrote 1 .strm files" in err
    assert "wrote m3u playlist" in err


def test_collection_export_missing_collection_errors(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["collection-export", "nope", "--db-file", db_path,
               "--output-dir", str(tmp_path / "lib")])
    assert rc == 1
