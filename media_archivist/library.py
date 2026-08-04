"""Local media-library tagger — scan, resolve, write NFO sidecars.

NON-DESTRUCTIVE by design: this module only ever *reads* files under a
library root and *writes* ``<basename>.nfo`` sidecars next to them. It never
edits, moves, renames, remuxes, or deletes the underlying media file.

Pipeline: :func:`scan` walks the tree → :func:`extract_signals` builds a
``mediavocab`` ``Signals`` bag per file (embedded tags / filename parsing,
optionally sharpened by ``guessit``/``mutagen`` when installed) →
:func:`tag_file` resolves it against metadatarr and writes a Kodi/Jellyfin
``.nfo`` via :mod:`media_archivist.nfo`.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional

from mediavocab import MediaType
from mediavocab.models.signals import Signals
from metadatarr.resolve import resolve

from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source
from media_archivist.nfo import nfo_xml

LOG = logging.getLogger("media_archivist.library")

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".m4v", ".ts"}
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}

try:
    import guessit as _guessit  # type: ignore
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    _guessit = None

try:
    import mutagen as _mutagen  # type: ignore
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    _mutagen = None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

@dataclass
class LocalMediaFile:
    """A media file discovered under a scanned root."""

    path: Path
    kind: Literal["video", "music"]


def scan(root: str, *, media: str = "both") -> Iterator[LocalMediaFile]:
    """Recursively walk *root*, yielding every matched media file.

    ``media`` restricts the walk to ``"video"``, ``"music"``, or the
    default ``"both"``. Non-media files (subtitles, artwork, existing
    ``.nfo`` sidecars, etc.) are silently skipped.
    """
    want_video = media in ("both", "video")
    want_music = media in ("both", "music")
    root_path = Path(root)
    for dirpath, _dirnames, filenames in os.walk(root_path):
        for name in filenames:
            ext = Path(name).suffix.lower()
            if want_video and ext in VIDEO_EXTS:
                yield LocalMediaFile(path=Path(dirpath) / name, kind="video")
            elif want_music and ext in AUDIO_EXTS:
                yield LocalMediaFile(path=Path(dirpath) / name, kind="music")


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(?:\(|\.|\s)((?:19|20)\d{2})(?:\)|\.|\s|$)")
_SXXEXX_RE = re.compile(r"[Ss](\d{1,2})[EeXx](\d{1,3})")
_RELEASE_JUNK_RE = re.compile(
    r"\b(1080p|720p|2160p|4k|hdr|x264|x265|h264|h265|hevc|webrip|web-dl|webdl|"
    r"bluray|brrip|dvdrip|hdtv|amzn|nf|remux|proper|repack|extended|"
    r"[a-z0-9]+-group)\b",
    re.IGNORECASE,
)


def _clean_title(text: str) -> str:
    text = text.replace(".", " ").replace("_", " ")
    text = _RELEASE_JUNK_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -.")
    return text


def _parse_video_filename(stem: str) -> Signals:
    """Regex fallback filename parser for video files.

    Handles "Title (2010)", "Show.Name.S01E02", "Title.2010.1080p".
    """
    m = _SXXEXX_RE.search(stem)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
        title = _clean_title(stem[: m.start()])
        return Signals(
            title=title or None,
            season=season,
            episode=episode,
            medium=MediaType.EPISODIC_SERIES,
        )

    year = None
    ym = _YEAR_RE.search(stem)
    title_part = stem
    if ym:
        year = int(ym.group(1))
        title_part = stem[: ym.start()]
    title = _clean_title(title_part)
    return Signals(
        title=title or None,
        year=year,
        medium=MediaType.MOVIE,
    )


def _guessit_video_signals(path: Path) -> Optional[Signals]:
    if _guessit is None:
        return None
    try:
        guess = _guessit.guessit(path.name)
    except Exception:
        LOG.exception("guessit failed on %s; falling back to filename parsing", path)
        return None
    gtype = str(guess.get("type") or "").lower()
    title = guess.get("title")
    if not title:
        return None
    if gtype == "episode":
        season = guess.get("season")
        episode = guess.get("episode")
        return Signals(
            title=str(title),
            season=int(season) if isinstance(season, int) else None,
            episode=int(episode) if isinstance(episode, int) else None,
            medium=MediaType.EPISODIC_SERIES,
        )
    year = guess.get("year")
    return Signals(
        title=str(title),
        year=int(year) if isinstance(year, int) else None,
        medium=MediaType.MOVIE,
    )


_ARTIST_TITLE_RE = re.compile(r"^\s*(.+?)\s*-\s*(.+?)\s*$")
_TRACK_NUM_RE = re.compile(r"^\s*\d{1,3}[\s._-]+(.+?)\s*$")


def _parse_music_filename(stem: str) -> Signals:
    """Regex fallback for music files: 'Artist - Title' or 'NN Title'."""
    m = _ARTIST_TITLE_RE.match(stem)
    if m:
        return Signals(artist=m.group(1) or None, title=m.group(2) or None,
                        medium=MediaType.MUSIC)
    m = _TRACK_NUM_RE.match(stem)
    if m:
        return Signals(title=m.group(1) or None, medium=MediaType.MUSIC)
    return Signals(title=stem or None, medium=MediaType.MUSIC)


def _mutagen_music_signals(path: Path) -> Optional[Signals]:
    if _mutagen is None:
        return None
    try:
        audio = _mutagen.File(path, easy=True)  # type: ignore[attr-defined]
    except Exception:
        LOG.exception("mutagen failed on %s; falling back to filename parsing", path)
        return None
    if not audio or not audio.tags:
        return None

    def _first(key: str) -> Optional[str]:
        vals = audio.tags.get(key)
        return vals[0] if vals else None

    title = _first("title")
    artist = _first("artist")
    album = _first("album")
    date = _first("date")
    if not (title or artist or album):
        return None
    year = None
    if date:
        m = re.match(r"(\d{4})", str(date))
        if m:
            year = int(m.group(1))
    return Signals(
        title=title, artist=artist, medium=MediaType.MUSIC, year=year,
    ) if title or artist else None


def extract_signals(file: LocalMediaFile) -> Signals:
    """Build a ``Signals`` bag describing *file* for metadatarr resolution.

    VIDEO: ``guessit`` when available, else a regex filename fallback.
    MUSIC: embedded tags via ``mutagen`` when available, else a regex
    filename fallback ("Artist - Title" / "NN Title").
    """
    stem = file.path.stem
    if file.kind == "video":
        signals = _guessit_video_signals(file.path)
        if signals is not None:
            return signals
        return _parse_video_filename(stem)

    signals = _mutagen_music_signals(file.path)
    if signals is not None:
        return signals
    return _parse_music_filename(stem)


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

@dataclass
class TagResult:
    path: Path
    matched: bool
    external_ids: Optional[Dict[str, Any]]
    nfo_path: Optional[Path]
    action: Literal["wrote", "would-write", "skipped", "error"]
    note: str = ""


def _entry_from_signals(file: LocalMediaFile, signals: Signals,
                        external_ids: Optional[Dict[str, Any]]) -> MediaEntry:
    published = str(signals.year) if signals.year else None
    raw: Dict[str, Any] = {
        "source": Source.LOCAL.value,
        "url": file.path.as_uri(),
        "path": str(file.path),
        "title": signals.title or file.path.stem,
        "artist": signals.artist,
        "duration": signals.runtime,
        "published": published,
        "media_type": signals.medium.value if signals.medium else None,
        "season": signals.season,
        "episode": signals.episode,
    }
    entry = MediaEntry.build(
        source=Source.LOCAL,
        url=raw["url"],
        title=raw["title"],
        raw=raw,
        artist=signals.artist,
        duration=signals.runtime,
        published=published,
        stream=str(file.path),
    )
    if external_ids:
        from mediavocab.models import ExternalIds
        entry.external_ids = ExternalIds.model_validate(external_ids)
    return entry


def tag_file(file: LocalMediaFile, *, write_nfo: bool = True,
            dry_run: bool = False, min_confidence: float = 0.5) -> TagResult:
    """Resolve *file* via metadatarr and write (or preview) its ``.nfo``.

    Never raises: any failure to read/resolve the file is captured in the
    returned :class:`TagResult` (``action="error"``) so a bad file never
    aborts a batch run. Never touches the media file itself.
    """
    try:
        signals = extract_signals(file)
    except Exception as exc:  # pragma: no cover — defensive, extractors are safe
        LOG.exception("failed to extract signals for %s", file.path)
        return TagResult(path=file.path, matched=False, external_ids=None,
                         nfo_path=None, action="error", note=str(exc))

    external_ids: Optional[Dict[str, Any]] = None
    matched = False
    note = ""
    try:
        result = resolve(signals)
        if result.signals is not None and result.external_ids is not None:
            ids_dict = {
                k: v for k, v in result.external_ids.model_dump().items()
                if v and k != "extra"
            }
            if ids_dict:
                external_ids = ids_dict
                matched = True
                signals = result.signals
    except Exception as exc:
        # A provider failure (network, missing key, bad response) must never
        # abort the run — fall through and write a filename-only nfo.
        LOG.warning("metadatarr resolve failed for %s: %s", file.path, exc)
        note = f"resolve failed: {exc}"

    if not signals.title:
        return TagResult(path=file.path, matched=False, external_ids=None,
                         nfo_path=None, action="skipped",
                         note=note or "no title could be determined")

    entry = _entry_from_signals(file, signals, external_ids)
    nfo_path = file.path.with_suffix(".nfo")

    if not write_nfo:
        return TagResult(path=file.path, matched=matched,
                         external_ids=external_ids, nfo_path=None,
                         action="skipped", note=note or "--no-nfo")

    if dry_run:
        return TagResult(path=file.path, matched=matched,
                         external_ids=external_ids, nfo_path=nfo_path,
                         action="would-write",
                         note=note or ("matched" if matched else "filename-only"))

    try:
        xml = nfo_xml(entry)
        nfo_path.write_text(xml, encoding="utf-8")
    except Exception as exc:
        LOG.exception("failed to write nfo for %s", file.path)
        return TagResult(path=file.path, matched=matched,
                         external_ids=external_ids, nfo_path=None,
                         action="error", note=str(exc))

    return TagResult(path=file.path, matched=matched, external_ids=external_ids,
                     nfo_path=nfo_path, action="wrote",
                     note=note or ("matched" if matched else "filename-only"))


def tag_library(root: str, *, media: str = "both", write_nfo: bool = True,
                dry_run: bool = False, index_db: Optional[str] = None,
                min_confidence: float = 0.5) -> List[TagResult]:
    """Scan *root* and tag every discovered media file.

    When *index_db* is given, matched entries are additionally upserted
    into that media-archivist DB (as ``Source.LOCAL`` rows) so they show up
    in the WebUI/index alongside the other sources.
    """
    results: List[TagResult] = []
    db = None
    if index_db and not dry_run:
        from media_archivist.storage import EnvelopeJsonStorage
        db = EnvelopeJsonStorage(index_db)

    for file in scan(root, media=media):
        result = tag_file(file, write_nfo=write_nfo, dry_run=dry_run,
                          min_confidence=min_confidence)
        results.append(result)
        if db is not None and result.matched:
            try:
                signals = extract_signals(file)
                entry = _entry_from_signals(file, signals, result.external_ids)
                db[entry.url] = entry.raw
            except Exception:
                LOG.exception("failed to index %s", file.path)

    if db is not None:
        db.store()

    return results
