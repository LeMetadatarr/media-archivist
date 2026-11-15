# SPDX-License-Identifier: Apache-2.0
"""Index steps 200 and 201: the index columns and the titles from the JSON database."""
from __future__ import annotations

import json

from media_archivist.indexdb import IndexDB
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source

UC = "UC" + "a" * 22
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def write_db(tmp_path, entries, subscriptions=()):
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"entries": entries}))
    (tmp_path / "db.subscriptions.json").write_text(json.dumps({"subscriptions": [
        {"url": u, "backend": b} for u, b in subscriptions]}))
    return db


def test_titles_and_pictures_are_copied_and_subscription_kinds_fixed(tmp_path):
    db = write_db(tmp_path, {
        URL: {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ", "title": "A Short Film",
              "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"},
        "https://x.bandcamp.com/track/y": {"source": "bandcamp", "url": "https://x.bandcamp.com/track/y",
                                           "title": "  "},
    }, [("https://www.youtube.com/@SomeChannel", "youtube"),
        (f"https://www.youtube.com/channel/{UC}", "youtube"),
        ("https://archive.org/details/x", "ia")])
    index = IndexDB(db)
    try:
        assert {1, 2, 200, 201} <= {m["version"] for m in index.migrations()}
        row = index.execute("SELECT title, thumbnail FROM entries_meta WHERE entry_id = ?",
                            (stable_id(Source.YOUTUBE, URL),)).fetchone()
        assert tuple(row) == ("A Short Film", "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg")
        assert index.execute("SELECT title FROM entries_meta WHERE key LIKE 'bandcamp:%'"
                             ).fetchone()[0] is None
        subs = {r["id"]: (r["kind"], r["channel_id"]) for r in index.execute(
            "SELECT id, kind, channel_id FROM subscriptions")}
        assert subs == {
            "youtube:url:https://www.youtube.com/@SomeChannel": ("channel", None),
            f"youtube:channel:{UC}": ("channel", UC),
            "ia:url:https://archive.org/details/x": ("other", None),
        }
        cols = {r[1] for r in index.execute("PRAGMA table_info(subscriptions)")}
        assert {"thumbnail", "thumbnail_at", "thumbnail_error", "playlists_at",
                "listing_requested"} <= cols
    finally:
        index.close()


def test_a_known_title_is_kept_and_an_unreadable_database_defers_the_copy(tmp_path):
    db = write_db(tmp_path, {URL: {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ",
                                   "title": "From JSON"}})
    index = IndexDB(db)
    try:
        with index.write() as conn:
            conn.execute("UPDATE entries_meta SET title = 'From a listing'")
            conn.execute("DELETE FROM migrations WHERE version = 201")
        db.write_text("{not json")
        assert index.migrate() == []
        assert 201 not in {m["version"] for m in index.migrations()}
        db.write_text(json.dumps({"entries": {URL: {"source": "youtube", "url": URL,
                                                    "videoId": "dQw4w9WgXcQ", "title": "From JSON"}}}))
        assert index.migrate() == [201]
        assert index.execute("SELECT title FROM entries_meta").fetchone()[0] == "From a listing"
    finally:
        index.close()
