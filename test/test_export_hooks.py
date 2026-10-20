# SPDX-License-Identifier: Apache-2.0
"""Automatic export of newly indexed entries for subscriptions that ask for it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_archivist import export_hooks, indexdb, links, strm
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.storage import EnvelopeJsonStorage

SUB = "youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx"


def watch(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def eid(vid):
    return stable_id(Source.YOUTUBE, watch(vid))


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in (links.ENV_SECRET, "MEDIA_ARCHIVIST_JELLYFIN_URL", "MEDIA_ARCHIVIST_JELLYFIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(links.ENV_PUBLIC_URL, "http://archivist.lan:8000")
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv(strm.ENV_LIBRARY_ROOTS, str(library))
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    for vid in ("vidAAAAAAAA", "vidBBBBBBBB"):
        db[watch(vid)] = {"source": "youtube", "url": watch(vid), "videoId": vid, "title": vid,
                          "author": "Some Channel", "published": "2024-05-03"}
    db.store()
    index = indexdb.open_index(str(path))
    with index.write() as conn:
        conn.execute("INSERT INTO subscriptions (id, url, kind, created_at, export_automatic, export_target)"
                     " VALUES (?, ?, 'channel', '2024-01-01T00:00:00Z', 1, ?)",
                     (SUB, "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
                      str(library / "auto")))
    yield {"db": str(path), "index": index, "library": library, "tmp": tmp_path}
    indexdb.close_all()


def test_new_entries_are_exported_into_one_automatic_record(env):
    first = export_hooks.on_entries_added(SUB, [eid("vidAAAAAAAA")], db_path=env["db"])
    folder = env["library"] / "auto" / "vidAAAAAAAA (2024) [vidAAAAAAAA]"
    assert (folder / "vidAAAAAAAA (2024) [vidAAAAAAAA].strm").read_text() == \
        f"http://archivist.lan:8000/api/v1/stream/{eid('vidAAAAAAAA')}\n"
    second = export_hooks.on_entries_added(SUB, [eid("vidBBBBBBBB")], db_path=env["db"])
    assert second["id"] == first["id"]
    rows = env["index"].execute("SELECT * FROM exports").fetchall()
    assert len(rows) == 1 and rows[0]["automatic"] == 1
    assert rows[0]["scope_kind"] == "subscription" and rows[0]["scope_id"] == SUB
    assert len(json.loads(rows[0]["written"])) == 4


def test_the_open_index_is_found_without_a_path(env):
    assert export_hooks.on_entries_added(SUB, [eid("vidAAAAAAAA")]) is not None


def test_nothing_happens_unless_the_subscription_asks(env):
    with env["index"].write() as conn:
        conn.execute("UPDATE subscriptions SET export_automatic = 0")
    assert export_hooks.on_entries_added(SUB, [eid("vidAAAAAAAA")], db_path=env["db"]) is None
    assert export_hooks.on_entries_added("youtube:channel:UCnope", [eid("vidAAAAAAAA")],
                                         db_path=env["db"]) is None
    assert not (env["library"] / "auto").exists()


def test_a_target_outside_the_roots_is_never_written(env):
    outside = env["tmp"] / "outside"
    with env["index"].write() as conn:
        conn.execute("UPDATE subscriptions SET export_target = ?", (str(outside),))
    assert export_hooks.on_entries_added(SUB, [eid("vidAAAAAAAA")], db_path=env["db"]) is None
    assert not outside.exists()


def test_signed_links_when_the_subscription_asks(env):
    with env["index"].write() as conn:
        conn.execute("UPDATE subscriptions SET export_links = 'signed', export_layout = 'episodes'")
    rec = export_hooks.on_entries_added(SUB, [eid("vidAAAAAAAA")], db_path=env["db"])
    [strm_path] = [p for p in rec["written"] if p.endswith(".strm")]
    assert "/Season 2024/" in strm_path
    assert "?sig=" in Path(strm_path).read_text()


def test_no_public_url_means_no_automatic_export(env, monkeypatch):
    monkeypatch.delenv(links.ENV_PUBLIC_URL)
    assert export_hooks.on_entries_added(SUB, [eid("vidAAAAAAAA")], db_path=env["db"]) is None
    assert not (env["library"] / "auto").exists()
