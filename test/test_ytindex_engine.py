# SPDX-License-Identifier: Apache-2.0
"""The indexer: what is due, what each job writes, failures and pauses."""
from __future__ import annotations

import json

import pytest

from media_archivist import subscriptions as subs
from media_archivist import ytgate
from media_archivist.indexdb import IndexDB
from media_archivist.ytindex import engine, store
from media_archivist.ytindex.client import VideoItem
from media_archivist.ytindex.engine import Indexer
from media_archivist.ytindex.store import entry_key
from ytindex_fakes import HANDLE_URL, UC, UC2, Clock, FakeGate, FakeYouTube, playlist, vid, videos


@pytest.fixture
def index(tmp_path):
    db = IndexDB(tmp_path / "db.json")
    yield db
    db.close()


@pytest.fixture
def yt():
    return FakeYouTube()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def gate():
    return FakeGate()


def make(index, yt, gate, clock, **kw):
    return Indexer(index, client=yt, gate=gate, clock=clock, **kw)


def members(index, sid):
    return [r[0] for r in index.execute(
        "SELECT e.key FROM memberships m JOIN entries_meta e ON e.entry_id = m.entry_id"
        " WHERE m.subscription_id = ? ORDER BY m.position", (sid,))]


def sub_row(index, sid):
    return index.execute("SELECT * FROM subscriptions WHERE id = ?", (sid,)).fetchone()


def test_a_handle_is_listed_in_full_and_moved_to_its_channel_id(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 3), handle=HANDLE_URL)
    sub, created = subs.create_indexed(index, HANDLE_URL, app="shortarr")
    assert created and sub["id"] == f"youtube:url:{HANDLE_URL}"
    ix = make(index, yt, gate, clock)
    assert ix.due() == [(sub["id"], "listing")]
    assert ix.run_due() == {"listing:ok": 1}
    sid = f"youtube:channel:{UC}"
    assert sub_row(index, sub["id"]) is None
    row = sub_row(index, sid)
    assert row["channel_id"] == UC and row["title"] == "Some Channel"
    assert row["first_indexed_at"] == row["last_listing_at"] == "2024-05-01T12:00:00+00:00"
    assert row["thumbnail"] == "https://yt3.example/c.jpg" and not row["listing_requested"]
    assert members(index, sid) == [entry_key(vid(n)) for n in (1, 2, 3)]
    holders = [r[0] for r in index.execute(
        "SELECT app FROM subscription_holders WHERE subscription_id = ?", (sid,))]
    assert holders == ["shortarr"]
    assert gate.kinds == ["listing"]


def test_after_the_listing_the_feed_runs_every_two_hours_and_the_listing_weekly(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 2))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    jobs = {job for s, job in ix.due() if s == sid}
    assert jobs == {"feed", "playlists"}
    ix.run(sid, "feed")
    ix.run(sid, "playlists")
    assert ix.due() == []
    clock.advance(hours=1, minutes=59)
    assert ix.due() == []
    clock.advance(minutes=1)
    assert ix.due() == [(sid, "feed")]
    clock.advance(days=7)
    assert (sid, "listing") in ix.due()


def test_the_feed_adds_new_uploads_with_their_date_and_marks_shorts(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 2))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    yt.feeds[UC] = [VideoItem(vid(9), "Brand new", published="2024-05-02", vertical=True),
                    VideoItem(vid(1), "Video 1 renamed", published="2024-04-01")]
    clock.advance(hours=3)
    assert ix.run(sid, "feed") == "ok"
    new = index.execute("SELECT * FROM entries_meta WHERE key = ?", (entry_key(vid(9)),)).fetchone()
    assert new["published"] == "2024-05-02" and new["vertical"] == 1
    assert new["first_seen"] == "2024-05-01T15:00:00+00:00" and new["channel_id"] == UC
    old = index.execute("SELECT * FROM entries_meta WHERE key = ?", (entry_key(vid(1)),)).fetchone()
    assert old["title"] == "Video 1 renamed" and old["duration"] == 600.0
    assert old["first_seen"] == "2024-05-01T12:00:00+00:00"
    assert set(members(index, sid)) == {entry_key(vid(n)) for n in (1, 2, 9)}
    assert sub_row(index, sid)["last_feed_at"] == "2024-05-01T15:00:00+00:00"


def test_a_value_once_known_is_never_replaced_by_a_listing_that_lacks_it(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1, duration=754.0))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    yt.channels[UC].videos = [VideoItem(vid(1), None, None)]
    ix.refresh(sid)
    row = index.execute("SELECT title, duration FROM entries_meta").fetchone()
    assert (row["title"], row["duration"]) == ("Video 1", 754.0)


def test_three_failures_in_a_row_flag_and_a_bot_check_does_not_count(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    yt.fail["read_channel"] = RuntimeError("Sign in to confirm you're not a bot")
    for _ in range(5):
        assert ix.run(sid, "listing") == "bot-check"
    row = sub_row(index, sid)
    assert row["failures"] == 0 and not row["flagged"] and "bot check" in row["last_error"]
    yt.fail["read_channel"] = ConnectionError("connection reset")
    assert [ix.run(sid, "listing") for _ in range(3)] == ["failed"] * 3
    row = sub_row(index, sid)
    assert row["failures"] == 3 and row["flagged"] and row["last_error"] == "connection reset"
    assert ix.due() == []
    del yt.fail["read_channel"]
    subs.update_indexed(index, sid, {"label": "fixed"})
    assert ix.due() == [(sid, "listing")]
    assert ix.run(sid, "listing") == "ok"
    row = sub_row(index, sid)
    assert row["failures"] == 0 and row["last_error"] is None


def test_a_failing_playlists_tab_does_not_count_against_the_subscription(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    yt.fail["channel_playlist_ids"] = ConnectionError("tab broken")
    for _ in range(4):
        assert ix.run(sid, "playlists") == "failed"
    row = sub_row(index, sid)
    assert row["failures"] == 0 and not row["flagged"] and row["last_error"] == "tab broken"


def test_an_empty_listing_of_a_channel_that_had_videos_is_a_failure(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 2))
    yt.add_channel(UC2, [])
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    empty = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC2}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    assert ix.run(sid, "listing") == "ok"
    assert ix.run(empty, "listing") == "ok"
    yt.channels[UC].videos = []
    assert ix.run(sid, "listing") == "failed"
    row = sub_row(index, sid)
    assert row["failures"] == 1 and row["last_error"] == "the listing returned no videos"
    assert len(members(index, sid)) == 2


def test_a_success_between_failures_resets_the_count(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    yt.fail["read_channel"] = ConnectionError("down")
    ix.run(sid, "listing")
    ix.run(sid, "listing")
    del yt.fail["read_channel"]
    ix.run(sid, "listing")
    yt.fail["read_channel"] = ConnectionError("down")
    ix.run(sid, "listing")
    ix.run(sid, "listing")
    assert not sub_row(index, sid)["flagged"]


def test_a_paused_kind_is_not_due_and_its_work_waits(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    gate.paused_kinds = {"listing"}
    assert ix.due() == []
    assert ix.refresh(sid) == "paused"
    assert yt.calls == [] and sub_row(index, sid)["listing_requested"] == 1
    gate.paused_kinds = set()
    assert ix.run_due() == {"listing:ok": 1}


def test_channel_playlists_are_read_daily_at_most_eight_per_pass_oldest_first(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 3))
    raw = [f"PL{n:02d}" for n in range(10)]
    yt.tabs[UC] = raw
    for n, pid in enumerate(raw):
        yt.playlists[pid] = playlist(pid, [3, 1, 50 + n])
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    ix.run(sid, "playlists")
    rows = index.execute("SELECT playlist_id, position, videos_at FROM playlists"
                         " ORDER BY position").fetchall()
    assert [r["playlist_id"] for r in rows] == [f"youtube:playlist:{p}" for p in raw]
    assert all(r["videos_at"] is None for r in rows)
    assert ix.run("*", "playlist_videos") == "ok"
    read = [c[1] for c in yt.calls if c[0] == "read_playlist"]
    assert read == raw[:8]
    items = [r[0] for r in index.execute(
        "SELECT e.key FROM playlist_items i JOIN entries_meta e ON e.entry_id = i.entry_id"
        " WHERE i.playlist_id = 'youtube:playlist:PL00' ORDER BY i.position")]
    assert items == [entry_key(vid(3)), entry_key(vid(1)), entry_key(vid(50))]
    clock.advance(hours=1)
    ix.run("*", "playlist_videos")
    assert [c[1] for c in yt.calls if c[0] == "read_playlist"][8:] == raw[8:]
    assert ("*", "playlist_videos") not in ix.due()
    clock.advance(hours=24)
    assert ("*", "playlist_videos") in ix.due()


def test_a_playlist_gone_from_the_tab_is_forgotten(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1))
    yt.tabs[UC] = ["PLa", "PLb"]
    yt.playlists["PLa"] = playlist("PLa", [1])
    yt.playlists["PLb"] = playlist("PLb", [1])
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    ix.run(sid, "playlists")
    ix.run("*", "playlist_videos")
    yt.tabs[UC] = ["PLb"]
    ix.run(sid, "playlists")
    assert [r[0] for r in index.execute("SELECT playlist_id FROM playlists")] == ["youtube:playlist:PLb"]
    assert index.execute("SELECT COUNT(*) FROM playlist_items WHERE playlist_id = 'youtube:playlist:PLa'"
                         ).fetchone()[0] == 0


def test_index_playlists_off_reads_no_playlists(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user",
                              index_playlists=False)[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    assert {job for _, job in ix.due()} == {"feed"}


def test_a_playlist_subscription_is_listed_daily_in_order(index, yt, gate, clock):
    yt.playlists["PLxyz"] = playlist("PLxyz", [7, 5, 6], title="Animation")
    sid = subs.create_indexed(index, "https://www.youtube.com/playlist?list=PLxyz", app="user")[0]["id"]
    assert sid == "youtube:playlist:PLxyz"
    ix = make(index, yt, gate, clock)
    assert ix.run_due() == {"listing:ok": 1}
    assert members(index, sid) == [entry_key(vid(n)) for n in (7, 5, 6)]
    row = sub_row(index, sid)
    assert row["title"] == "Animation" and row["thumbnail"] == "https://i.ytimg.com/pl/PLxyz.jpg"
    assert index.execute("SELECT video_count FROM playlists WHERE playlist_id = ?",
                         (sid,)).fetchone()[0] == 3
    assert ix.due() == []
    clock.advance(hours=24)
    assert ix.due() == [(sid, "listing")]


def test_a_channel_without_a_picture_is_retried_a_day_later(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 1), thumbnail=None)
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user",
                              index_playlists=False)[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    ix.run(sid, "feed")
    assert ix.due() == [(sid, "thumbnail")]
    yt.fail["read_channel"] = ConnectionError("no")
    assert ix.run(sid, "thumbnail") == "failed"
    row = sub_row(index, sid)
    assert row["thumbnail_error"] == "no" and row["failures"] == 0
    assert ix.due() == []
    clock.advance(hours=24)
    del yt.fail["read_channel"]
    yt.channels[UC].thumbnail = "https://yt3.example/late.jpg"
    assert (sid, "thumbnail") in ix.due()
    assert ix.run(sid, "thumbnail") == "ok"
    assert sub_row(index, sid)["thumbnail"] == "https://yt3.example/late.jpg"


def test_handle_and_channel_id_subscriptions_become_one(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 2), handle=HANDLE_URL)
    by_id = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(by_id, "listing")
    clock.advance(hours=1)
    by_handle = subs.create_indexed(index, HANDLE_URL, app="shortarr")[0]["id"]
    ix.run(by_handle, "listing")
    assert [r[0] for r in index.execute("SELECT id FROM subscriptions")] == [by_id]
    assert [r[0] for r in index.execute("SELECT app FROM subscription_holders ORDER BY app")] == \
        ["shortarr", "user"]
    firsts = {r[0] for r in index.execute("SELECT first_seen FROM memberships")}
    assert firsts == {"2024-05-01T12:00:00+00:00"}


def test_new_videos_become_json_rows_and_existing_rows_are_kept(index, yt, gate, clock):
    url3 = f"https://www.youtube.com/watch?v={vid(3)}"
    from media_archivist.storage import EnvelopeJsonStorage

    db = EnvelopeJsonStorage(str(index.json_path))
    db[url3] = {"source": "youtube", "url": url3, "videoId": vid(3), "title": "archived by hand"}
    db.store()
    yt.add_channel(UC, videos(1, 3))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(sid, "listing")
    data = json.loads(index.json_path.read_text())["entries"]
    assert set(data) == {f"https://www.youtube.com/watch?v={vid(n)}" for n in (1, 2, 3)}
    row = data[f"https://www.youtube.com/watch?v={vid(1)}"]
    assert (row["videoId"], row["title"], row["duration"], row["author"]) == \
        (vid(1), "Video 1", 600.0, "Some Channel")
    assert data[url3]["title"] == "archived by hand"


def test_a_handle_added_again_once_it_resolves_joins_the_unresolved_subscription(index):
    first, _ = subs.create_indexed(index, HANDLE_URL, app="user", resolve=lambda u: None)
    second, created = subs.create_indexed(index, HANDLE_URL, app="shortarr", resolve=lambda u: UC)
    assert not created and second["id"] == first["id"] == f"youtube:url:{HANDLE_URL}"
    assert second["holders"] == ["shortarr", "user"]


def test_merging_two_subscriptions_keeps_the_earlier_first_seen(index):
    by_handle = subs.create_indexed(index, HANDLE_URL, app="user")[0]["id"]
    by_id = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="shortarr")[0]["id"]
    with index.write() as conn:
        (a, b), _ = store.upsert_videos(conn, [VideoItem(vid(1)), VideoItem(vid(2))], "2024-05-01T10:00:00+00:00")
        store.record_memberships(conn, by_handle, [a], "2024-05-01T14:00:00+00:00")
        store.record_memberships(conn, by_handle, [b], "2024-05-01T09:00:00+00:00")
        store.record_memberships(conn, by_id, [a, b], "2024-05-01T12:00:00+00:00")
        assert subs.rekey_indexed(conn, by_handle, UC) == by_id
    firsts = dict(index.execute("SELECT e.key, m.first_seen FROM memberships m"
                                " JOIN entries_meta e ON e.entry_id = m.entry_id").fetchall())
    assert firsts == {entry_key(vid(1)): "2024-05-01T12:00:00+00:00",
                      entry_key(vid(2)): "2024-05-01T09:00:00+00:00"}
    assert [r[0] for r in index.execute("SELECT id FROM subscriptions")] == [by_id]


def test_new_entry_hooks_run_only_for_automatic_export(index, yt, gate, clock, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "ON_NEW_ENTRIES", [lambda ix, sid, ids: seen.append((sid, len(ids)))])
    yt.add_channel(UC, videos(1, 2))
    yt.add_channel(UC2, videos(10, 3))
    a = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    b = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC2}", app="user",
                            export={"automatic": True, "target_dir": "/media/x"})[0]["id"]
    ix = make(index, yt, gate, clock)
    ix.run(a, "listing")
    ix.run(b, "listing")
    yt.feeds[UC2] = [VideoItem(vid(99), "new")]
    ix.run(b, "feed")
    ix.run(b, "feed")
    assert seen == [(b, 3), (b, 1)]


def test_details_fill_missing_lengths_a_few_per_pass(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 7, duration=None))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    asked = []

    def extract(video_id):
        asked.append(video_id)
        return {"duration": 61, "upload_date": "20240430", "channel": "Some Channel",
                "channel_id": UC}

    ix = make(index, yt, gate, clock, extract=extract, details_per_run=5)
    ix.run(sid, "listing")
    assert ("*", "details") in ix.due()
    ix.run("*", "details")
    assert len(asked) == 5 and gate.kinds.count("details") == 5
    ix.run("*", "details")
    assert len(asked) == 7
    assert ("*", "details") not in ix.due()
    rows = index.execute("SELECT duration, published, metadata_checked FROM entries_meta").fetchall()
    assert {(r[0], r[1]) for r in rows} == {(61.0, "2024-04-30")} and all(r[2] for r in rows)


def test_details_stop_at_a_bot_check_and_wait_a_day_after_a_network_failure(index, yt, gate, clock):
    yt.add_channel(UC, videos(1, 3, duration=None))
    sid = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="user")[0]["id"]
    asked = []

    def bot(video_id):
        asked.append(video_id)
        raise RuntimeError("Sign in to confirm you're not a bot")

    ix = make(index, yt, gate, clock, extract=bot)
    ix.run(sid, "listing")
    ix.run("*", "details")
    assert len(asked) == 1

    def down(video_id):
        asked.append(video_id)
        raise ConnectionError("unreachable")

    ix.extract = down
    ix.run("*", "details")
    assert len(asked) == 4
    assert ("*", "details") not in ix.due()
    clock.advance(hours=24, seconds=1)
    assert ("*", "details") in ix.due()


def test_the_gate_errors_are_typed():
    gate = ytgate.PassThroughGate()
    with pytest.raises(ytgate.BotCheck):
        gate.call("listing", lambda: (_ for _ in ()).throw(RuntimeError("HTTP Error 429")))
    with pytest.raises(ytgate.RequestFailed) as e:
        gate.call("feed", lambda: (_ for _ in ()).throw(RuntimeError("HTTP Error 404: Not Found")))
    assert e.value.permanent and e.value.kind == "feed"
    with pytest.raises(ytgate.RequestFailed) as e:
        gate.call("feed", lambda: (_ for _ in ()).throw(ConnectionError("reset")))
    assert not e.value.permanent
    with pytest.raises(ValueError):
        gate.call("nonsense", lambda: 1)

    class HTTPFailure(Exception):
        def __init__(self, status):
            super().__init__(f"{status} Client Error")
            self.response = type("R", (), {"status_code": status})()

    def fail(status):
        raise HTTPFailure(status)

    for status, kind in ((429, ytgate.BotCheck), (403, ytgate.BotCheck), (404, ytgate.RequestFailed)):
        with pytest.raises(kind) as e:
            gate.call("feed", fail, status)
        assert isinstance(e.value, ytgate.BotCheck) or e.value.permanent
    assert gate.call("thumb", lambda x: x + 1, 1) == 2


def test_an_installed_gate_replaces_the_default():
    mine = FakeGate()
    previous = ytgate.install(mine)
    try:
        assert ytgate.get() is mine
    finally:
        ytgate.install(previous)
    assert isinstance(ytgate.get(), ytgate.ClientGate)
