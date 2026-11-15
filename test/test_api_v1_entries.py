# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/entries``: filters, ``since``, stable cursors, fields, aliases, details."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import subscriptions as subs  # noqa: E402
from media_archivist.server import auth  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.ytindex import details, store  # noqa: E402
from media_archivist.ytindex.client import VideoItem  # noqa: E402
from ytindex_fakes import UC, UC2, FakeGate, vid  # noqa: E402

A = f"youtube:channel:{UC}"
B = f"youtube:channel:{UC2}"


def at(day, hour=0, minute=0, second=0):
    return f"2024-05-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+00:00"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    with TestClient(create_app(str(tmp_path / "db.json"))) as c:
        c.app.state.periodic.index_gate = FakeGate()
        yield c


@pytest.fixture
def index(client):
    return client.app.state.index


FIRST_LISTING = at(1)


def subscribe(index, sid, *, indexed_at=FIRST_LISTING):
    channel_id = sid.rsplit(":", 1)[1]
    subs.create_indexed(index, f"https://www.youtube.com/channel/{channel_id}", app="user")
    with index.write() as conn:
        conn.execute("UPDATE subscriptions SET first_indexed_at = ?, channel_id = ? WHERE id = ?",
                     (indexed_at, channel_id, sid))


def add(index, sid, items, when, *, channel_id=UC, channel="Some Channel"):
    with index.write() as conn:
        ids, _ = store.upsert_videos(conn, items, when, channel_id=channel_id, channel=channel)
        if sid:
            store.record_memberships(conn, sid, ids, when, positions=False)
    return ids


def get(client, **params):
    r = client.get("/api/v1/entries", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def keys(body):
    return [e["key"] for e in body["entries"]]


def test_an_entry_has_every_field(client, index):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "A Short Film", 754.0, "2024-05-03")], at(3, 14, 2, 11))
    body = get(client, subscription=A)
    assert body["complete"] is True and body["next_cursor"] is None
    e, = body["entries"]
    assert e == {
        "id": store.entry_id_for(vid(1)), "key": f"youtube:{vid(1)}",
        "url": f"https://www.youtube.com/watch?v={vid(1)}", "title": "A Short Film",
        "duration": 754.0, "published": "2024-05-03", "first_seen": "2024-05-03T14:02:11Z",
        "channel": {"id": UC, "title": "Some Channel"},
        "thumbnail": f"https://i.ytimg.com/vi/{vid(1)}/hqdefault.jpg", "vertical": None,
        "subscriptions": [A], "playlists": []}


def test_newest_first_and_paging_is_stable_while_new_entries_arrive(client, index):
    subscribe(index, A)
    for n in range(1, 8):
        add(index, A, [VideoItem(vid(n), f"V{n}", 100.0)], at(2, n))
    first = get(client, subscription=A, limit=3)
    assert keys(first) == [f"youtube:{vid(n)}" for n in (7, 6, 5)]
    add(index, A, [VideoItem(vid(8), "V8", 100.0), VideoItem(vid(9), "V9", 100.0)], at(9))
    second = get(client, subscription=A, limit=3, cursor=first["next_cursor"])
    assert keys(second) == [f"youtube:{vid(n)}" for n in (4, 3, 2)]
    last = get(client, subscription=A, limit=3, cursor=second["next_cursor"])
    assert keys(last) == [f"youtube:{vid(1)}"] and last["next_cursor"] is None
    assert set(keys(get(client, subscription=A, limit=2))) == {f"youtube:{vid(9)}", f"youtube:{vid(8)}"}


def test_ties_on_first_seen_are_broken_by_id_without_losing_or_repeating(client, index):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(n), f"V{n}", 100.0) for n in range(25)], at(2))
    seen, cursor = [], None
    while True:
        body = get(client, subscription=A, limit=4, **({"cursor": cursor} if cursor else {}))
        seen += [e["id"] for e in body["entries"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 25 and seen == sorted(seen, reverse=True)


def test_first_seen_after_returns_only_what_arrived_since(client, index):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "old", 100.0)], at(2))
    add(index, A, [VideoItem(vid(2), "new", 100.0)], at(3))
    newest = get(client, subscription=A)["entries"][0]["first_seen"]
    assert keys(get(client, first_seen_after="2024-05-02T00:00:00Z")) == [f"youtube:{vid(2)}"]
    assert get(client, first_seen_after=newest)["entries"] == []


def test_since_uses_the_upload_date_and_else_first_seen_after_the_first_listing(client, index):
    subscribe(index, A, indexed_at=at(2, 12))
    add(index, A, [VideoItem(vid(1), "back catalogue, no date", 100.0)], at(2, 12))
    add(index, A, [VideoItem(vid(2), "dated, old", 100.0, "2023-01-01")], at(2, 12))
    add(index, A, [VideoItem(vid(3), "dated, new", 100.0, "2024-05-05")], at(2, 12))
    add(index, A, [VideoItem(vid(4), "found later, no date", 100.0)], at(6))
    add(index, A, [VideoItem(vid(5), "found later, before T", 100.0)], at(3))
    body = get(client, subscription=A, since="2024-05-04T00:00:00Z")
    assert set(keys(body)) == {f"youtube:{vid(3)}", f"youtube:{vid(4)}"}
    body = get(client, subscription=A, since="2024-05-01T00:00:00Z")
    assert set(keys(body)) == {f"youtube:{vid(n)}" for n in (3, 4, 5)}
    assert set(keys(get(client, since="2024-05-04"))) == {f"youtube:{vid(3)}", f"youtube:{vid(4)}"}


def test_a_subscription_before_its_first_listing_counts_nothing_as_new(client, index):
    subscribe(index, A, indexed_at=None)
    add(index, A, [VideoItem(vid(1), "x", 100.0)], at(6))
    body = get(client, subscription=A, since="2024-05-01T00:00:00Z")
    assert body["entries"] == [] and body["complete"] is False


def test_duration_window_and_unknown_durations(client, index):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "short", 59.0), VideoItem(vid(2), "film", 754.0),
                   VideoItem(vid(3), "long", 7200.0), VideoItem(vid(4), "unknown", None)], at(2))
    window = {"min_duration": 60, "max_duration": 3600}
    assert keys(get(client, **window)) == [f"youtube:{vid(2)}"]
    assert set(keys(get(client, unknown_duration="include", **window))) == \
        {f"youtube:{vid(2)}", f"youtube:{vid(4)}"}
    assert set(keys(get(client, min_duration=754))) == {f"youtube:{vid(2)}", f"youtube:{vid(3)}"}
    assert len(get(client)["entries"]) == 4
    assert f"youtube:{vid(4)}" not in keys(get(client, unknown_duration="exclude"))


def test_exclude_matches_whole_words_and_still_fills_the_page(client, index):
    subscribe(index, A)
    titles = ["Official Trailers", "Behind-the-Scenes", "Trailblazer", "A Film"] + \
        [f"Trailer {n}" for n in range(300)]
    add(index, A, [VideoItem(vid(n), t, 100.0) for n, t in enumerate(titles)], at(2))
    with index.write() as conn:
        for n in range(4, len(titles)):
            conn.execute("UPDATE entries_meta SET first_seen = ? WHERE key = ?",
                         (at(5), f"youtube:{vid(n)}"))
    body = get(client, exclude="trailer,behind the scenes", limit=2)
    assert sorted(e["title"] for e in body["entries"]) == ["A Film", "Trailblazer"]
    assert body["next_cursor"] is None


def test_vertical_filter(client, index):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "short", 30.0, vertical=True),
                   VideoItem(vid(2), "film", 600.0), VideoItem(vid(3), "wide", 600.0, vertical=False)],
        at(2))
    assert keys(get(client, vertical="true")) == [f"youtube:{vid(1)}"]
    assert set(keys(get(client, vertical="false"))) == {f"youtube:{vid(2)}", f"youtube:{vid(3)}"}
    assert get(client, vertical="true")["entries"][0]["vertical"] is True


def test_several_subscriptions_and_fields(client, index):
    subscribe(index, A)
    subscribe(index, B)
    add(index, A, [VideoItem(vid(1), "a", 100.0)], at(2))
    add(index, B, [VideoItem(vid(2), "b", 100.0)], at(3), channel_id=UC2)
    add(index, B, [VideoItem(vid(1), "a", 100.0)], at(4), channel_id=UC2)
    add(index, None, [VideoItem(vid(3), "not subscribed", 100.0)], at(5))
    body = get(client, subscription=f"{A},{B}", fields="key,subscriptions")
    assert body["entries"] == [
        {"key": f"youtube:{vid(2)}", "subscriptions": [B]},
        {"key": f"youtube:{vid(1)}", "subscriptions": [A, B]}]
    assert len(get(client)["entries"]) == 3


@pytest.mark.parametrize("params,status", [
    ({"subscription": "youtube:channel:UCnope"}, 404),
    ({"playlist": "youtube:playlist:PLnope"}, 404),
    ({"fields": "id,colour"}, 400),
    ({"limit": 0}, 400),
    ({"limit": 1001}, 400),
    ({"cursor": "garbage!"}, 400),
    ({"since": "last tuesday"}, 400),
    ({"first_seen_after": "2024-13-01"}, 400),
    ({"unknown_duration": "maybe"}, 400),
    ({"min_duration": 10, "max_duration": 5}, 400),
    ({"min_duration": -1}, 400),
])
def test_queries_that_cannot_be_answered(client, index, params, status):
    r = client.get("/api/v1/entries", params=params)
    assert r.status_code == status and r.json()["detail"]


def test_one_entry_by_id_or_key(client, index):
    subscribe(index, A)
    entry_id, = add(index, A, [VideoItem(vid(1), "x", 100.0, "2024-05-01")], at(2))
    by_key = client.get(f"/api/v1/entries/youtube:{vid(1)}").json()
    assert by_key == client.get(f"/api/v1/entries/{entry_id}").json()
    assert by_key["id"] == entry_id and by_key["subscriptions"] == [A]
    assert client.get("/api/v1/entries/youtube:nonexistent").status_code == 404


def test_one_entry_lacking_details_is_filled_before_answering(client, index, monkeypatch):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "x", None)], at(2), channel_id=None, channel=None)
    asked = []

    def extract(video_id):
        asked.append(video_id)
        return {"duration": 61, "upload_date": "20240430", "channel": "Some Channel", "channel_id": UC}

    monkeypatch.setattr(details, "EXTRACT", extract)
    gate = client.app.state.periodic.index_gate
    gate.paused_kinds = {"details"}
    body = client.get(f"/api/v1/entries/youtube:{vid(1)}").json()
    assert body["duration"] is None and asked == []
    gate.paused_kinds = set()
    body = client.get(f"/api/v1/entries/youtube:{vid(1)}").json()
    assert (body["duration"], body["published"], body["channel"]) == \
        (61.0, "2024-04-30", {"id": UC, "title": "Some Channel"})
    assert asked == [vid(1)]
    client.get(f"/api/v1/entries/youtube:{vid(1)}")
    assert asked == [vid(1)]


def test_an_entry_read_in_full_once_is_not_read_again(client, index, monkeypatch):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "live", None)], at(2), channel_id=None, channel=None)
    asked = []
    monkeypatch.setattr(details, "EXTRACT", lambda v: asked.append(v) or {"title": "live"})
    for _ in range(3):
        body = client.get(f"/api/v1/entries/youtube:{vid(1)}").json()
    assert asked == [vid(1)] and body["duration"] is None


def test_one_entry_answers_at_once_while_another_details_read_runs(client, index, monkeypatch):
    subscribe(index, A)
    add(index, A, [VideoItem(vid(1), "x", None)], at(2))
    monkeypatch.setattr(details, "EXTRACT", lambda v: pytest.fail("must not read"))
    assert details._running.acquire(blocking=False)
    try:
        assert client.get(f"/api/v1/entries/youtube:{vid(1)}").json()["duration"] is None
    finally:
        details._running.release()


def test_a_2000_video_channel_answers_within_bounds(client, index):
    subscribe(index, A)
    subscribe(index, B)
    items = [VideoItem(vid(n), f"Episode {n} {'trailer' if n % 10 == 0 else 'film'}",
                       float(30 + (n * 37) % 4000) if n % 7 else None,
                       f"2024-0{1 + n % 4}-1{n % 10}", vertical=(n % 5 == 0) or None)
             for n in range(2000)]
    with index.write() as conn:
        for start in range(0, 2000, 100):
            when = at(1 + start // 100, 0, 0, 0)
            ids, _ = store.upsert_videos(conn, items[start:start + 100], when, channel_id=UC)
            store.record_memberships(conn, A, ids, when)
            store.record_memberships(conn, B, ids[:10], when)
    params = {"subscription": A, "min_duration": 60, "max_duration": 1800,
              "exclude": "trailer", "vertical": "false", "since": "2024-02-01T00:00:00Z"}
    t0 = time.perf_counter()
    body = get(client, limit=100, **params)
    first_page = time.perf_counter() - t0
    assert len(body["entries"]) == 100
    t0 = time.perf_counter()
    total, cursor = len(body["entries"]), body["next_cursor"]
    while cursor:
        body = get(client, limit=100, cursor=cursor, **params)
        total += len(body["entries"])
        cursor = body["next_cursor"]
    walk = time.perf_counter() - t0
    expected = sum(1 for n, i in enumerate(items)
                   if i.duration and 60 <= i.duration <= 1800 and n % 10 and n % 5 != 0
                   and i.published >= "2024-02-01")
    assert total == expected
    assert first_page < 0.5, first_page
    assert walk < 5.0, walk
    t0 = time.perf_counter()
    assert len(get(client, subscription=A, limit=1000)["entries"]) == 1000
    assert time.perf_counter() - t0 < 1.5
