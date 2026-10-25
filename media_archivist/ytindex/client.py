# SPDX-License-Identifier: Apache-2.0
"""What the indexer asks YouTube, one function per request it makes.

Listings, playlists and pictures come from tutubo; the channel feed is one
plain HTTP request. Nothing here retries, waits or knows about back-off:
each function is the body of one call through :mod:`media_archivist.ytgate`.
The results are plain records, so the indexer can be tested with a fake
client that returns them.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
FEED_TIMEOUT_S = 30

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass
class VideoItem:
    video_id: str
    title: Optional[str] = None
    duration: Optional[float] = None
    published: Optional[str] = None
    thumbnail: Optional[str] = None
    vertical: Optional[bool] = None


@dataclass
class ChannelInfo:
    channel_id: str
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    videos: List[VideoItem] = field(default_factory=list)


@dataclass
class PlaylistInfo:
    playlist_id: str
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    videos: List[VideoItem] = field(default_factory=list)


def video_id_from_url(url: str) -> Optional[str]:
    """The video id of a watch, ``youtu.be``, ``/shorts/``, ``/live/`` or ``/embed/`` URL."""
    parsed = urlparse(url or "")
    if parsed.netloc.endswith("youtu.be"):
        vid = parsed.path.strip("/")
    else:
        vid = (parse_qs(parsed.query).get("v") or [""])[0]
        if not vid:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live"):
                vid = parts[1]
    return vid if _VIDEO_ID.match(vid or "") else None


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def channel_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"


def _attr(obj, name):
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _seconds(value) -> Optional[float]:
    from media_archivist.ytmeta import clean_duration

    return clean_duration(value)


def _video(v) -> Optional[VideoItem]:
    vid = _attr(v, "video_id")
    if not isinstance(vid, str) or not _VIDEO_ID.match(vid):
        return None
    return VideoItem(video_id=vid, title=_attr(v, "title") or None,
                     duration=_seconds(_attr(v, "duration_seconds")))


def _videos(source) -> List[VideoItem]:
    out, seen = [], set()
    for v in source.videos:
        item = _video(v)
        if item and item.video_id not in seen:
            seen.add(item.video_id)
            out.append(item)
    return out


def read_channel(url: str, *, videos: bool = False) -> ChannelInfo:
    """A channel's id, name and picture, and with ``videos`` its whole Videos tab."""
    from media_archivist import ytclient

    channel = ytclient.CLIENT.channel(url)
    channel_id = channel.channel_id
    if not channel_id:
        raise LookupError(f"no channel id found at {url}")
    return ChannelInfo(channel_id=channel_id, title=channel.channel_name or None,
                       thumbnail=channel.thumbnail_url or None,
                       videos=_videos(channel) if videos else [])


def channel_playlist_ids(url: str) -> List[str]:
    """The playlist ids on a channel's Playlists tab, in the tab's order."""
    from media_archivist import ytclient

    ids = []
    for u in ytclient.CLIENT.channel(url).playlist_urls:
        pid = (parse_qs(urlparse(u).query).get("list") or [""])[0]
        if pid and pid not in ids:
            ids.append(pid)
    return ids


def read_playlist(playlist_id: str, *, thumbnail: bool = True) -> PlaylistInfo:
    """A playlist's title, picture and video ids in playlist order."""
    from media_archivist import ytclient

    playlist = ytclient.CLIENT.playlist(playlist_url(playlist_id))
    videos = _videos(playlist)
    return PlaylistInfo(playlist_id=playlist_id, title=playlist.title or None,
                        thumbnail=(playlist.thumbnail_url or None) if thumbnail else None,
                        videos=videos)


def parse_feed(xml_text: str) -> List[VideoItem]:
    """The uploads in a channel's Atom feed, newest first as the feed lists them.

    A Short's link in the feed is ``/shorts/<id>``; the item keeps only the
    id, and is marked vertical.
    """
    root = ET.fromstring(xml_text)
    items = []
    for entry in root.findall("atom:entry", _NS):
        link = entry.find("atom:link[@rel='alternate']", _NS)
        href = link.get("href", "") if link is not None else ""
        vid = (entry.findtext("yt:videoId", "", _NS) or "").strip() or video_id_from_url(href)
        if not vid or not _VIDEO_ID.match(vid):
            continue
        thumb = entry.find("media:group/media:thumbnail", _NS)
        published = (entry.findtext("atom:published", "", _NS) or "").strip()
        items.append(VideoItem(
            video_id=vid,
            title=(entry.findtext("atom:title", "", _NS) or "").strip() or None,
            published=published[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", published) else None,
            thumbnail=thumb.get("url") if thumb is not None else None,
            vertical=True if "/shorts/" in href else None,
        ))
    return items


def read_feed(channel_id: str) -> List[VideoItem]:
    """One request for the channel's feed: its latest uploads."""
    import requests

    resp = requests.get(FEED_URL.format(channel_id=channel_id), timeout=FEED_TIMEOUT_S)
    resp.raise_for_status()
    return parse_feed(resp.text)


class TutuboClient:
    """The client the indexer uses unless it is given another."""

    read_channel = staticmethod(read_channel)
    channel_playlist_ids = staticmethod(channel_playlist_ids)
    read_playlist = staticmethod(read_playlist)
    read_feed = staticmethod(read_feed)
