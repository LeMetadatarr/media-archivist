# SPDX-License-Identifier: Apache-2.0
"""How the app API's parts use one another: the indexer, exports, streams and
downloads all go through the one YouTube client."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_archivist import backoff, downloads, indexdb, links, strm, streams, ytclient, ytgate
from media_archivist import subscriptions as subs
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source
from media_archivist.server.app import create_app
from media_archivist.ytindex.engine import Indexer
from ytindex_fakes import UC, Clock as DateClock, FakeYouTube, videos

BOT_CHECK = (Path(__file__).parent / "fixtures" / "yt_dlp_bot_check_error.txt").read_text().strip()


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ytclient, "CLIENT", ytclient.YouTubeClient(
        backoff=backoff.Backoff(clock=clock), clock=clock, sleep=clock.sleep))
    return clock


def fail(error):
    def run():
        raise error
    return run


# the client


def test_a_request_inside_a_request_of_its_kind_is_one_request(clock):
    client = ytclient.CLIENT
    client.call("details", lambda: "first")
    assert client.call("details", lambda: client.call("details", lambda: "inner")) == "inner"
    assert clock.sleeps == [10]
    client.call("listing", lambda: client.call("thumb", lambda: "picture"))
    assert clock.sleeps == [10, 10, 10]


def test_the_gate_lets_a_start_through_only_when_spacing_allows(clock):
    gate = ytclient.CLIENT.gate
    assert gate.try_start() == 0
    clock.now += 4
    assert gate.try_start() == 6
    clock.now += 6
    assert gate.try_start() == 0
    ytclient.CLIENT.call("details", lambda: None)
    assert clock.sleeps == [10]


# the indexer's gate


def test_the_default_gate_is_the_client(clock):
    gate = ytgate.get()
    gate.call("listing", lambda: None)
    gate.call("feed", lambda: None)
    assert clock.sleeps == [10]
    with pytest.raises(ytgate.BotCheck):
        gate.call("listing", fail(RuntimeError(BOT_CHECK)))
    assert ytclient.CLIENT.backoff.blocked_until("listing") is not None
    assert gate.paused("listing") and not gate.paused("feed")
    ran = []
    with pytest.raises(ytgate.Paused) as e:
        gate.call("listing", lambda: ran.append(1))
    assert ran == [] and e.value.until == ytclient.CLIENT.backoff.blocked_until("listing")
    with pytest.raises(ytgate.RequestFailed) as e:
        gate.call("feed", fail(RuntimeError("ERROR: [youtube] x: Private video")))
    assert e.value.permanent
    with pytest.raises(ytgate.RequestFailed) as e:
        gate.call("feed", fail(ConnectionError("connection reset")))
    assert not e.value.permanent
    with pytest.raises(ValueError):
        gate.call("comments", lambda: None)


def test_a_details_read_by_the_indexer_is_spaced_once(clock, tmp_path, monkeypatch):
    from media_archivist import ytmeta
    from media_archivist.ytindex import details

    reads = []

    def read():
        reads.append(1)
        return {"duration": 61, "channel": "C", "upload_date": "20240501"}

    monkeypatch.setattr(ytmeta, "_extract_python", lambda *a, **k: read())
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: object())
    index = indexdb.IndexDB(tmp_path / "db.json")
    with index.write() as conn:
        conn.execute("INSERT INTO entries_meta (entry_id, key, url, first_seen) VALUES ('e1', 'youtube:vid00000001',"
                     " 'https://www.youtube.com/watch?v=vid00000001', '2024-05-01')")
    ytclient.CLIENT.call("listing", lambda: None)
    assert details.fill(index, "e1") == "filled"
    assert reads == [1] and clock.sleeps == [10]
    index.close()


def test_the_indexer_exports_new_entries_of_an_automatic_subscription(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv(strm.ENV_LIBRARY_ROOTS, str(library))
    monkeypatch.setenv(links.ENV_PUBLIC_URL, "http://archivist.lan:8000")
    for var in (links.ENV_SECRET, "MEDIA_ARCHIVIST_JELLYFIN_URL", "MEDIA_ARCHIVIST_JELLYFIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    db = tmp_path / "db.json"
    index = indexdb.open_index(str(db))
    yt = FakeYouTube()
    yt.add_channel(UC, videos(1, 2))
    plain = subs.create_indexed(index, f"https://www.youtube.com/channel/{UC}", app="a")[0]["id"]
    Indexer(index, client=yt, gate=ytgate.PassThroughGate(), clock=DateClock()).run(plain, "listing")
    assert not list(library.rglob("*.strm"))

    with index.write() as conn:
        conn.execute("UPDATE subscriptions SET export_automatic = 1, export_target = ?,"
                     " export_layout = 'movies'", (str(library / "auto"),))
    yt.feeds[UC] = videos(3, 1, title="Fresh {n}")
    Indexer(index, client=yt, gate=ytgate.PassThroughGate(), clock=DateClock()).run(plain, "feed")
    written = sorted(p.name for p in library.rglob("*.strm"))
    assert len(written) == 1 and written[0].startswith("Fresh 3")
    row = index.execute("SELECT scope_id, automatic FROM exports").fetchone()
    assert tuple(row) == (plain, 1)
    indexdb.close_all()


# play-time resolution and pictures


def _entry(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
    return MediaEntry(id="e1", source=Source.YOUTUBE, url=url, title="t")


def test_stream_resolution_is_a_details_request_of_the_client(clock, monkeypatch, tmp_path):
    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    monkeypatch.setattr(streams, "resolve_stream",
                        lambda url, **kw: types.SimpleNamespace(url="https://cdn.example/v"))
    ytclient.CLIENT.call("listing", lambda: None)
    clock.now += 10
    assert stream._resolve(index, _entry()) == "https://cdn.example/v"
    assert clock.sleeps == []

    def bot(url, **kw):
        raise streams.StreamResolveError(BOT_CHECK)

    monkeypatch.setattr(streams, "resolve_stream", bot)
    clock.now += 10
    assert stream._resolve(index, _entry()) is None
    assert ytclient.CLIENT.backoff.blocked_until("details") is not None
    index.close()


def _resolver(monkeypatch):
    resolved = []

    def resolve(url, **kw):
        resolved.append(url)
        return types.SimpleNamespace(url="https://cdn.example/v")

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    return resolved


def test_a_play_takes_no_turn_in_the_spacing(clock, monkeypatch, tmp_path):
    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    resolved = _resolver(monkeypatch)
    ytclient.CLIENT.call("listing", lambda: None)
    assert stream._resolve(index, _entry()) == "https://cdn.example/v"
    assert resolved and clock.sleeps == []
    assert ytclient.CLIENT.gate.try_start() == 10
    clock.now += 10
    assert ytclient.CLIENT.gate.try_start() == 0
    index.close()


def test_a_paused_details_kind_sends_a_play_to_the_fallback(clock, monkeypatch, tmp_path):
    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    resolved = _resolver(monkeypatch)
    ytclient.CLIENT.backoff.mark_blocked("details")
    assert stream._resolve(index, _entry()) is None
    assert resolved == []
    index.close()


def test_a_play_does_not_wait_behind_a_sleeping_request(clock, monkeypatch, tmp_path):
    import threading
    import time

    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    _resolver(monkeypatch)
    gate = ytclient.CLIENT.gate
    sleeping = threading.Event()
    release = threading.Event()

    def slow_sleep(seconds):
        sleeping.set()
        release.wait(10)

    gate.sleep = slow_sleep
    ytclient.CLIENT.call("listing", lambda: None)
    waiter = threading.Thread(target=lambda: ytclient.CLIENT.call("feed", lambda: None))
    waiter.start()
    assert sleeping.wait(5)
    try:
        started = time.monotonic()
        assert gate.try_start() > 0
        assert stream._play_url(index, _entry()) == "https://cdn.example/v"
        assert time.monotonic() - started < 1
    finally:
        release.set()
        waiter.join()
    index.close()


def test_plays_of_one_entry_share_one_resolution_for_a_while(clock, monkeypatch, tmp_path):
    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    resolved = _resolver(monkeypatch)
    assert [stream._play_url(index, _entry()) for _ in range(5)] == ["https://cdn.example/v"] * 5
    assert len(resolved) == 1
    other = MediaEntry(id="e2", source=Source.YOUTUBE, url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
                       title="t")
    stream._play_url(index, other)
    assert len(resolved) == 2
    monkeypatch.setattr(stream, "PLAY_CACHE_TTL_S", 0.0)
    stream._play_url(index, _entry("https://www.youtube.com/watch?v=bbbbbbbbbbb"))
    stream._play_url(index, _entry("https://www.youtube.com/watch?v=bbbbbbbbbbb"))
    assert len(resolved) == 4
    index.close()


def test_a_failed_resolution_is_not_cached(clock, monkeypatch, tmp_path):
    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    ytclient.CLIENT.backoff.mark_blocked("details")
    assert stream._play_url(index, _entry()) is None
    ytclient.CLIENT.backoff.mark_clear("details")
    resolved = _resolver(monkeypatch)
    assert stream._play_url(index, _entry()) == "https://cdn.example/v"
    assert len(resolved) == 1
    index.close()


def test_plays_cannot_delay_a_download_waiting_for_its_turn(clock, monkeypatch, tmp_path):
    from media_archivist.server.api_v1 import stream

    index = indexdb.IndexDB(tmp_path / "db.json")
    lane = downloads.Downloads(index)
    with index.write() as conn:
        conn.execute("INSERT INTO entries_meta (entry_id, key, url, first_seen) VALUES ('e1', 'youtube:dQw4w9WgXcQ',"
                     " 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', '2024-05-01')")
        conn.execute("INSERT INTO downloads (id, app, entry_id, target_dir, status, created_at,"
                     " updated_at) VALUES ('d1', 'a', 'e1', ?, 'queued', '', '')", (str(tmp_path),))
    _resolver(monkeypatch)
    monkeypatch.setattr(stream, "PLAY_CACHE_TTL_S", 0.0)
    claimed = []
    lane._claim_next = lambda: claimed.append(clock.now) or None
    ytclient.CLIENT.call("listing", lambda: None)
    start = clock.now
    assert lane._start_next() == 10
    plays = []
    for _ in range(15):
        clock.now += 1
        plays.append(stream._play_url(index, _entry()))
        lane._start_next()
        if claimed:
            break
    assert claimed == [start + 10]
    assert plays and all(p == "https://cdn.example/v" for p in plays)
    index.close()


def test_a_request_that_began_before_a_bot_check_does_not_end_its_pause():
    import threading

    client = ytclient.YouTubeClient(sleep=lambda s: None)
    inside, go = threading.Event(), threading.Event()

    def play():
        inside.set()
        go.wait(5)
        return "https://cdn.example/v"

    worker = threading.Thread(target=lambda: client.call_unspaced("details", play))
    worker.start()
    assert inside.wait(5)

    def blocked():
        raise RuntimeError("Sign in to confirm you're not a bot")

    with pytest.raises(ytclient.BotCheckError):
        client.call("details", blocked)
    until = client.backoff.blocked_until("details")
    assert until is not None
    go.set()
    worker.join(5)
    assert client.backoff.blocked_until("details") == until
    client.backoff.mark_clear("details")
    assert client.backoff.blocked_until("details") is None
    assert client.call("details", lambda: "ok") == "ok"


def test_yt_dlp_resolution_uses_the_address_family(clock, monkeypatch):
    seen = []

    class FakeYDL:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"url": "https://cdn.example/v", "formats": [
                {"url": "https://cdn.example/v", "acodec": "aac", "vcodec": "h264"}]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: sys.modules["yt_dlp"])
    streams._resolve_via_python("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "best", 10)
    assert seen[-1]["source_address"] == "0.0.0.0"
    streams._resolve_via_python("https://media.example/a.mp4", "best", 10)
    assert "source_address" not in seen[-1]


def test_export_posters_are_thumb_requests_of_the_client(clock, monkeypatch, tmp_path):
    from media_archivist import movie_layout

    fetched = []

    def fetch(entry, folder):
        fetched.append(entry.id)
        return None

    monkeypatch.setattr(movie_layout, "_fetch_poster", fetch)
    index = indexdb.IndexDB(tmp_path / "db.json")
    job = strm.ExportJob(export_id="x", posters=[(_entry(), tmp_path), (_entry(), tmp_path)],
                         refresh=False, collection=None)
    with index.write() as conn:
        conn.execute("INSERT INTO exports (id, scope_kind, scope_id, target_dir, layout, links,"
                     " app, created_at, updated_at) VALUES ('x', 'entries', 'e1', ?, 'movies', 'plain',"
                     " 'a', '', '')", (str(tmp_path),))
    strm.finish_export(index, job)
    assert fetched == ["e1", "e1"] and clock.sleeps == [10]
    ytclient.CLIENT.backoff.mark_blocked("thumb")
    strm.finish_export(index, job)
    assert fetched == ["e1", "e1"]
    index.close()


# downloads


def test_a_download_start_takes_its_turn_at_the_shared_gate(clock, tmp_path):
    index = indexdb.IndexDB(tmp_path / "db.json")
    lane = downloads.Downloads(index)
    with index.write() as conn:
        conn.execute("INSERT INTO entries_meta (entry_id, key, url, first_seen) VALUES ('e1', 'youtube:dQw4w9WgXcQ',"
                     " 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', '2024-05-01')")
        conn.execute("INSERT INTO downloads (id, app, entry_id, target_dir, status, created_at,"
                     " updated_at) VALUES ('d1', 'a', 'e1', ?, 'queued', '', '')", (str(tmp_path),))
    ytclient.CLIENT.call("listing", lambda: None)
    lane._claim_next = lambda: None
    assert lane._start_next() == 10
    clock.now += 10
    assert lane._start_next() is None
    assert ytclient.CLIENT.gate.try_start() == 10
    with index.write() as conn:
        conn.execute("DELETE FROM downloads")
    clock.now += 10
    assert lane._start_next() is None
    assert ytclient.CLIENT.gate.try_start() == 0
    index.close()


def test_ending_the_download_pause_wakes_the_lane(tmp_path, monkeypatch):
    woken = []
    monkeypatch.setattr(downloads.Downloads, "wake", lambda self: woken.append(1))
    db = str(tmp_path / "db.json")
    with TestClient(create_app(db)) as c:
        backoff.Backoff(indexdb.open_index(db)).mark_blocked("download")
        assert c.delete("/api/v1/youtube/backoff/download").status_code == 200
        assert woken == [1]
        assert c.post("/api/v1/youtube/backoff/details/clear").status_code == 200
        assert woken == [1]
