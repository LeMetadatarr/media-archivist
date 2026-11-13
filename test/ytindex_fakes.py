# SPDX-License-Identifier: Apache-2.0
"""A fake YouTube for the index tests: channels, playlists and feeds in memory."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from media_archivist import ytgate
from media_archivist.ytindex.client import ChannelInfo, PlaylistInfo, VideoItem

UC = "UC" + "a" * 22
UC2 = "UC" + "b" * 22
HANDLE_URL = "https://www.youtube.com/@SomeChannel"


def vid(n: int) -> str:
    return f"v{n:010d}"


class FakeYouTube:
    """Answers the indexer's client calls from dictionaries; records every call."""

    def __init__(self):
        self.channels = {}   # url or channel id -> ChannelInfo
        self.handles = {}    # handle URL -> channel id
        self.playlists = {}  # raw playlist id -> PlaylistInfo
        self.tabs = {}       # channel id -> [raw playlist ids]
        self.feeds = {}      # channel id -> [VideoItem]
        self.calls = []
        self.fail = {}       # method name -> exception to raise

    def add_channel(self, channel_id, videos, *, title="Some Channel",
                    thumbnail="https://yt3.example/c.jpg", handle=None):
        self.channels[channel_id] = ChannelInfo(channel_id=channel_id, title=title,
                                                thumbnail=thumbnail, videos=list(videos))
        if handle:
            self.handles[handle] = channel_id

    def _check(self, name):
        if name in self.fail:
            raise self.fail[name]

    def read_channel(self, url, *, videos=False):
        self.calls.append(("read_channel", url, videos))
        self._check("read_channel")
        channel_id = self.handles.get(url) or url.rstrip("/").rsplit("/", 1)[-1]
        info = self.channels[channel_id]
        return ChannelInfo(info.channel_id, info.title, info.thumbnail,
                           list(info.videos) if videos else [])

    def channel_playlist_ids(self, url):
        self.calls.append(("channel_playlist_ids", url))
        self._check("channel_playlist_ids")
        return list(self.tabs.get(url.rstrip("/").rsplit("/", 1)[-1], []))

    def read_playlist(self, playlist_id, *, thumbnail=True):
        self.calls.append(("read_playlist", playlist_id))
        self._check("read_playlist")
        return self.playlists[playlist_id]

    def read_feed(self, channel_id):
        self.calls.append(("read_feed", channel_id))
        self._check("read_feed")
        return list(self.feeds.get(channel_id, []))


class FakeGate(ytgate.PassThroughGate):
    """Runs calls at once, remembering their kinds; ``paused_kinds`` refuse."""

    def __init__(self):
        self.kinds = []
        self.paused_kinds = set()

    def paused(self, kind):
        return kind in self.paused_kinds

    def call(self, kind, fn, *args, **kwargs):
        if kind in self.paused_kinds:
            raise ytgate.Paused(kind)
        self.kinds.append(kind)
        return super().call(kind, fn, *args, **kwargs)


class Clock:
    def __init__(self, start=datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def videos(start, count, *, duration=600.0, title="Video {n}"):
    return [VideoItem(video_id=vid(n), title=title.format(n=n), duration=duration)
            for n in range(start, start + count)]


def playlist(raw_id, video_numbers, title=None):
    return PlaylistInfo(playlist_id=raw_id, title=title or f"List {raw_id}",
                        thumbnail=f"https://i.ytimg.com/pl/{raw_id}.jpg",
                        videos=[VideoItem(video_id=vid(n), duration=300.0) for n in video_numbers])
