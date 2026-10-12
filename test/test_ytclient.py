# SPDX-License-Identifier: Apache-2.0
"""The YouTube client: one spacing gate, per-kind pauses, the address family."""
from __future__ import annotations

import http.server
import json
import logging
import threading
import urllib.error
from pathlib import Path

import pytest

from media_archivist import backoff, indexdb, settings, streams, ytclient, ytmeta
from media_archivist.storage import EnvelopeJsonStorage

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_INFO = json.loads((FIXTURES / "yt_dlp_video_dQw4w9WgXcQ.json").read_text())
BOT_CHECK = (FIXTURES / "yt_dlp_bot_check_error.txt").read_text().strip()


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
def clock():
    return Clock()


@pytest.fixture
def client(clock):
    return ytclient.YouTubeClient(backoff=backoff.Backoff(clock=clock), clock=clock,
                                  sleep=clock.sleep)


@pytest.fixture
def index(tmp_path):
    return indexdb.IndexDB(str(tmp_path / "db.json"))


def ok():
    return "ok"


def fail(error):
    def run():
        raise error
    return run


class _Status(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


# spacing


def test_requests_are_spaced_ten_seconds_by_default(client, clock):
    client.call("details", ok)
    clock.now += 3
    client.call("listing", ok)
    client.call("thumb", ok)
    assert clock.sleeps == [7, 10]


def test_the_spacing_setting_applies_to_the_next_request(clock, index):
    client = ytclient.YouTubeClient(clock=clock, sleep=clock.sleep)
    client.bind(index)
    client.call("feed", ok)
    settings.Settings(index).set("youtube_request_spacing", 2.5)
    client.call("feed", ok)
    settings.Settings(index).set("youtube_request_spacing", 0)
    client.call("feed", ok)
    assert clock.sleeps == [2.5]


def test_the_gate_is_one_for_all_threads():
    client = ytclient.YouTubeClient()
    client.gate.spacing = lambda: 0.2
    starts = []

    def run():
        client.call("details", lambda: starts.append(client.gate.clock()))

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts.sort()
    assert len(starts) == 3
    assert all(b - a >= 0.19 for a, b in zip(starts, starts[1:]))


# pauses


def test_a_paused_kind_is_refused_at_once_without_a_request(client, clock):
    client.backoff.mark_blocked("details")
    called = []
    with pytest.raises(ytclient.PausedError) as e:
        client.call("details", lambda: called.append(1))
    assert called == [] and clock.sleeps == []
    assert e.value.kind == "details"
    assert e.value.until == clock.now + 1800 and e.value.remaining == 1800
    assert client.call("listing", ok) == "ok"


def test_a_bot_check_pauses_only_its_kind(client):
    with pytest.raises(ytclient.BotCheckError) as e:
        client.call("listing", fail(RuntimeError(BOT_CHECK)))
    assert e.value.kind == "listing" and e.value.wait == 1800
    assert client.backoff.blocked_until("listing") is not None
    assert client.backoff.blocked_until("details") is None
    with pytest.raises(ytclient.PausedError):
        client.call("listing", ok)


def test_a_success_ends_the_pause_and_resets_the_wait(client, clock):
    with pytest.raises(ytclient.BotCheckError):
        client.call("details", fail(RuntimeError(BOT_CHECK)))
    clock.now += 1800
    with pytest.raises(ytclient.BotCheckError) as e:
        client.call("details", fail(RuntimeError(BOT_CHECK)))
    assert e.value.wait == 3600
    clock.now += 3600
    assert client.call("details", ok) == "ok"
    assert client.backoff.blocked()["details"] == {"paused": False}
    with pytest.raises(ytclient.BotCheckError) as e:
        client.call("details", fail(RuntimeError(BOT_CHECK)))
    assert e.value.wait == 1800


@pytest.mark.parametrize("kind", backoff.KINDS)
def test_every_kind_pauses_on_its_own(client, kind):
    with pytest.raises(ytclient.BotCheckError):
        client.call(kind, fail(RuntimeError(BOT_CHECK)))
    state = client.backoff.blocked()
    assert [k for k, v in state.items() if v["paused"]] == [kind]


def test_an_unknown_kind_is_refused(client):
    with pytest.raises(ValueError):
        client.call("comments", ok)


# classification


@pytest.mark.parametrize("error, expected", [
    (RuntimeError("ERROR: [youtube] x: Video unavailable. Sign in to confirm you're not a bot"),
     ytclient.BotCheckError),
    (_Status("ERROR: [youtube] x: Private video", 403), ytclient.PermanentError),
    (_Status("Forbidden", 403), ytclient.BotCheckError),
    (_Status("Too Many", 429), ytclient.BotCheckError),
    (_Status("Not Found", 404), ytclient.PermanentError),
    (RuntimeError("Unable to download webpage: HTTP Error 410: Gone"), ytclient.PermanentError),
    (ConnectionResetError("reset by peer"), ytclient.NetworkError),
    (_Status("Bad Gateway", 502), ytclient.NetworkError),
    (KeyError("x"), ytclient.NetworkError),
])
def test_failures_are_classified_in_order(client, error, expected):
    with pytest.raises(ytclient.YouTubeError) as e:
        client.call("details", fail(error))
    assert type(e.value) is expected
    assert e.value.__cause__ is error
    assert (client.backoff.blocked_until("details") is not None) == (expected is ytclient.BotCheckError)


def test_a_failure_other_than_the_bot_check_leaves_the_wait(client, clock):
    with pytest.raises(ytclient.BotCheckError):
        client.call("details", fail(RuntimeError(BOT_CHECK)))
    clock.now += 1800
    with pytest.raises(ytclient.NetworkError):
        client.call("details", fail(ConnectionError("down")))
    with pytest.raises(ytclient.BotCheckError) as e:
        client.call("details", fail(RuntimeError(BOT_CHECK)))
    assert e.value.wait == 3600


def test_ytmeta_classifies_with_the_same_rules():
    assert ytmeta.failure_kind is ytclient.failure_kind
    assert issubclass(ytmeta.BotCheckError, ytmeta.MetadataError)
    assert issubclass(ytclient.PausedError, ytmeta.MetadataError)


# persistence


def test_a_bound_client_keeps_its_pauses_in_the_index(clock, index):
    client = ytclient.YouTubeClient(backoff=backoff.Backoff(clock=clock), clock=clock,
                                    sleep=clock.sleep)
    client.backoff.mark_blocked("feed")
    client.bind(index)
    assert backoff.Backoff(index).blocked_until("feed") is not None
    with pytest.raises(ytclient.BotCheckError):
        client.call("thumb", fail(RuntimeError(BOT_CHECK)))
    assert backoff.Backoff(index).blocked_until("thumb") is not None
    backoff.Backoff(index).mark_clear("thumb")
    assert client.call("thumb", ok) == "ok"


# address family


def test_ipv4_is_the_default_family(client):
    assert client.ip_family() == "ipv4"
    assert client.ytdlp_options() == {"source_address": "0.0.0.0"}
    assert client.ytdlp_args() == ["--force-ipv4"]


@pytest.mark.parametrize("family, options, args", [
    ("ipv4", {"source_address": "0.0.0.0"}, ["--force-ipv4"]),
    ("ipv6", {"source_address": "::"}, ["--force-ipv6"]),
    ("auto", {}, []),
])
def test_the_family_setting_pins_yt_dlp(index, family, options, args):
    client = ytclient.YouTubeClient()
    client.bind(index)
    settings.Settings(index).set("youtube_ip_family", family)
    assert client.ytdlp_options() == options
    assert client.ytdlp_args() == args


class _FakeYDL:
    calls = []
    error = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False, process=True):
        type(self).calls.append(dict(self.opts))
        if type(self).error:
            raise Exception(type(self).error)
        return dict(VIDEO_INFO)


@pytest.fixture
def fake_ytdlp(monkeypatch):
    _FakeYDL.calls = []
    _FakeYDL.error = None

    class _Module:
        YoutubeDL = _FakeYDL

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: _Module)
    return _FakeYDL


def test_metadata_reads_use_the_family(fake_ytdlp, index):
    ytclient.CLIENT.bind(index)
    ytmeta.extract_video("dQw4w9WgXcQ")
    settings.Settings(index).update({"youtube_ip_family": "ipv6",
                                     "youtube_request_spacing": 0})
    ytmeta.extract_video("dQw4w9WgXcQ")
    settings.Settings(index).set("youtube_ip_family", "auto")
    ytmeta.extract_video("dQw4w9WgXcQ")
    assert [c.get("source_address") for c in fake_ytdlp.calls] == ["0.0.0.0", "::", None]


def test_the_binary_gets_the_family_flag(monkeypatch, index):
    import subprocess

    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(VIDEO_INFO))

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    monkeypatch.setattr(ytmeta.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(ytmeta.subprocess, "run", run)
    ytclient.CLIENT.bind(index)
    settings.Settings(index).set("youtube_ip_family", "ipv6")
    ytmeta.extract_video("dQw4w9WgXcQ")
    assert "--force-ipv6" in seen[0] and "--force-ipv4" not in seen[0]
    assert seen[0][-1] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_metadata_reads_are_details_requests_of_the_client(fake_ytdlp):
    fake_ytdlp.error = BOT_CHECK
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("dQw4w9WgXcQ")
    assert ytclient.CLIENT.backoff.blocked_until("details") is not None
    fake_ytdlp.error = None
    with pytest.raises(ytclient.PausedError):
        ytmeta.extract_video("dQw4w9WgXcQ")
    assert len(fake_ytdlp.calls) == 1


def test_metadata_reads_are_spaced(fake_ytdlp, clock):
    client = ytclient.YouTubeClient(clock=clock, sleep=clock.sleep)
    ytmeta.extract_video("dQw4w9WgXcQ", client=client)
    ytmeta.extract_video("dQw4w9WgXcQ", client=client)
    assert clock.sleeps == [10]


def test_a_fill_while_details_is_paused_writes_nothing(tmp_path, fake_ytdlp):
    path = str(tmp_path / "db.json")
    row = {"source": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
           "videoId": "dQw4w9WgXcQ", "title": "t", "published": "", "duration": None,
           "author": None}
    db = EnvelopeJsonStorage(path)
    db[row["url"]] = row
    db.store()
    ytclient.CLIENT.backoff.mark_blocked("details")
    limiter = ytmeta.MetadataLimiter(0.0)
    assert limiter.try_acquire()
    res = ytmeta.fill_row(path, dict(row), limiter=limiter)
    assert res.status == "bot-check"
    assert fake_ytdlp.calls == []
    stored = EnvelopeJsonStorage(path)[row["url"]]
    assert not stored.get("metadata_checked") and not stored.get(ytmeta.NETWORK_FAILED_KEY)
    assert limiter.blocked


class _Session:
    def __init__(self, family):
        self.family = family


class _Channel:
    def __init__(self, url, session=None):
        self.url = url
        self.session = session


def test_tutubo_sessions_are_pinned_when_tutubo_can(monkeypatch, index):
    import tutubo.channel
    import tutubo.transport

    made = []

    def make_session(family):
        made.append(family)
        return _Session(family)

    monkeypatch.setattr(tutubo.transport, "make_session", make_session, raising=False)
    monkeypatch.setattr(tutubo.channel, "Channel", _Channel)
    client = ytclient.YouTubeClient()
    client.bind(index)
    assert client.channel("https://www.youtube.com/@x").session.family == "ipv4"
    settings.Settings(index).set("youtube_ip_family", "ipv6")
    assert client.channel("https://www.youtube.com/@x").session.family == "ipv6"
    settings.Settings(index).set("youtube_ip_family", "auto")
    assert client.channel("https://www.youtube.com/@x").session is None
    assert made == ["ipv4", "ipv6"]


def test_without_tutubo_support_nothing_is_pinned_and_a_warning_is_logged_once(
        monkeypatch, caplog):
    import socket

    import tutubo.transport
    import urllib3.util.connection

    monkeypatch.delattr(tutubo.transport, "make_session", raising=False)
    before = (urllib3.util.connection.allowed_gai_family,
              urllib3.util.connection.create_connection, socket.getaddrinfo)
    client = ytclient.YouTubeClient()
    with caplog.at_level(logging.WARNING, logger="media_archivist.ytclient"):
        assert client.tutubo_session() is None
        assert client.tutubo_session() is None
    warnings = [r for r in caplog.records if "address family" in r.getMessage()]
    assert len(warnings) == 1
    assert (urllib3.util.connection.allowed_gai_family,
            urllib3.util.connection.create_connection, socket.getaddrinfo) == before


# tutubo requests


class _Video:
    def __init__(self, video_id, duration_seconds=None):
        self.video_id = video_id
        self.duration_seconds = duration_seconds


class _Listing:
    lists = []

    def __init__(self, url, session=None):
        self.url = url

    @property
    def videos(self):
        type(self).lists.append(self.url)
        return iter([_Video("a" * 11, 61), _Video("b" * 11)])


def test_listings_are_listing_requests(monkeypatch, client):
    import tutubo.channel

    _Listing.lists = []
    monkeypatch.setattr(tutubo.channel, "Channel", _Listing)
    monkeypatch.setattr(tutubo.channel, "Playlist", _Listing)
    videos = client.list_videos("https://www.youtube.com/@x")
    assert [v.video_id for v in videos] == ["a" * 11, "b" * 11]
    client.list_videos("https://www.youtube.com/playlist?list=PLx")
    client.backoff.mark_blocked("listing")
    with pytest.raises(ytclient.PausedError):
        client.list_videos("https://www.youtube.com/@x")
    assert _Listing.lists == ["https://www.youtube.com/@x",
                              "https://www.youtube.com/playlist?list=PLx"]


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        self.send_response(type(self).status)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"jpg")

    def log_message(self, *a):
        pass


@pytest.fixture
def picture_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/vi/x/hqdefault.jpg"
    server.shutdown()
    server.server_close()


def test_pictures_are_thumb_requests(picture_server, client):
    _Handler.status = 200
    assert client.fetch(picture_server) == b"jpg"
    _Handler.status = 429
    with pytest.raises(ytclient.BotCheckError):
        client.fetch(picture_server)
    assert client.backoff.blocked_until("thumb") is not None
    assert client.backoff.blocked_until("details") is None
    _Handler.status = 404
    client.backoff.mark_clear("thumb")
    with pytest.raises(ytclient.PermanentError):
        client.fetch(picture_server)


# lengths


def test_lengths_come_from_the_listing_then_yt_dlp_for_unknown_ones_only(client):
    reads = []

    def read(video_id):
        reads.append(video_id)
        return dict(VIDEO_INFO)

    videos = [_Video("a" * 11, 61), _Video("b" * 11), _Video("c" * 11, 0), _Video("d" * 11)]
    out = ytclient.learn_lengths(videos, known={"d" * 11: 30.0}, read=read, client=client)
    assert reads == ["b" * 11, "c" * 11]
    assert out["a" * 11] == {"duration": 61.0, "published": None, "channel": None,
                             "source": "listing"}
    assert out["b" * 11] == {"duration": 213.0, "published": "2009-10-25",
                             "channel": "Rick Astley", "source": "yt-dlp"}
    assert "d" * 11 not in out


def test_length_reads_are_limited_and_stop_when_details_pauses(fake_ytdlp, clock):
    client = ytclient.YouTubeClient(backoff=backoff.Backoff(clock=clock), clock=clock,
                                    sleep=clock.sleep)
    videos = [_Video(c * 11) for c in "abcd"]
    out = ytclient.learn_lengths(videos, limit=2, client=client)
    assert sorted(out) == ["a" * 11, "b" * 11] and len(fake_ytdlp.calls) == 2

    fake_ytdlp.error = BOT_CHECK
    assert ytclient.learn_lengths(videos, client=client) == {}
    assert len(fake_ytdlp.calls) == 3
    assert client.backoff.blocked_until("details") is not None
    assert ytclient.learn_lengths(videos, client=client) == {}
    assert len(fake_ytdlp.calls) == 3


def test_a_video_yt_dlp_cannot_read_is_skipped(client):
    def read(video_id):
        if video_id == "a" * 11:
            raise ytclient.PermanentError("Video unavailable")
        return dict(VIDEO_INFO)

    out = ytclient.learn_lengths([_Video("a" * 11), _Video("b" * 11)], read=read, client=client)
    assert list(out) == ["b" * 11]


def test_http_error_objects_are_classified(client):
    err = urllib.error.HTTPError("https://i.ytimg.com/x", 403, "Forbidden", {}, None)
    with pytest.raises(ytclient.BotCheckError):
        client.call("thumb", fail(err))
