"""Kodi/Jellyfin ``.nfo`` sidecar generation.

Jellyfin (and Kodi) read a ``.nfo`` file sitting next to a media file
(or, for ``.strm`` remote-media stubs, next to the ``.strm``) to fill
in rich metadata instead of relying on filename scraping or a network
lookup. This module builds that XML from a :class:`MediaEntry` —
**metadata only**: it never downloads the thumbnail, it just writes
the URL into ``<thumb>`` and lets Jellyfin fetch it itself.

Three root elements are used, matching Jellyfin's NFO support:

* ``<musicvideo>`` for music sources (bandcamp, soundcloud, youtube_music,
  and local audio files)
* ``<episodedetails>`` for local files identified as a TV/episodic-series
  episode (``entry.raw["season"]``/``entry.raw["episode"]`` set)
* ``<movie>`` for everything else (youtube, internet_archive, local movies)
"""
from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape

from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source

_MUSIC_SOURCES = {Source.BANDCAMP, Source.SOUNDCLOUD, Source.YOUTUBE_MUSIC}


def _tag(name: str, value) -> str:
    return f"  <{name}>{escape(str(value))}</{name}>"


def _year_and_premiered(published: str | None) -> tuple[str | None, str | None]:
    if not published:
        return None, None
    year = published[:4]
    if not year.isdigit():
        return None, None
    premiered = published[:10] if len(published) >= 10 else published
    return year, premiered


def _runtime_minutes(duration: float | None) -> int | None:
    if not duration or duration <= 0:
        return None
    minutes = round(duration / 60)
    return minutes if minutes > 0 else 1


def _plot(entry: MediaEntry) -> str:
    if entry.raw.get("description"):
        return str(entry.raw["description"])
    bits = [entry.title]
    if entry.artist:
        bits.append(f"by {entry.artist}")
    bits.append(f"({entry.source.value})")
    return " ".join(bits)


def _is_music(entry: MediaEntry) -> bool:
    if entry.source in _MUSIC_SOURCES:
        return True
    if entry.source == Source.LOCAL:
        return str((entry.raw or {}).get("media_type") or "").lower() in (
            "music", "audio",
        )
    return False


def _episode_info(entry: MediaEntry) -> tuple[Optional[int], Optional[int]]:
    """Return (season, episode) when *entry* is a local episodic-series row."""
    if entry.source != Source.LOCAL:
        return None, None
    raw = entry.raw or {}
    season = raw.get("season")
    episode = raw.get("episode")
    if season is None and episode is None:
        return None, None
    return season, episode


def nfo_xml(entry: MediaEntry) -> str:
    """Build a well-formed Kodi/Jellyfin-compatible NFO XML string."""
    is_music = _is_music(entry)
    season, episode = (None, None) if is_music else _episode_info(entry)
    is_episode = season is not None or episode is not None
    if is_music:
        root = "musicvideo"
    elif is_episode:
        root = "episodedetails"
    else:
        root = "movie"

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             f"<{root}>"]
    lines.append(_tag("title", entry.title or entry.url))
    lines.append(_tag("plot", _plot(entry)))

    if is_music and entry.artist:
        lines.append(_tag("artist", entry.artist))
    if is_music and entry.album:
        lines.append(_tag("album", entry.album))
    if not is_music and entry.artist:
        lines.append(_tag("studio", entry.artist))

    if is_episode:
        if season is not None:
            lines.append(_tag("season", season))
        if episode is not None:
            lines.append(_tag("episode", episode))

    for t in entry.tags:
        lines.append(_tag("genre", t))
        lines.append(_tag("tag", t))

    runtime = _runtime_minutes(entry.duration)
    if runtime is not None:
        lines.append(_tag("runtime", runtime))

    if entry.thumbnail:
        lines.append(_tag("thumb", entry.thumbnail))

    year, premiered = _year_and_premiered(entry.published)
    if premiered:
        lines.append(_tag("premiered", premiered))
    if year:
        lines.append(_tag("year", year))

    if not is_music:
        for scheme, value in (entry.external_ids.model_dump() or {}).items():
            if value:
                lines.append(
                    f'  <uniqueid type="{escape(str(scheme))}">'
                    f'{escape(str(value))}</uniqueid>'
                )

    lines.append(f"</{root}>")
    return "\n".join(lines) + "\n"
