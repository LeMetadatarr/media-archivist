"""Bandcamp archivist — index tracks, albums and artists via ``py_bandcamp``."""
from __future__ import annotations

from typing import Optional

from py_bandcamp import BandCamp, BandcampAlbum, BandcampArtist, BandcampTrack

from media_archivist.base import LOG, JsonArchivist


class BandcampArchivist(JsonArchivist):
    """Index Bandcamp tracks, albums and artists into a JSON DB.

    Accepts either Bandcamp URLs or free-text queries. Each entry stores
    artist, album, track number, duration (seconds), artwork URL and the
    direct stream URL when available.
    """

    def __init__(
        self,
        db_name: Optional[str] = None,
        required_kwords=None,
        blacklisted_kwords=None,
        min_duration: int = -1,
        logger=LOG,
        db_path: Optional[str] = None,
    ) -> None:
        super().__init__(
            db_name=db_name,
            required_kwords=required_kwords,
            blacklisted_kwords=blacklisted_kwords,
            min_duration=min_duration,
            logger=logger,
            db_path=db_path,
        )

    def archive(self, url_or_query: str) -> None:
        if "bandcamp.com" in url_or_query:
            if "/track/" in url_or_query:
                self._archive_track(BandcampTrack.from_url(url_or_query))
            elif "/album/" in url_or_query:
                self.archive_album(url_or_query)
            else:
                self.archive_artist(url_or_query)
            return
        self.archive_search(url_or_query)

    def archive_search(self, query: str, max_results: int = -1) -> None:
        self.log.debug("Bandcamp search: %s", query)
        from media_archivist.progress import progress
        count = 0
        for hit in progress(BandCamp.search_tracks(query), desc=f"bandcamp '{query}'",
                            unit="hit"):
            if isinstance(hit, BandcampTrack):
                if self._archive_track(hit, extra_data={"source_query": query}):
                    count += 1
            if 0 < max_results <= count:
                break

    def archive_album(self, url: str) -> None:
        album = BandcampAlbum.from_url(url)
        meta = {
            "album": album.title or "",
            "album_url": album.url,
            "artist": album.artist,
            "artwork": album.image,
        }
        from media_archivist.progress import progress
        for track in progress(album.tracks, desc=f"album {album.title}", unit="trk"):
            self._archive_track(track, extra_data=dict(meta))

    def archive_artist(self, url: str) -> None:
        from media_archivist.progress import progress
        artist = BandcampArtist.from_url(url)
        for album in progress(artist.albums, desc=f"artist {url}", unit="alb"):
            try:
                self.archive_album(album.url)
            except Exception:
                self.log.exception("Failed to archive album %s", album.url)

    def _archive_track(self, track: BandcampTrack, extra_data: Optional[dict] = None) -> bool:
        url = track.url
        if not url or url in self.db:
            return False

        title = track.title or ""
        title_l = title.lower()
        if any(k.lower() in title_l for k in self.blacklisted_kwords):
            return False
        if self.required_kwords and not all(k.lower() in title_l for k in self.required_kwords):
            return False

        duration = track.duration  # seconds (float on Bandcamp)
        if self.min_duration and self.min_duration > 0 and duration is not None:
            if duration < self.min_duration:
                return False

        from media_archivist.models import RawBandcampEntry

        def _str_or_none(value) -> Optional[str]:
            """py_bandcamp returns BandcampArtist/Album objects from .artist/.album."""
            if value is None:
                return None
            if isinstance(value, str):
                return value
            for attr in ("name", "title"):
                got = getattr(value, attr, None)
                if isinstance(got, str) and got:
                    return got
            return str(value)

        known_keys = {"album", "album_url", "artist", "artwork"}
        carried = {k: v for k, v in (extra_data or {}).items() if k in known_keys}
        unknown_extras = {k: v for k, v in (extra_data or {}).items() if k not in known_keys}
        entry = RawBandcampEntry(
            url=url,
            title=title,
            artist=_str_or_none(carried.get("artist")) or _str_or_none(track.artist),
            album=_str_or_none(carried.get("album")) or _str_or_none(getattr(track, "album", "")) or "",
            album_url=carried.get("album_url"),
            track_number=getattr(track, "track_num", None),
            duration=duration,
            thumbnail=track.image,
            stream=track.stream,
            artwork=carried.get("artwork"),
            extra=unknown_extras,
        )
        self.db[url] = entry.model_dump(mode="json")
        self.db.store()
        return True
