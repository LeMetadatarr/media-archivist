"""YouTube Music archivist — index songs, albums, artists and playlists.

Built on top of :mod:`tutubo.ytmus`, which wraps ``ytmusicapi``. Each archived
entry stores rich music metadata (artist, album, year, duration, explicit
flag, video type, thumbnail) that the regular YouTube channel scraper can't
provide.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qs, urlparse

from tutubo.ytmus import (
    MusicAlbum,
    MusicArtist,
    MusicPlaylist,
    MusicTrack,
    MusicVideo,
    _get_ytmus,
    search_yt_music,
)

from media_archivist.base import LOG, JsonArchivist


def _playlist_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "list" in qs:
        return qs["list"][0]
    raise ValueError(f"No playlist id in URL: {url}")


def _browse_id_from_url(url: str) -> str:
    """Extract a YT Music browseId (e.g. ``MPREb_...`` for albums, ``UC...`` for artists)."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    # https://music.youtube.com/browse/MPREb_xxx  or  /channel/UCxxx
    for marker in ("browse", "channel"):
        if marker in parts:
            i = parts.index(marker)
            if i + 1 < len(parts):
                return parts[i + 1]
    raise ValueError(f"No browse/channel id in URL: {url}")


class YoutubeMusicArchivist(JsonArchivist):
    """Index YouTube Music tracks, albums, artists and playlists into a JSON DB."""

    def __init__(
        self,
        db_name: Optional[str] = None,
        required_kwords=None,
        blacklisted_kwords=None,
        min_duration: int = -1,
        logger=LOG,
        db_path: Optional[str] = None,
        *,
        skip_explicit: bool = False,
        only_audio: bool = False,
    ) -> None:
        super().__init__(
            db_name=db_name,
            required_kwords=required_kwords,
            blacklisted_kwords=blacklisted_kwords,
            min_duration=min_duration,
            logger=logger,
            db_path=db_path,
        )
        self.skip_explicit = skip_explicit
        self.only_audio = only_audio

    # ------------------------------------------------------------------
    # Public archive entry points
    # ------------------------------------------------------------------

    def archive(self, url_or_query: str) -> None:
        """Dispatch on input shape: URL vs free-text query."""
        if "music.youtube.com" in url_or_query or "youtube.com" in url_or_query:
            if "playlist?" in url_or_query or "list=" in url_or_query:
                return self.archive_playlist(url_or_query)
            if "/browse/" in url_or_query:
                return self.archive_album(_browse_id_from_url(url_or_query))
            if "/channel/" in url_or_query:
                return self.archive_artist(_browse_id_from_url(url_or_query))
            if "watch?" in url_or_query:
                # Single track URL — fall through to search by videoId
                vid = parse_qs(urlparse(url_or_query).query).get("v", [""])[0]
                if vid:
                    return self.archive_video_id(vid)
        return self.archive_search(url_or_query)

    def archive_search(self, query: str, max_results: int = -1) -> None:
        """Run a YT Music search and archive every track / video result."""
        self.log.debug("YT Music search: %s", query)
        count = 0
        for raw in search_yt_music(query, as_dict=False):
            if isinstance(raw, (MusicTrack, MusicVideo)):
                if self._archive_track(raw, extra_data={"source_query": query}):
                    count += 1
            if 0 < max_results <= count:
                break

    def archive_playlist(self, url: str) -> None:
        ytm = _get_ytmus()
        if not ytm:
            self.log.warning("Could not connect to YT Music")
            return
        pid = _playlist_id_from_url(url)
        self.log.debug("YT Music playlist: %s", pid)
        data = ytm.get_playlist(pid)
        playlist = MusicPlaylist({**data, "playlistId": pid})
        meta = {"playlist": playlist.title or "", "playlist_id": pid}
        from media_archivist.progress import progress
        for track in progress(playlist.tracks, desc=f"playlist {pid}", unit="trk"):
            self._archive_track(track, extra_data=dict(meta))

    def archive_album(self, browse_id: str) -> None:
        from tutubo.ytmus import get_album

        data = get_album(browse_id)
        album = MusicAlbum(data)
        meta = {
            "album": album.title or "",
            "album_browse_id": browse_id,
            "year": album.year,
            "label": album.label,
        }
        from media_archivist.progress import progress
        for track in progress(album.tracks, desc=f"album {browse_id}", unit="trk"):
            self._archive_track(track, extra_data=dict(meta))

    def archive_artist(self, browse_id: str) -> None:
        ytm = _get_ytmus()
        if not ytm:
            self.log.warning("Could not connect to YT Music")
            return
        artist = MusicArtist(ytm.get_artist(browse_id))
        meta = {"artist_browse_id": browse_id, "artist": artist.name}
        from media_archivist.progress import progress
        for track in progress(artist.tracks, desc=f"artist {browse_id}", unit="trk"):
            self._archive_track(track, extra_data=dict(meta))

    def archive_video_id(self, video_id: str) -> None:
        ytm = _get_ytmus()
        if not ytm:
            return
        try:
            song = ytm.get_song(video_id)
            details = song.get("videoDetails", {})
            track = MusicTrack({
                "videoId": video_id,
                "title": details.get("title"),
                "artist": details.get("author"),
                "duration_seconds": int(details.get("lengthSeconds") or 0) or None,
                "thumbnails": details.get("thumbnail", {}).get("thumbnails", []),
            })
            self._archive_track(track)
        except Exception:
            self.log.exception("Failed to fetch YT Music song %s", video_id)

    # ------------------------------------------------------------------
    # Internal — apply filters and persist
    # ------------------------------------------------------------------

    def _archive_track(self, track, extra_data: Optional[dict] = None) -> bool:
        url = track.watch_url
        if not url or url in self.db:
            return False

        title = track.title or ""
        title_l = title.lower()
        if any(k.lower() in title_l for k in self.blacklisted_kwords):
            return False
        if self.required_kwords and not all(k.lower() in title_l for k in self.required_kwords):
            return False

        length = track.length
        if self.min_duration and self.min_duration > 0 and length is not None:
            if length < self.min_duration:
                return False

        if self.skip_explicit and getattr(track, "is_explicit", False):
            return False
        if self.only_audio and not getattr(track, "is_audio_only", False):
            return False

        from media_archivist.models import RawYoutubeMusicEntry

        known_keys = {"playlist", "playlist_id", "album", "album_browse_id",
                      "year", "label", "artist", "artist_browse_id"}
        carried = {k: v for k, v in (extra_data or {}).items() if k in known_keys}
        unknown_extras = {k: v for k, v in (extra_data or {}).items() if k not in known_keys}
        entry = RawYoutubeMusicEntry(
            url=url,
            videoId=track.video_id,
            title=title,
            artist=carried.get("artist") or track.artist,
            album=carried.get("album") or getattr(track, "album", "") or "",
            year=carried.get("year") if carried.get("year") is not None else getattr(track, "year", None),
            duration=length,
            thumbnail=track.thumbnail_url,
            explicit=bool(getattr(track, "is_explicit", False)),
            video_type=getattr(track, "video_type", "") or "",
            audio_only=bool(getattr(track, "is_audio_only", False)),
            music_video=bool(getattr(track, "is_music_video", False)),
            views=getattr(track, "views", "") or "",
            playlist=carried.get("playlist"),
            playlist_id=carried.get("playlist_id"),
            album_browse_id=carried.get("album_browse_id"),
            artist_browse_id=carried.get("artist_browse_id"),
            label=carried.get("label"),
            extra=unknown_extras,
        )
        self.db[url] = entry.model_dump(mode="json")
        self.db.store()
        return True
