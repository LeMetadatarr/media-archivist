"""Kodi/Jellyfin ``.nfo`` sidecar generation.

Jellyfin (and Kodi) read a ``.nfo`` file sitting next to a media file
(or, for ``.strm`` remote-media stubs, next to the ``.strm``) to fill
in rich metadata instead of relying on filename scraping or a network
lookup. This module builds that XML from a :class:`MediaEntry` —
**metadata only**: it never downloads the thumbnail, it just writes
the URL into ``<thumb>`` and lets Jellyfin fetch it itself.

Used by ``strm-export --nfo`` to serialize media-archivist's own already-known
index entries when exporting the stream library to Jellyfin — no re-resolution,
no re-tagging of existing files (that job lives in ``metadatarr tag-library``).

Two root elements are used, matching Jellyfin's NFO support:

* ``<musicvideo>`` for music sources (bandcamp, soundcloud, youtube_music)
* ``<movie>`` for everything else (youtube, internet_archive)

# TODO: dedupe with metadatarr.nfo once released
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
    return entry.source in _MUSIC_SOURCES


# mediavocab id field -> the scheme name Jellyfin / Kodi read.
_SCHEME_NAMES = {"tmdb_movie": "tmdb"}


def _unique_ids(entry: MediaEntry) -> list[tuple[str, str]]:
    """``(scheme, value)`` pairs for a ``<movie>`` NFO: the YouTube video id
    when the entry has one, then every scalar external id (imdb, tmdb, ...)."""
    ids = []
    video_id = entry.raw.get("videoId")
    if entry.source is Source.YOUTUBE and video_id:
        ids.append(("youtube", str(video_id)))
    for scheme, value in entry.external_ids.model_dump().items():
        if isinstance(value, (str, int)) and value != "":
            ids.append((_SCHEME_NAMES.get(scheme, scheme), str(value)))
    return ids


def nfo_xml(entry: MediaEntry) -> str:
    """Build a well-formed Kodi/Jellyfin-compatible NFO XML string."""
    is_music = _is_music(entry)
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
        for scheme, value in _unique_ids(entry):
            lines.append(
                f'  <uniqueid type="{escape(scheme)}">{escape(value)}</uniqueid>'
            )
        lines.append(
            f'  <uniqueid type="media-archivist">{escape(entry.id)}</uniqueid>'
        )

    lines.append(f"</{root}>")
    return "\n".join(lines) + "\n"


def _youtube_id(entry: MediaEntry) -> Optional[str]:
    video_id = entry.raw.get("videoId")
    return str(video_id) if entry.source is Source.YOUTUBE and video_id else None


def episode_nfo_xml(entry: MediaEntry, season: int, episode: int) -> str:
    """An ``<episodedetails>`` NFO for a video filed as one episode of its channel."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<episodedetails>",
             _tag("title", entry.title or entry.url), _tag("plot", _plot(entry)),
             _tag("season", season), _tag("episode", episode)]
    if entry.artist:
        lines.append(_tag("studio", entry.artist))
    runtime = _runtime_minutes(entry.duration)
    if runtime is not None:
        lines.append(_tag("runtime", runtime))
    if entry.thumbnail:
        lines.append(_tag("thumb", entry.thumbnail))
    year, premiered = _year_and_premiered(entry.published)
    if premiered:
        lines += [_tag("aired", premiered), _tag("premiered", premiered)]
    if year:
        lines.append(_tag("year", year))
    video_id = _youtube_id(entry)
    if video_id:
        lines.append(f'  <uniqueid type="youtube" default="true">{escape(video_id)}</uniqueid>')
    lines.append(f'  <uniqueid type="media-archivist">{escape(entry.id)}</uniqueid>')
    lines.append("</episodedetails>")
    return "\n".join(lines) + "\n"


def tvshow_nfo_xml(title: str, channel_id: Optional[str] = None) -> str:
    """A ``<tvshow>`` NFO for a channel filed as a show."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<tvshow>",
             _tag("title", title), _tag("studio", title)]
    if channel_id:
        lines.append(f'  <uniqueid type="youtube" default="true">{escape(channel_id)}</uniqueid>')
    lines.append("</tvshow>")
    return "\n".join(lines) + "\n"


def season_nfo_xml(season: int) -> str:
    """A ``<season>`` NFO for one upload year of a channel."""
    return "\n".join(['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<season>",
                      _tag("title", f"Season {season}"), _tag("seasonnumber", season),
                      "</season>"]) + "\n"
