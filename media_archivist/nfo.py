"""Kodi/Jellyfin ``.nfo`` sidecar generation.

Jellyfin (and Kodi) read a ``.nfo`` file sitting next to a media file
(or, for ``.strm`` remote-media stubs, next to the ``.strm``) to fill
in rich metadata instead of relying on filename scraping or a network
lookup. This module builds that XML from a :class:`MediaEntry` —
**metadata only**: it never downloads the thumbnail, it just writes
the URL into ``<thumb>`` and lets Jellyfin fetch it itself.

Two root elements are used, matching Jellyfin's NFO support:

* ``<musicvideo>`` for music sources (bandcamp, soundcloud, youtube_music)
* ``<movie>`` for everything else (youtube, internet_archive)
"""
from __future__ import annotations

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


def nfo_xml(entry: MediaEntry) -> str:
    """Build a well-formed Kodi/Jellyfin-compatible NFO XML string."""
    is_music = entry.source in _MUSIC_SOURCES
    root = "musicvideo" if is_music else "movie"

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
