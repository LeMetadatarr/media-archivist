# SPDX-License-Identifier: Apache-2.0
"""The SQLite working index: migrations, concurrent writers, the JSON import."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from media_archivist import indexdb
from media_archivist import subscriptions as subs_mod
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.storage import EnvelopeJsonStorage

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
URL2 = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
CHANNEL = "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw"


@pytest.fixture
def db_path(tmp_path):
    yield str(tmp_path / "talks.json")
    indexdb.close_all()


def _seed_json(db_path):
    db = EnvelopeJsonStorage(db_path)
    db[URL] = {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ", "title": "a",
               "duration": 213.0, "published": "2009-10-25", "author": "Rick Astley",
               "metadata_checked": "2024-05-01T10:00:00+00:00"}
    db[URL2] = {"source": "youtube", "url": URL2, "videoId": "aaaaaaaaaaa", "title": "b",
                "published": "3 years ago", "duration": 0,
                "metadata_network_failed": "2024-05-02T10:00:00+00:00"}
    db.store()
    subs_mod.add_subscription(db_path, CHANNEL, label="Rick", interval_hours=12)
    sidecar = subs_mod.load_subscriptions(db_path)
    sidecar.subscriptions[0].last_synced_at = "2024-05-03T10:00:00+00:00"
    subs_mod.save_subscriptions(db_path, sidecar)


def test_index_sits_beside_the_json_database(db_path):
    index = indexdb.IndexDB(db_path)
    assert index.path.name == "talks.index.sqlite3"
    assert index.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert index.execute("PRAGMA busy_timeout").fetchone()[0] >= 1000


def test_schema_v1_tables_exist(db_path):
    index = indexdb.IndexDB(db_path)
    names = {r[0] for r in index.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"entries_meta", "subscriptions", "subscription_holders", "memberships",
            "playlists", "playlist_items", "backoff", "settings", "downloads",
            "exports", "migrations"} <= names
    idx = {r[0] for r in index.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"memberships_by_first_seen", "entries_meta_duration"} <= idx
    plan = " ".join(r[-1] for r in index.execute(
        "EXPLAIN QUERY PLAN SELECT entry_id FROM memberships WHERE subscription_id = ?"
        " ORDER BY first_seen DESC", ("x",)))
    assert "memberships_by_first_seen" in plan


def test_migrations_are_recorded_and_idempotent(db_path):
    first = indexdb.IndexDB(db_path)
    assert [m["version"] for m in first.migrations()] == [1, 2]
    assert first.schema_version == 2
    assert first.migrate() == []
    first.close()
    again = indexdb.IndexDB(db_path)
    assert again.migrate() == []
    assert [m["version"] for m in again.migrations()] == [1, 2]


def test_migration_versions_must_be_unique(monkeypatch):
    steps = indexdb.load_migrations()
    assert [s.version for s in steps] == sorted({s.version for s in steps})


def test_a_failing_step_is_rolled_back_and_not_recorded(db_path, monkeypatch):
    def boom(conn, index):
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")

    real = indexdb.load_migrations
    monkeypatch.setattr(indexdb, "load_migrations",
                        lambda: real() + [indexdb.Migration(99, "boom", boom)])
    with pytest.raises(RuntimeError):
        indexdb.IndexDB(db_path)
    index = indexdb.IndexDB(db_path, migrate=False)
    assert [m["version"] for m in index.migrations()] == [1, 2]
    assert not index.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone()


def _hammer(n_threads, per_thread, work):
    errors = []
    start = threading.Barrier(n_threads)

    def run(i):
        try:
            start.wait()
            for j in range(per_thread):
                work(i, j)
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def _increment(index):
    with index.write() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'counter'").fetchone()
        value = json.loads(row["value"]) if row else 0
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('counter', ?, '')"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (json.dumps(value + 1),))


def test_concurrent_increments_on_one_index_lose_nothing(db_path):
    index = indexdb.IndexDB(db_path)
    _hammer(8, 50, lambda i, j: _increment(index))
    assert json.loads(index.execute(
        "SELECT value FROM settings WHERE key = 'counter'").fetchone()[0]) == 400


def test_concurrent_increments_from_separate_connections_lose_nothing(db_path):
    """Separate IndexDB objects stand in for separate processes."""
    indexes = [indexdb.IndexDB(db_path) for _ in range(6)]
    _hammer(6, 40, lambda i, j: _increment(indexes[i]))
    assert json.loads(indexes[0].execute(
        "SELECT value FROM settings WHERE key = 'counter'").fetchone()[0]) == 240


def test_concurrent_membership_inserts_lose_nothing(db_path):
    index = indexdb.IndexDB(db_path)

    def insert(i, j):
        with index.write() as conn:
            conn.execute("INSERT INTO memberships (subscription_id, entry_id, position, first_seen)"
                         " VALUES (?, ?, ?, ?)", (f"sub{i}", f"e{j}", j, f"2024-05-01T00:00:{j:02d}Z"))

    _hammer(8, 30, insert)
    assert index.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 240


def test_nested_write_joins_the_outer_transaction(db_path):
    index = indexdb.IndexDB(db_path)
    with pytest.raises(RuntimeError):
        with index.write() as conn:
            conn.execute("INSERT INTO settings VALUES ('a', '1', '')")
            with index.write() as inner:
                inner.execute("INSERT INTO settings VALUES ('b', '2', '')")
            raise RuntimeError("roll back both")
    assert index.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0


def test_json_import_copies_rows_and_subscriptions(db_path):
    _seed_json(db_path)
    before = Path(db_path).read_text()
    index = indexdb.IndexDB(db_path)
    assert Path(db_path).read_text() == before
    rows = {r["key"]: dict(r) for r in index.execute("SELECT * FROM entries_meta")}
    good = rows["youtube:dQw4w9WgXcQ"]
    assert good["entry_id"] == stable_id(Source.YOUTUBE, URL)
    assert (good["duration"], good["published"], good["channel"]) == (213.0, "2009-10-25", "Rick Astley")
    assert good["metadata_checked"] == "2024-05-01T10:00:00+00:00"
    junk = rows["youtube:aaaaaaaaaaa"]
    assert (junk["duration"], junk["published"], junk["channel"]) == (None, None, None)
    assert junk["network_failed_at"] == "2024-05-02T10:00:00+00:00"
    sub = dict(index.execute("SELECT * FROM subscriptions").fetchone())
    assert sub["id"] == "youtube:channel:UCuAXFkgsw1L7xaCfnd5JJOw"
    assert (sub["url"], sub["kind"], sub["label"], sub["interval_hours"]) == \
        (CHANNEL, "channel", "Rick", 12.0)
    assert sub["last_listing_at"] == "2024-05-03T10:00:00+00:00"
    assert sub["export_automatic"] == 0
    assert [r[0] for r in index.execute("SELECT app FROM subscription_holders")] == ["user"]


def test_json_import_rerun_is_a_no_op(db_path):
    _seed_json(db_path)
    index = indexdb.IndexDB(db_path)
    with index.write() as conn:
        conn.execute("UPDATE entries_meta SET duration = 1 WHERE key = 'youtube:dQw4w9WgXcQ'")
    index.close()
    db = EnvelopeJsonStorage(db_path)
    db["https://www.youtube.com/watch?v=bbbbbbbbbbb"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "videoId": "bbbbbbbbbbb"}
    db.store()
    again = indexdb.IndexDB(db_path)
    assert again.execute("SELECT COUNT(*) FROM entries_meta").fetchone()[0] == 2
    assert again.execute("SELECT duration FROM entries_meta WHERE key = 'youtube:dQw4w9WgXcQ'"
                         ).fetchone()[0] == 1


def test_no_json_database_imports_nothing(db_path):
    index = indexdb.IndexDB(db_path)
    assert index.execute("SELECT COUNT(*) FROM entries_meta").fetchone()[0] == 0
    assert index.schema_version == 2


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw/videos",
     "youtube:channel:UCuAXFkgsw1L7xaCfnd5JJOw"),
    ("https://www.youtube.com/playlist?list=PLabc_123", "youtube:playlist:PLabc_123"),
    ("https://www.youtube.com/watch?v=x&list=PLabc", "youtube:playlist:PLabc"),
    ("https://www.youtube.com/@handle", "youtube:url:https://www.youtube.com/@handle"),
])
def test_subscription_id_for_url(url, expected):
    assert indexdb.subscription_id_for_url(url) == expected


def test_open_index_is_shared_and_closable(db_path):
    a = indexdb.open_index(db_path)
    assert indexdb.open_index(db_path) is a
    indexdb.close_all()
    assert indexdb.open_index(db_path) is not a


def test_concurrent_first_opens_all_succeed(tmp_path):
    """Many connections opening a fresh index at once all get WAL and migrate."""
    failures = []
    for r in range(80):
        db = tmp_path / f"round{r}" / "t.json"
        db.parent.mkdir()
        start = threading.Barrier(10)
        errors = []

        def run(db=db, start=start, errors=errors):
            start.wait()
            try:
                indexdb.IndexDB(db).close()
            except Exception as e:  # pragma: no cover - reported below
                errors.append(repr(e))

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            failures.append((r, errors[0]))
    assert not failures, failures


@pytest.mark.parametrize("name,expected", [
    ("talks.json", "talks.index.sqlite3"),
    ("talks.v1", "talks.v1.index.sqlite3"),
    ("talks", "talks.index.sqlite3"),
])
def test_index_path_keeps_names_apart(tmp_path, name, expected):
    assert indexdb.index_path(tmp_path / name).name == expected


def test_index_paths_of_different_databases_differ(tmp_path):
    assert indexdb.index_path(tmp_path / "talks.v1") != indexdb.index_path(tmp_path / "talks.v2")


def test_caught_failure_in_a_nested_write_is_rolled_back(db_path):
    index = indexdb.IndexDB(db_path)
    with index.write() as conn:
        conn.execute("INSERT INTO settings VALUES ('outer', '1', '')")
        with pytest.raises(ValueError):
            with index.write() as inner:
                inner.execute("INSERT INTO settings VALUES ('inner', '1', '')")
                raise ValueError("inner failed")
        conn.execute("INSERT INTO settings VALUES ('after', '1', '')")
    keys = sorted(r[0] for r in index.execute("SELECT key FROM settings"))
    assert keys == ["after", "outer"]
    assert not index.conn.in_transaction


def _seed_raw(db_path, patch):
    row = {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ", "duration": 12}
    row.update(patch)
    Path(db_path).write_text(json.dumps({"_meta": {"schema_version": 2}, "entries": {URL: row}}))


@pytest.mark.parametrize("patch", [
    {"metadata_checked": {"x": 1}},
    {"metadata_network_failed": ["y"]},
    {"duration": {"a": 1}, "published": 20200101, "author": ["a"]},
    {"videoId": ["x"]},
    {"source": ["youtube"]},
])
def test_json_import_survives_odd_values(db_path, patch):
    _seed_raw(db_path, patch)
    index = indexdb.IndexDB(db_path)
    assert index.schema_version == 2
    for row in index.execute("SELECT * FROM entries_meta"):
        for column in ("key", "metadata_checked", "network_failed_at", "published", "channel"):
            assert row[column] is None or isinstance(row[column], str)


@pytest.mark.parametrize("text", ['{"_meta": {}, "entries": {"a": {"source": "you', "garbage", "[1, 2, 3]"])
def test_unreadable_json_leaves_the_import_to_retry(db_path, text):
    Path(db_path).write_text(text)
    index = indexdb.IndexDB(db_path)
    assert 1 in [m["version"] for m in index.migrations()]
    assert 2 not in [m["version"] for m in index.migrations()]
    index.close()
    Path(db_path).unlink()
    _seed_json(db_path)
    again = indexdb.IndexDB(db_path)
    assert 2 in [m["version"] for m in again.migrations()]
    assert again.execute("SELECT COUNT(*) FROM entries_meta").fetchone()[0] == 2


def test_channel_id_with_extra_characters_is_not_truncated():
    url = "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOwX"
    assert indexdb.subscription_id_for_url(url) == f"youtube:url:{url}"


def test_subscriptions_merged_by_id_are_logged(db_path, caplog):
    subs_mod.add_subscription(db_path, CHANNEL)
    subs_mod.add_subscription(db_path, CHANNEL + "/videos")
    with caplog.at_level("WARNING", logger="media_archivist.indexdb"):
        index = indexdb.IndexDB(db_path)
    assert index.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1
    assert any(CHANNEL + "/videos" in r.getMessage() for r in caplog.records)


def test_json_that_is_not_utf8_leaves_the_import_to_retry(db_path):
    Path(db_path).write_bytes(b'{"_meta": {}, "entries": {"\xff\xfe": {}}}')
    index = indexdb.IndexDB(db_path)
    assert [m["version"] for m in index.migrations()] == [1]


def test_json_that_cannot_be_opened_leaves_the_import_to_retry(db_path, monkeypatch):
    _seed_json(db_path)
    real = Path.read_text

    def denied(self, *a, **kw):
        if self == Path(db_path):
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", denied)
    index = indexdb.IndexDB(db_path)
    assert [m["version"] for m in index.migrations()] == [1]
    index.close()
    monkeypatch.setattr(Path, "read_text", real)
    assert indexdb.IndexDB(db_path).execute("SELECT COUNT(*) FROM entries_meta").fetchone()[0] == 2


def test_corrupt_subscriptions_sidecar_leaves_the_import_to_retry(db_path):
    _seed_json(db_path)
    sidecar = Path(db_path).with_suffix(".subscriptions.json")
    good = sidecar.read_text()
    sidecar.write_text('{"subscriptions": [ {"url": ')
    index = indexdb.IndexDB(db_path)
    assert [m["version"] for m in index.migrations()] == [1]
    assert index.execute("SELECT COUNT(*) FROM entries_meta").fetchone()[0] == 0
    index.close()
    sidecar.write_text(good)
    again = indexdb.IndexDB(db_path)
    assert [m["version"] for m in again.migrations()] == [1, 2]
    assert again.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1


def test_a_deferred_step_writes_nothing_and_holds_back_later_steps(db_path, monkeypatch):
    def not_ready(conn, index):
        conn.execute("INSERT INTO settings VALUES ('by_deferred_step', '1', '')")
        return False

    def later(conn, index):
        conn.execute("INSERT INTO settings VALUES ('by_later_step', '1', '')")

    real = indexdb.load_migrations
    monkeypatch.setattr(indexdb, "load_migrations", lambda: real() + [
        indexdb.Migration(50, "not ready", not_ready), indexdb.Migration(60, "later", later)])
    for _ in range(2):
        index = indexdb.IndexDB(db_path)
        assert [m["version"] for m in index.migrations()] == [1, 2]
        assert index.schema_version == 2
        assert index.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
        index.close()


def test_a_conflict_that_ends_the_transaction_surfaces_from_a_nested_write(db_path):
    import sqlite3

    index = indexdb.IndexDB(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        with index.write() as conn:
            conn.execute("INSERT INTO settings VALUES ('a', '1', '')")
            with index.write() as inner:
                inner.execute("INSERT OR ROLLBACK INTO settings VALUES ('a', '2', '')")
    assert not index.conn.in_transaction
    assert index.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    with index.write() as conn:
        conn.execute("INSERT INTO settings VALUES ('b', '1', '')")
    assert index.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1
