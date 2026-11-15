# SPDX-License-Identifier: Apache-2.0
"""Per-kind back-off and settings, persisted in the working index."""
from __future__ import annotations

import pytest

from media_archivist import backoff, indexdb, settings, ytmeta


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def db_path(tmp_path):
    yield str(tmp_path / "db.json")
    indexdb.close_all()


def test_backoff_doubles_from_30_minutes_to_a_day(db_path):
    clock = Clock()
    b = backoff.Backoff(indexdb.IndexDB(db_path), clock=clock)
    waits = []
    for _ in range(8):
        waits.append(b.mark_blocked("details"))
        clock.now += waits[-1]
    assert waits == [1800, 3600, 7200, 14400, 28800, 57600, 86400, 86400]


def test_a_check_during_a_pause_keeps_it(db_path):
    clock = Clock()
    b = backoff.Backoff(indexdb.IndexDB(db_path), clock=clock)
    assert b.mark_blocked("listing") == 1800
    clock.now += 600
    assert b.mark_blocked("listing") == 1200
    assert b.blocked_until("listing") == clock.now + 1200


def test_kinds_pause_independently_and_clear_resets(db_path):
    clock = Clock()
    b = backoff.Backoff(indexdb.IndexDB(db_path), clock=clock)
    b.mark_blocked("details")
    assert b.blocked_until("listing") is None
    state = b.blocked()
    assert state["details"]["paused"] and state["details"]["wait"] == 1800
    assert set(state) == set(backoff.KINDS)
    assert not state["feed"]["paused"]
    b.mark_clear("details")
    assert b.blocked_until("details") is None
    assert b.mark_blocked("details") == 1800


def test_unknown_kind_is_refused(db_path):
    b = backoff.Backoff(indexdb.IndexDB(db_path))
    with pytest.raises(ValueError):
        b.mark_blocked("comments")


def test_backoff_survives_a_restart(db_path):
    clock = Clock()
    first = backoff.Backoff(indexdb.IndexDB(db_path), clock=clock)
    first.mark_blocked("feed")
    clock.now += first.mark_blocked("feed") + 1
    assert first.mark_blocked("feed") == 3600
    first.index.close()
    second = backoff.Backoff(indexdb.IndexDB(db_path), clock=clock)
    assert second.remaining("feed") == 3600
    clock.now += 3601
    assert second.mark_blocked("feed") == 7200


def test_settings_defaults_set_and_restart(db_path):
    s = settings.Settings(indexdb.IndexDB(db_path))
    assert s.get("youtube_ip_family") == "ipv4"
    assert s.all()["youtube_request_spacing"] == 10
    s.set("youtube_ip_family", "ipv6")
    s.index.close()
    again = settings.Settings(indexdb.IndexDB(db_path))
    assert again.get("youtube_ip_family") == "ipv6"
    again.reset("youtube_ip_family")
    assert again.get("youtube_ip_family") == "ipv4"


def test_settings_refuse_bad_values_and_store_nothing(db_path):
    s = settings.Settings(indexdb.IndexDB(db_path))
    with pytest.raises(ValueError):
        s.update({"youtube_request_spacing": 5, "youtube_ip_family": "ipv5"})
    assert s.get("youtube_request_spacing") == 10
    with pytest.raises(KeyError):
        s.set("nope", 1)
    with pytest.raises(ValueError):
        s.set("youtube_request_spacing", -1)


def test_metadata_pause_survives_a_restart(db_path):
    first = ytmeta.MetadataLimiter(0.0)
    first.persist_to(db_path)
    length = first.bot_check()
    assert length == backoff.INITIAL_WAIT_S
    indexdb.close_all()
    second = ytmeta.MetadataLimiter(0.0)
    assert not second.blocked
    second.persist_to(db_path)
    assert second.blocked
    assert not second.try_acquire() and not second.acquire()
    assert backoff.for_db(db_path).blocked_until("details") is not None


def test_metadata_limiter_and_details_backoff_are_one(db_path):
    lim = ytmeta.MetadataLimiter(0.0)
    lim.persist_to(db_path)
    backoff.for_db(db_path).mark_blocked("details")
    assert lim.blocked
    backoff.for_db(db_path).mark_clear("details")
    assert not lim.blocked
    assert lim.try_acquire()
    lim.release()


def test_persist_keeps_a_pause_taken_in_memory(db_path):
    lim = ytmeta.MetadataLimiter(0.0)
    lim.bot_check()
    lim.persist_to(db_path)
    assert lim.blocked
    assert backoff.for_db(db_path).remaining("details") > 0


def test_reading_a_pause_never_waits_for_a_writer(db_path, monkeypatch):
    import sqlite3
    import time

    monkeypatch.setattr(indexdb, "BUSY_TIMEOUT_S", 2.0)
    index = indexdb.IndexDB(db_path)
    lim = ytmeta.MetadataLimiter(0.0)
    lim.persist_to(db_path)
    holder = sqlite3.connect(str(index.path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        assert not lim.blocked
        assert lim.try_acquire()
        lim.release()
        b = backoff.Backoff(index)
        assert b.blocked_until("listing") is None
        assert not b.blocked()["feed"]["paused"]
        assert time.monotonic() - started < 1.0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
