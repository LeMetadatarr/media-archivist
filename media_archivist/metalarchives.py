"""Encyclopaedia Metallum backend — index bands, albums and songs from
``metal-archives.com`` via :mod:`pymetal`.

Each archived row is a *song* (the playable unit), even when the archive
call started at band or album level. The metal-archives URL is the row
key; band / album metadata rides along on the row.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from media_archivist.base import LOG, JsonArchivist
from media_archivist.progress import progress

_LENGTH_RE = re.compile(r"(?:(\d+):)?(\d+):(\d+)$")


def _length_to_seconds(value: Optional[str]) -> Optional[float]:
    """Convert ``MM:SS`` / ``HH:MM:SS`` strings from MA into seconds."""
    if not value:
        return None
    match = _LENGTH_RE.search(value.strip())
    if not match:
        return None
    h, m, s = match.groups()
    total = int(m) * 60 + int(s)
    if h:
        total += int(h) * 3600
    return float(total)


class MetalArchivesArchivist(JsonArchivist):
    """Index Encyclopaedia Metallum bands / albums / songs as song rows."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from pymetal import MetalArchives  # noqa: WPS433
        self._client = MetalArchives()

    # ------------------------------------------------------------------
    # Public archive entry points
    # ------------------------------------------------------------------

    def archive(self, url_or_query: str) -> None:
        """Dispatch on input shape: URL vs free-text query."""
        if "metal-archives.com" in url_or_query:
            return self.archive_url(url_or_query)
        return self.archive_search(url_or_query)

    def archive_url(self, url: str) -> None:
        if "/bands/" in url:
            return self._archive_band_url(url)
        if "/albums/" in url:
            return self._archive_release_url(url)
        self.log.warning("metal-archives URL not recognised: %s", url)

    def archive_search(self, query: str, limit: int = 25) -> None:
        """Search bands → walk discography → archive every song."""
        self.log.debug("metal-archives band search: %s", query)
        hits = list(self._client.search_bands(band_name=query))[:limit]
        for hit in progress(hits, desc=f"metal-archives '{query}'", unit="band"):
            try:
                self._archive_band_id(hit.ma_id)
            except Exception:
                self.log.exception("failed to archive band %s", hit.ma_id)

    def archive_band(self, band_id: int) -> None:
        self._archive_band_id(band_id)

    def archive_album(self, release_id: int) -> None:
        self._archive_release_id(release_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _archive_band_url(self, url: str) -> None:
        band = self._client.get_band_by_url(url)
        if band.ma_id is None:
            self.log.warning("could not resolve band id for %s", url)
            return
        self._archive_band_id(band.ma_id)

    def _archive_band_id(self, band_id: int) -> None:
        band = self._client.get_band(band_id)
        for release in self._client.get_discography(band_id):
            self._archive_release(band, release)

    def _archive_release_url(self, url: str) -> None:
        # MA URL pattern: /albums/<band_slug>/<release_slug>/<release_id>
        try:
            release_id = int(url.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            self.log.warning("could not parse release id from %s", url)
            return
        self._archive_release_id(release_id)

    def _archive_release_id(self, release_id: int) -> None:
        release, songs, _ = self._client.get_release(release_id)
        band: Optional[object] = None
        if release.band_ids:
            try:
                band = self._client.get_band(release.band_ids[0])
            except Exception:
                self.log.exception("failed to fetch band for release %s", release_id)
        for song in songs:
            self._upsert_song(band=band, release=release, song=song)

    def _archive_release(self, band, release) -> None:
        if release.ma_id is None:
            return
        try:
            release_full, songs, _ = self._client.get_release(release.ma_id)
        except Exception:
            self.log.exception("failed to fetch release %s", release.ma_id)
            return
        for song in progress(songs, desc=f"  release {release_full.title}", unit="song"):
            self._upsert_song(band=band, release=release_full, song=song)

    def _upsert_song(self, *, band, release, song) -> None:
        from media_archivist.models import RawMetalArchivesEntry

        if song.ma_id is None or release is None:
            return

        url = (f"https://www.metal-archives.com/release.php"
               f"?releaseID={release.ma_id}&songID={song.ma_id}")

        title = (song.title or "").strip()
        if not title:
            return

        # Apply title-level filters before constructing the model.
        title_l = title.lower()
        if any(k.lower() in title_l for k in self.blacklisted_kwords):
            return
        if self.required_kwords and not all(k.lower() in title_l for k in self.required_kwords):
            return

        duration = _length_to_seconds(song.length)
        if self.min_duration and self.min_duration > 0 and duration is not None:
            if duration < self.min_duration:
                return

        if url in self.video_urls:
            return

        entry = RawMetalArchivesEntry(
            url=url,
            title=title,
            artist=getattr(band, "name", None),
            album=release.title,
            band_id=getattr(band, "ma_id", None),
            release_id=release.ma_id,
            song_id=song.ma_id,
            duration=duration,
            length=song.length,
            release_date=release.release_date,
            release_type=getattr(release.type, "value", str(release.type)) if release.type else None,
            country=getattr(band, "country", None),
            genres=list(getattr(band, "genres", []) or []),
            themes=list(getattr(band, "themes", []) or []),
            label_id=release.label_id,
            label_name=release.label_name,
            cover_url=str(release.cover_url) if release.cover_url else None,
            band_url=str(band.url) if (band and band.url) else None,
            tags=list(getattr(band, "genres", []) or []),
            thumbnail=str(release.cover_url) if release.cover_url else None,
        )
        self.db[url] = entry.model_dump(mode="json")
        self.db.store()
