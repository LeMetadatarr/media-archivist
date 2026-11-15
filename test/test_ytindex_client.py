# SPDX-License-Identifier: Apache-2.0
"""The indexer's YouTube client: feed parsing and fetching, tutubo listings."""
from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from media_archivist.ytindex import client
from media_archivist.ytindex.client import video_id_from_url

FEED = (Path(__file__).parent / "fixtures" / "youtube_channel_feed.xml").read_text()
UC = "UC" + "a" * 22


def test_feed_items_keep_ids_and_dates_and_shorts_links_become_vertical():
    items = client.parse_feed(FEED)
    assert [i.video_id for i in items] == ["shortAAAAAA", "dQw4w9WgXcQ"]
    short, film = items
    assert (short.title, short.published, short.vertical) == ("A Tiny Short #shorts", "2024-05-04", True)
    assert (film.title, film.published, film.vertical) == ("A Short Film", "2024-05-03", None)
    assert film.thumbnail == "https://i1.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert film.duration is None


def test_a_feed_entry_without_a_video_id_element_takes_it_from_its_link():
    xml = FEED.replace("<yt:videoId>shortAAAAAA</yt:videoId>", "")
    assert client.parse_feed(xml)[0].video_id == "shortAAAAAA"


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/shorts/shortAAAAAA", "shortAAAAAA"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?si=x", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/channel/" + UC, None),
    ("https://www.youtube.com/watch?v=short", None),
])
def test_video_ids_from_urls(url, expected):
    assert video_id_from_url(url) == expected


@pytest.fixture
def feed_server(monkeypatch):
    requests_seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(self.path)
            if UC not in self.path:
                self.send_response(404)
                self.end_headers()
                return
            body = FEED.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/atom+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(client, "FEED_URL",
                        f"http://127.0.0.1:{server.server_address[1]}/feeds/videos.xml?channel_id={{channel_id}}")
    yield requests_seen
    server.shutdown()
    server.server_close()


def test_the_feed_is_one_request(feed_server):
    items = client.read_feed(UC)
    assert len(items) == 2
    assert feed_server == [f"/feeds/videos.xml?channel_id={UC}"]


def test_a_missing_feed_raises_for_the_gate_to_classify(feed_server):
    from media_archivist import ytgate

    with pytest.raises(ytgate.RequestFailed) as e:
        ytgate.PassThroughGate().call("feed", client.read_feed, "UC" + "z" * 22)
    assert e.value.permanent


class _Video:
    def __init__(self, video_id, title=None, duration_seconds=None):
        self.video_id = video_id
        self.title = title
        self.duration_seconds = duration_seconds


class _Channel:
    made = []
    sessions = []

    def __init__(self, url, session=None):
        _Channel.made.append(url)
        _Channel.sessions.append(session)
        self.channel_id = UC
        self.channel_name = "Some Channel"
        self.thumbnail_url = "https://yt3.example/c.jpg"
        self.videos = [_Video("aaaaaaaaaaa", "One", 61), _Video("aaaaaaaaaaa", "One again", 61),
                       _Video("bbbbbbbbbbb", "Live", None), _Video("bad", "Not an id", 5)]
        self.playlist_urls = ["https://www.youtube.com/playlist?list=PL1",
                              "https://www.youtube.com/playlist?list=PL2",
                              "https://www.youtube.com/playlist?list=PL1"]


class _Playlist:
    def __init__(self, url, session=None):
        self.url = url
        self.session = session
        self.title = "Animation"
        self.thumbnail_url = "https://i.ytimg.com/pl.jpg"
        self.videos = [_Video("ccccccccccc", None, 30), _Video("aaaaaaaaaaa", None, 61)]


@pytest.fixture
def fake_tutubo(monkeypatch):
    import tutubo.channel

    _Channel.made = []
    _Channel.sessions = []
    monkeypatch.setattr(tutubo.channel, "Channel", _Channel)
    monkeypatch.setattr(tutubo.channel, "Playlist", _Playlist)


def test_a_channel_listing_through_tutubo(fake_tutubo):
    info = client.read_channel("https://www.youtube.com/@SomeChannel", videos=True)
    assert (info.channel_id, info.title, info.thumbnail) == (UC, "Some Channel", "https://yt3.example/c.jpg")
    assert [(v.video_id, v.title, v.duration) for v in info.videos] == \
        [("aaaaaaaaaaa", "One", 61.0), ("bbbbbbbbbbb", "Live", None)]
    assert client.read_channel("https://www.youtube.com/@SomeChannel").videos == []
    assert all(s is not None for s in _Channel.sessions)


def test_a_channel_page_without_an_id_is_an_error(fake_tutubo, monkeypatch):
    monkeypatch.setattr(_Channel, "__init__", lambda self, url, session=None: setattr(self, "channel_id", ""))
    with pytest.raises(LookupError):
        client.read_channel("https://www.youtube.com/@gone")


def test_channel_playlist_ids_keep_the_tab_order_once(fake_tutubo):
    assert client.channel_playlist_ids("https://www.youtube.com/channel/" + UC) == ["PL1", "PL2"]


def test_a_playlist_through_tutubo(fake_tutubo):
    info = client.read_playlist("PLxyz")
    assert (info.playlist_id, info.title, info.thumbnail) == ("PLxyz", "Animation", "https://i.ytimg.com/pl.jpg")
    assert [(v.video_id, v.duration) for v in info.videos] == [("ccccccccccc", 30.0), ("aaaaaaaaaaa", 61.0)]
