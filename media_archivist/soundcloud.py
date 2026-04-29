"""SoundCloud archivist — index tracks, sets and artists via ``nuvem_de_som``."""
from __future__ import annotations

from typing import Optional

from nuvem_de_som import SoundCloud

from media_archivist.base import LOG, JsonArchivist


class SoundCloudArchivist(JsonArchivist):
    """Index SoundCloud tracks, sets and artists into a JSON DB.

    Accepts SoundCloud track / set / profile URLs, or free-text queries.
    Each track entry stores title, artist, artist URL, duration (ms), artwork.
    """

    def __init__(
        self,
        db_name: Optional[str] = None,
        required_kwords=None,
        blacklisted_kwords=None,
        min_duration: int = -1,
        logger=LOG,
        db_path: Optional[str] = None,
        *,
        backend: Optional[SoundCloud] = None,
        resolve_streams: bool = False,
    ) -> None:
        super().__init__(
            db_name=db_name,
            required_kwords=required_kwords,
            blacklisted_kwords=blacklisted_kwords,
            min_duration=min_duration,
            logger=logger,
            db_path=db_path,
        )
        self.client = backend or SoundCloud()
        self.resolve_streams = resolve_streams

    def archive(self, url_or_query: str) -> None:
        if "soundcloud.com" in url_or_query:
            self.archive_url(url_or_query)
        else:
            self.archive_search(url_or_query)

    def archive_search(self, query: str, limit: int = 20) -> None:
        from media_archivist.progress import progress
        self.log.debug("SoundCloud search: %s", query)
        for track in progress(self.client.search_tracks(query, limit=limit),
                              desc=f"soundcloud '{query}'", total=limit, unit="trk"):
            self._archive_track(track, extra_data={"source_query": query})

    def archive_url(self, url: str) -> None:
        """Archive a track URL, a set URL or an artist profile URL."""
        from media_archivist.progress import progress
        for track in progress(self.client.get_tracks(url), desc=f"soundcloud {url}",
                              unit="trk"):
            self._archive_track(track, extra_data={"source_url": url})

    def _archive_track(self, track: dict, extra_data: Optional[dict] = None) -> bool:
        url = track.get("url") or ""
        if not url or url in self.db:
            return False

        title = track.get("title") or ""
        title_l = title.lower()
        if any(k.lower() in title_l for k in self.blacklisted_kwords):
            return False
        if self.required_kwords and not all(k.lower() in title_l for k in self.required_kwords):
            return False

        duration_ms = track.get("duration")  # nuvem_de_som returns ms or None
        duration_s = duration_ms / 1000 if isinstance(duration_ms, (int, float)) else None
        if self.min_duration and self.min_duration > 0 and duration_s is not None:
            if duration_s < self.min_duration:
                return False

        from media_archivist.models import RawSoundcloudEntry

        stream: Optional[str] = None
        if self.resolve_streams:
            try:
                stream = self.client.resolve_stream(url)
            except Exception:
                self.log.exception("Failed to resolve stream for %s", url)

        known_keys = {"source_query", "source_url"}
        carried = {k: v for k, v in (extra_data or {}).items() if k in known_keys}
        unknown_extras = {k: v for k, v in (extra_data or {}).items() if k not in known_keys}
        entry = RawSoundcloudEntry(
            url=url,
            title=title,
            artist=track.get("artist") or "",
            artist_url=track.get("artist_url") or "",
            thumbnail=track.get("image") or "",
            duration=duration_s,
            stream=stream,
            source_query=carried.get("source_query"),
            source_url=carried.get("source_url"),
            extra=unknown_extras,
        )
        self.db[url] = entry.model_dump(mode="json")
        self.db.store()
        return True
