"""YouTube indexer built on top of tutubo's standalone Channel/Playlist/Video classes."""
from __future__ import annotations

import time
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

    def archive(self, url: str) -> None:
        if "/watch" in url or "youtu.be/" in url or "/shorts/" in url:
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

    def archive_playlist(self, url: str) -> None:
        from media_archivist.progress import progress
        self.log.debug("Archiving playlist: %s", url)
        playlist = Playlist(url)
        try:
            meta = {"playlist": playlist.title}
        except Exception:
            meta = {}
        for video in progress(playlist.videos, desc=f"playlist {url}", unit="vid"):
            try:
                self.archive_video(video, dict(meta))
            except VideoUnavailable:
                continue

    def archive_channel(self, url: str) -> None:
        from media_archivist.progress import progress
        self.log.debug("Archiving channel: %s", url)
        channel = Channel(url)
        for video in progress(channel.videos, desc=f"channel {url}", unit="vid"):
            try:
                self.archive_video(video, {})
            except VideoUnavailable:
                continue

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
