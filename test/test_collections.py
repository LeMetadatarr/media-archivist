# SPDX-License-Identifier: Apache-2.0
"""media_archivist.collections — sidecar store, saved-filter execution, export.

No network — collections never touch a provider; they run Index over a
locally-seeded DB.
"""
from __future__ import annotations

import pytest

from media_archivist import collections as coll_mod
from media_archivist.index import WhereError
from media_archivist.storage import EnvelopeJsonStorage


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db["a"] = {"source": "youtube", "url": "a", "videoId": "aaaaaaaaaaa",
               "title": "Big Buck Bunny", "author": "Blender Foundation",
               "duration": 596, "stream": "https://x/bbb.mp4"}
    db["b"] = {"source": "youtube", "url": "b", "videoId": "bbbbbbbbbbb",
               "title": "Sintel", "author": "Blender Foundation",
               "duration": 888, "stream": "https://x/sintel.mp4"}
    db["c"] = {"source": "bandcamp", "url": "c", "title": "Some Album",
               "artist": "Some Band", "duration": 200, "stream": "https://x/c.mp3"}
    db.store()
    return str(path)


# ---------------------------------------------------------------------------
# sidecar round-trip / dedupe
# ---------------------------------------------------------------------------

def test_add_then_load_round_trips(db_path):
    coll = coll_mod.add_collection(db_path, "Blender open movies",
                                    source="youtube", grep="blend")
    assert coll.name == "Blender open movies"
    loaded = coll_mod.list_collections(db_path)
    assert len(loaded) == 1
    assert loaded[0].source == "youtube"


def test_save_load_sidecar_file_exists(db_path, tmp_path):
    coll_mod.add_collection(db_path, "Foo", where="duration>100")
    sidecar_path = tmp_path / "db.collections.json"
    assert sidecar_path.exists()


def test_add_dedupes_by_name_updates_in_place(db_path):
    coll_mod.add_collection(db_path, "Foo", source="youtube")
    coll_mod.add_collection(db_path, "Foo", source="bandcamp", description="updated")
    colls = coll_mod.list_collections(db_path)
    assert len(colls) == 1
    assert colls[0].source == "bandcamp"
    assert colls[0].description == "updated"


def test_remove_collection(db_path):
    coll_mod.add_collection(db_path, "Foo")
    assert coll_mod.remove_collection(db_path, "Foo") is True
    assert coll_mod.list_collections(db_path) == []


def test_remove_nonexistent_returns_false(db_path):
    assert coll_mod.remove_collection(db_path, "nope") is False


def test_add_empty_name_raises(db_path):
    with pytest.raises(ValueError):
        coll_mod.add_collection(db_path, "  ")


def test_add_bad_where_raises_wheree_error_not_crash(db_path):
    with pytest.raises(WhereError):
        coll_mod.add_collection(db_path, "Bad", where="def not valid python(")


# ---------------------------------------------------------------------------
# collection_entries runs the saved filter
# ---------------------------------------------------------------------------

def test_collection_entries_filters(db_path):
    coll = coll_mod.add_collection(db_path, "Blender", source="youtube", grep="blender")
    entries = coll_mod.collection_entries(db_path, coll)
    # grep matches title, not author -- Blender Foundation authored both but
    # neither title contains "blender", so restrict on source only and check.
    assert entries == []
    coll2 = coll_mod.add_collection(db_path, "YT only", source="youtube")
    entries2 = coll_mod.collection_entries(db_path, coll2)
    assert {e.title for e in entries2} == {"Big Buck Bunny", "Sintel"}


def test_collection_entries_where(db_path):
    coll = coll_mod.add_collection(db_path, "Long", where="duration>500")
    entries = coll_mod.collection_entries(db_path, coll)
    assert {e.title for e in entries} == {"Big Buck Bunny", "Sintel"}


def test_collection_count_matches_entries(db_path):
    coll = coll_mod.add_collection(db_path, "YT", source="youtube")
    assert coll_mod.collection_count(db_path, coll) == 2
    assert len(coll_mod.collection_entries(db_path, coll)) == 2


def test_collection_entries_bad_field_surfaces_where_error(db_path):
    # Passes add_collection's syntax-only check but fails at eval time
    # (unknown field) -- must surface as WhereError, not crash the caller.
    coll = coll_mod.add_collection(db_path, "Bad field", where="nonexistent_field==1")
    with pytest.raises(WhereError):
        coll_mod.collection_entries(db_path, coll)


# ---------------------------------------------------------------------------
# export_collection
# ---------------------------------------------------------------------------

def test_export_collection_writes_strm(db_path, tmp_path):
    coll = coll_mod.add_collection(db_path, "YT", source="youtube")
    out = tmp_path / "lib"
    result = coll_mod.export_collection(db_path, coll, str(out))
    assert result["strm_written"] == 2
    assert result["m3u_path"] is None
    strm_files = list(out.rglob("*.strm"))
    assert len(strm_files) == 2


def test_export_collection_writes_m3u(db_path, tmp_path):
    coll = coll_mod.add_collection(db_path, "YT", source="youtube")
    out = tmp_path / "lib"
    result = coll_mod.export_collection(db_path, coll, str(out), m3u=True, strm=False)
    assert result["strm_written"] == 0
    assert result["m3u_path"] is not None
    body = open(result["m3u_path"]).read()
    assert body.startswith("#EXTM3U")
    # youtube rows resolve their stream via "streams", not raw "stream" --
    # build_m3u falls back to entry.url the same way routes.m3u does.
    assert "\na\n" in body or body.rstrip().endswith("a")
    assert "\nb\n" in body or body.rstrip().endswith("b")
    assert not list(out.rglob("*.strm"))


def test_build_m3u_matches_route_shape(db_path):
    coll = coll_mod.add_collection(db_path, "YT", source="youtube")
    entries = coll_mod.collection_entries(db_path, coll)
    body = coll_mod.build_m3u(entries)
    lines = body.splitlines()
    assert lines[0] == "#EXTM3U"
    assert any(l.startswith("#EXTINF:") for l in lines)
