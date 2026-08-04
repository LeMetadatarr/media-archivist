"""Per-backend adapters from raw rows to :class:`MediaEntry`.

Each adapter is a pure function ``(raw_dict) -> MediaEntry``. The dispatcher
:func:`to_media_entry` picks the right one based on the row's ``source``
discriminator.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source


def _youtube(raw: Dict[str, Any]) -> MediaEntry:
    return MediaEntry.build(
        source=Source.YOUTUBE,
        url=raw["url"],
        title=raw.get("title"),
        raw=raw,
        artist=raw.get("author"),
        duration=raw.get("duration"),
        published=raw.get("published") or None,
        thumbnail=raw.get("thumbnail"),
        is_live=bool(raw.get("is_live")),
        tags=list(raw.get("tags") or []),
    )


def _youtube_music(raw: Dict[str, Any]) -> MediaEntry:
    return MediaEntry.build(
        source=Source.YOUTUBE_MUSIC,
        url=raw["url"],
        title=raw.get("title"),
        raw=raw,
        artist=raw.get("artist"),
        album=raw.get("album") or None,
        duration=raw.get("duration"),
        published=str(raw["year"]) if raw.get("year") else None,
        thumbnail=raw.get("thumbnail"),
        explicit=bool(raw.get("explicit")),
        tags=list(raw.get("tags") or []),
    )


def _bandcamp(raw: Dict[str, Any]) -> MediaEntry:
    return MediaEntry.build(
        source=Source.BANDCAMP,
        url=raw["url"],
        title=raw.get("title"),
        raw=raw,
        artist=raw.get("artist"),
        album=raw.get("album") or None,
        duration=raw.get("duration"),
        thumbnail=raw.get("thumbnail") or raw.get("artwork"),
        stream=raw.get("stream"),
        explicit=bool(raw.get("explicit")),
        tags=list(raw.get("tags") or []),
    )


def _soundcloud(raw: Dict[str, Any]) -> MediaEntry:
    return MediaEntry.build(
        source=Source.SOUNDCLOUD,
        url=raw["url"],
        title=raw.get("title"),
        raw=raw,
        artist=raw.get("artist") or None,
        duration=raw.get("duration"),
        thumbnail=raw.get("thumbnail"),
        stream=raw.get("stream"),
        explicit=bool(raw.get("explicit")),
        tags=list(raw.get("tags") or []),
    )


def _ia(raw: Dict[str, Any]) -> MediaEntry:
    duration = raw.get("duration")
    # IA returns "HH:MM:SS" runtime strings; parse to seconds when possible.
    parsed: float | None = None
    if isinstance(duration, (int, float)):
        parsed = float(duration)
    elif isinstance(duration, str) and duration.count(":") in (1, 2):
        try:
            parts = [int(p) for p in duration.split(":")]
            if len(parts) == 2:
                parsed = parts[0] * 60 + parts[1]
            else:
                parsed = parts[0] * 3600 + parts[1] * 60 + parts[2]
        except ValueError:
            parsed = None
    streams = raw.get("streams") or []
    return MediaEntry.build(
        source=Source.INTERNET_ARCHIVE,
        url=raw["url"],
        title=raw.get("title"),
        raw=raw,
        duration=parsed,
        thumbnail=(raw.get("images") or [None])[0],
        stream=streams[0] if streams else None,
        tags=list(raw.get("tags") or []),
    )


def _local(raw: Dict[str, Any]) -> MediaEntry:
    return MediaEntry.build(
        source=Source.LOCAL,
        url=raw["url"],
        title=raw.get("title"),
        raw=raw,
        artist=raw.get("artist"),
        album=raw.get("album") or None,
        duration=raw.get("duration"),
        published=raw.get("published") or None,
        thumbnail=raw.get("thumbnail"),
        stream=raw.get("path"),
        tags=list(raw.get("tags") or []),
    )


_ADAPTERS: Dict[Source, Callable[[Dict[str, Any]], MediaEntry]] = {
    Source.YOUTUBE: _youtube,
    Source.YOUTUBE_MUSIC: _youtube_music,
    Source.BANDCAMP: _bandcamp,
    Source.SOUNDCLOUD: _soundcloud,
    Source.INTERNET_ARCHIVE: _ia,
    Source.LOCAL: _local,
}


def to_media_entry(raw: Dict[str, Any]) -> MediaEntry:
    """Project a raw row to its canonical :class:`MediaEntry` shape."""
    src = Source(raw["source"])
    return _ADAPTERS[src](raw)
