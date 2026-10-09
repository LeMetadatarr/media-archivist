"""YouTube indexer built on top of tutubo's standalone Channel/Playlist/Video classes."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from queue import Queue
from threading import Event, Thread
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from tutubo.channel import Channel, Playlist, Video

from media_archivist.base import LOG, JsonArchivist
from media_archivist.exceptions import VideoUnavailable


def _video_id_from_url(url: str) -> str:
    """Extract a YouTube video id from a watch / youtu.be / shorts URL."""
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.lstrip("/")
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live"):
        return parts[1]
    raise ValueError(f"Could not extract video id from URL: {url}")


def _is_video_available(video_id: str, timeout: int = 10) -> bool:
    """Probe the public oEmbed endpoint — 200 OK means the video is still reachable."""
    try:
        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=timeout,
        )
    except requests.RequestException:
        return True  # network blip — keep the entry rather than risk false positives
    return resp.status_code == 200


@dataclass
class _FlatVideo:
    """A video described by a ``yt-dlp --flat-playlist`` record."""

    video_id: str
    title: str = ""
    length: Optional[float] = None
    author: Optional[str] = None
    thumbnail_url: str = ""
    description: str = ""
    view_count: str = ""

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CHANNEL_TABS = {"videos", "shorts", "streams", "live", "playlists", "releases", "featured", "podcasts", "community", "about", "search"}
_MAX_TAB_DEPTH = 2


def _channel_videos_url(url: str) -> str:
    """The Videos tab of a channel URL; any other URL is returned unchanged.

    ``yt-dlp --flat-playlist`` on a bare channel URL lists the channel's
    tabs (Videos, Live, Shorts), not its videos.
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or parsed.query:
        return url
    if parts[0].startswith("@"):
        base = 1
    elif parts[0] in ("channel", "c", "user") and len(parts) >= 2:
        base = 2
    else:
        return url
    if len(parts) > base and parts[base] in _CHANNEL_TABS:
        return url
    if len(parts) != base:
        return url
    return parsed._replace(path="/" + "/".join(parts) + "/videos").geturl()


def _flat_records(info: dict, depth: int = 0):
    """Yield the video records of a flat listing, descending into tabs and playlists."""
    from media_archivist import streams

    for rec in info.get("entries") or []:
        if not rec:
            continue
        if rec.get("entries") is not None:
            yield from _flat_records(rec, depth)
            continue
        is_tab = rec.get("ie_key") in ("YoutubeTab", "YoutubePlaylist") or rec.get("_type") == "playlist"
        if is_tab:
            if depth < _MAX_TAB_DEPTH and rec.get("url"):
                yield from _flat_records(streams.list_playlist(rec["url"]), depth + 1)
            continue
        if _VIDEO_ID.fullmatch(rec.get("id") or ""):
            yield rec


def _flat_videos(url: str) -> tuple:
    """List ``url`` through yt-dlp; returns ``(videos, playlist_title)``."""
    from media_archivist import streams

    info = streams.list_playlist(_channel_videos_url(url))
    videos = []
    seen = set()
    for rec in _flat_records(info):
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        thumbs = rec.get("thumbnails") or []
        videos.append(_FlatVideo(
            video_id=rec["id"],
            title=rec.get("title") or "",
            length=rec.get("duration"),
            author=rec.get("channel") or rec.get("uploader"),
            thumbnail_url=thumbs[-1].get("url", "") if thumbs else "",
            description=rec.get("description") or "",
            view_count=str(rec["view_count"]) if rec.get("view_count") is not None else "",
        ))
    return videos, info.get("title")


class YoutubeMonitor(Thread):
    """Background thread that periodically re-syncs a set of channel / playlist URLs."""

    def __init__(
        self,
        db_name: Optional[str] = None,
        required_kwords=None,
        blacklisted_kwords=None,
        min_duration: int = -1,
        logger=LOG,
        sync_interval: int = 120,
        repeat_min_gap: int = 30,
        db_path: Optional[str] = None,
    ) -> None:
        super().__init__(daemon=True)
        self.archive = YoutubeArchivist(
            db_name=db_name,
            required_kwords=required_kwords,
            blacklisted_kwords=blacklisted_kwords,
            min_duration=min_duration,
            db_path=db_path,
        )
        self.monitoring = Event()
        self.queue: "Queue[str]" = Queue()
        self.repeat_list: dict[str, float] = {}
        self.log = logger
        self.sync_interval = sync_interval
        self.repeat_min_gap = repeat_min_gap

    @property
    def db(self):
        return self.archive.db

    def sorted_entries(self):
        return self.archive.sorted_entries()

    def bootstrap_from_url(self, url: str) -> None:
        """Seed an empty database from a remote JSON dump."""
        if not self.archive.db:
            self.log.info("Bootstrapping database from: %s", url)
            self.archive.db.update(requests.get(url, timeout=30).json())
            self.archive.db.store()

    def _index_url(self, url: str) -> None:
        last = self.repeat_list.get(url)
        if last is not None and time.time() - last < self.repeat_min_gap:
            return
        if url in self.repeat_list:
            self.repeat_list[url] = time.time()
        self.archive.archive(url)

    def run(self) -> None:
        self.monitoring.set()
        self.log.info("Started monitoring: %s", self.archive.db.name)

        try:
            self.archive.remove_unavailable()
        except Exception:
            self.log.exception("remove_unavailable failed")

        while self.monitoring.is_set():
            url = self.queue.get()
            try:
                self._index_url(url)
            except Exception:
                self.log.exception("Failed to index %s", url)
            time.sleep(self.sync_interval)
            if url in self.repeat_list:
                self.queue.put(url)

    def sync(self, url: str) -> None:
        self.queue.put(url)

    def monitor(self, url: str) -> None:
        self.repeat_list.setdefault(url, 0.0)
        self.sync(url)

    def stop(self) -> None:
        self.monitoring.clear()


class YoutubeArchivist(JsonArchivist):
    """Index YouTube channels, playlists and individual videos into a JSON-backed DB."""

    entries_seen: Optional[int] = None
    """Videos the source listed during the last :meth:`archive` call,
    whether or not they were new; 0 for a playlist or channel means the
    source returned nothing. ``None`` until :meth:`archive` has run."""

    def archive(self, url: str) -> None:
        self.entries_seen = 0
        if "/watch" in url or "youtu.be/" in url or "/shorts/" in url:
            self.entries_seen = 1
            self.archive_video(url)
            return
        if "/playlist" in url or "list=" in url:
            self.archive_playlist(url)
            return
        # Default: treat as channel URL (handles /channel/, /c/, /@handle, etc.)
        self.archive_channel(url)

    def _passes_filters(self, title: str) -> bool:
        title_l = (title or "").lower()
        if any(k.lower() in title_l for k in self.blacklisted_kwords):
            return False
        if self.required_kwords and not all(k.lower() in title_l for k in self.required_kwords):
            return False
        return True

    def archive_video(self, video_or_url, extra_data: Optional[dict] = None) -> None:
        if isinstance(video_or_url, str):
            video = Video(_video_id_from_url(video_or_url))
        else:
            video = video_or_url

        if video.watch_url in self.video_urls:
            return

        title = video.title or ""
        if title and not self._passes_filters(title):
            return

        # Length filter — applies whenever the source exposes it
        # (VideoPreview from search, MusicTrack from YT Music). Bare
        # Channel/Playlist iterators don't, so the filter is a no-op there.
        if self.min_duration is not None and self.min_duration > 0:
            length = getattr(video, "length", None)
            if length is not None and length < self.min_duration:
                return

        self.log.debug("Archiving video: %s", video.watch_url)
        self._update_video(video, extra_data)

    def _list_videos(self, url: str, source) -> tuple:
        """Videos of a playlist / channel, as ``(videos, playlist_title)``.

        tutubo parses YouTube's page markup and returns nothing, without an
        error, when that markup changes. An empty or failing tutubo listing
        is retried through ``yt-dlp --flat-playlist``.
        """
        try:
            videos = list(source.videos)
        except Exception as e:
            self.log.warning("tutubo failed to list %s (%s)", url, e)
            videos = []
        title = None
        if videos:
            try:
                title = getattr(source, "title", None)
            except Exception:
                title = None
            return videos, title
        self.log.warning("tutubo returned no videos for %s; falling back to yt-dlp", url)
        try:
            return _flat_videos(url)
        except Exception as e:
            self.log.warning("yt-dlp fallback failed for %s: %s", url, e)
            return [], None

    def _archive_listing(self, url: str, videos, meta: dict, desc: str) -> None:
        from media_archivist.progress import progress
        self.entries_seen = len(videos)
        for video in progress(videos, desc=desc, unit="vid"):
            try:
                self.archive_video(video, dict(meta))
            except VideoUnavailable:
                continue

    def archive_playlist(self, url: str) -> None:
        self.log.debug("Archiving playlist: %s", url)
        videos, title = self._list_videos(url, Playlist(url))
        meta = {"playlist": title} if title else {}
        self._archive_listing(url, videos, meta, f"playlist {url}")

    def archive_channel(self, url: str) -> None:
        self.log.debug("Archiving channel: %s", url)
        videos, _ = self._list_videos(url, Channel(url))
        self._archive_listing(url, videos, {}, f"channel {url}")

    def archive_channel_playlists(self, url: str) -> None:
        from media_archivist.progress import progress
        self.log.debug("Archiving channel playlists: %s", url)
        channel = Channel(url)
        for playlist in progress(channel.playlists, desc=f"playlists {url}", unit="pl"):
            try:
                meta = {"playlist": playlist.title}
            except Exception:
                meta = {}
            for video in progress(playlist.videos, desc=f"  pl {meta.get('playlist','')}", unit="vid"):
                try:
                    self.archive_video(video, dict(meta))
                except VideoUnavailable:
                    continue

    def _update_video(self, video, extra_data: Optional[dict] = None) -> None:
        from media_archivist.models import RawYoutubeEntry

        url = video.watch_url
        length = getattr(video, "length", None)
        author = getattr(video, "author", None)
        playlist = (extra_data or {}).get("playlist")
        unknown_extras = {k: v for k, v in (extra_data or {}).items() if k != "playlist"}
        entry = RawYoutubeEntry(
            url=url,
            videoId=video.video_id,
            title=video.title,
            tags=list(getattr(video, "keywords", None) or []) + list(getattr(video, "tags", []) or []),
            thumbnail=video.thumbnail_url,
            is_live=bool(getattr(video, "is_live", False)),
            published=getattr(video, "published_time", "") or "",
            views=getattr(video, "view_count", "") or getattr(video, "views", "") or "",
            description=getattr(video, "description", "") or "",
            duration=length,
            author=author or None,
            playlist=playlist,
            extra=unknown_extras,
        )
        self.db[url] = entry.model_dump(mode="json")
        self.db.store()

    def remove_unavailable(self) -> None:
        """Drop entries whose videos no longer resolve via the oEmbed endpoint."""
        from media_archivist.progress import progress
        keys = list(self.db.keys())
        to_remove: list[str] = []
        for url in progress(keys, desc="checking availability", total=len(keys), unit="url"):
            try:
                video_id = _video_id_from_url(url)
            except ValueError:
                to_remove.append(url)
                continue
            if not _is_video_available(video_id):
                to_remove.append(url)
        for url in to_remove:
            self.db.pop(url)
            self.log.info("Removed entry: %s", url)
        if to_remove:
            self.db.store()
