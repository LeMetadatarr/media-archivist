"""Subtitle/caption fetching as Jellyfin/Kodi-ready sidecar files.

:mod:`media_archivist.enrich.transcripts` already shells out to
``yt-dlp`` to pull YouTube subtitle tracks for in-DB text enrichment
(``_meta.enriched.transcript``). This module builds on the same
``yt-dlp`` invocation (:func:`media_archivist.enrich.transcripts.fetch_subtitle_files`
— no yt-dlp command is duplicated here) to make subtitle fetching a
first-class, user-facing feature: on-disk ``<basename>.<lang>.srt``/``.vtt``
sidecar files written next to a stream's ``.strm`` export (or any
directory a caller names), so a media player picks them up automatically.

Layout mirrors :mod:`media_archivist.strm` — same ``_safe`` basename
and ``_target_dir`` per-entry directory logic — so subtitle files land
next to the ``.strm`` file for the same entry when both are exported
under the same ``layout``.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from media_archivist.enrich.transcripts import fetch_subtitle_files
from media_archivist.index import Index, WhereError
from media_archivist.models.canonical import MediaEntry
from media_archivist.strm import LAYOUTS, _safe, _target_dir
from media_archivist.streams import ytdlp_available

LOG = logging.getLogger("media_archivist.subtitles")

_DEFAULT_LANGS = ("en",)
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_WORKERS = 4


@dataclass
class SubtitleResult:
    """Outcome of a single entry's subtitle fetch."""

    entry_id: str
    status: str  # "written" | "none" | "skipped" | "error" | "dry-run"
    langs: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    error: Optional[str] = None


def fetch_subtitles(entry: MediaEntry, out_dir: str | Path, *,
                    langs: Iterable[str] = _DEFAULT_LANGS,
                    auto: bool = True,
                    sub_format: str = "vtt",
                    dry_run: bool = False,
                    timeout: float = _DEFAULT_TIMEOUT) -> SubtitleResult:
    """Fetch subtitle tracks for ``entry`` and write ``.srt``/``.vtt`` sidecars.

    Writes ``<out_dir>/<safe-title>.<lang>.<sub_format>`` for every
    language track yt-dlp returns (manual subs, plus auto-generated
    ones when ``auto=True``). Never raises — per-entry failures are
    captured in the returned :class:`SubtitleResult`.
    """
    langs = list(langs) or list(_DEFAULT_LANGS)
    if not ytdlp_available():
        LOG.info("yt-dlp unavailable — skipping subtitles for %s", entry.id)
        return SubtitleResult(entry_id=entry.id, status="skipped",
                              error="yt-dlp not available")

    url = entry.url
    if not url:
        return SubtitleResult(entry_id=entry.id, status="error",
                              error="entry has no url")

    basename = _safe(entry.title or entry.url)
    out_dir = Path(out_dir).expanduser()

    if dry_run:
        return SubtitleResult(
            entry_id=entry.id, status="dry-run", langs=langs,
            files=[str(out_dir / f"{basename}.{lang}.{sub_format}") for lang in langs],
        )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            fetched = fetch_subtitle_files(
                url, Path(tmp), languages=langs, auto=auto,
                sub_format=sub_format, timeout=timeout,
            )
            if not fetched:
                return SubtitleResult(entry_id=entry.id, status="none", langs=langs)

            out_dir.mkdir(parents=True, exist_ok=True)
            written: List[str] = []
            got_langs: List[str] = []
            for src in fetched:
                # yt-dlp names files "<id>.<lang>.<ext>" (or
                # "<id>.<lang>.auto.<ext>" for auto-subs on some
                # versions); pull the lang token, default to the
                # first requested lang if it can't be parsed.
                stem = src.name[: -(len(sub_format) + 1)] if src.name.endswith(f".{sub_format}") else src.stem
                parts = [p for p in stem.split(".")[1:] if p and p != "auto"]
                lang = parts[0] if parts else langs[0]
                dest = out_dir / f"{basename}.{lang}.{sub_format}"
                shutil.copyfile(src, dest)
                written.append(str(dest))
                if lang not in got_langs:
                    got_langs.append(lang)
            return SubtitleResult(entry_id=entry.id, status="written",
                                  langs=got_langs, files=written)
    except Exception as e:  # never raise — this is a best-effort enrichment
        LOG.warning("subtitle fetch failed for %s (%s): %s", entry.id, url, e)
        return SubtitleResult(entry_id=entry.id, status="error", error=str(e))


def fetch_library_subtitles(db_path: str | Path, out_dir: str | Path, *,
                            source: Optional[str] = None,
                            where: Optional[str] = None,
                            langs: Iterable[str] = _DEFAULT_LANGS,
                            auto: bool = True,
                            sub_format: str = "vtt",
                            dry_run: bool = False,
                            layout: str = "by-source-artist",
                            limit: int = 0,
                            max_workers: int = _DEFAULT_MAX_WORKERS) -> List[SubtitleResult]:
    """Fetch subtitles for every entry matching ``source``/``where``.

    Directory-per-entry uses the same ``layout`` logic as
    :func:`media_archivist.strm.export_strm` (default
    ``by-source-artist``) so subtitle sidecars land next to the
    matching ``.strm`` file. Concurrency is bounded (``max_workers``)
    and polite — each fetch is a separate yt-dlp subprocess, so a
    large ``max_workers`` just parallelizes network-bound waits, not
    CPU work.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")

    out_root = Path(out_dir).expanduser()
    langs = list(langs) or list(_DEFAULT_LANGS)

    idx = Index(str(db_path))
    try:
        entries = idx.to_list(source=source, where=where, limit=limit)
    except WhereError:
        raise

    if not entries:
        return []

    def _run(entry: MediaEntry) -> SubtitleResult:
        target_dir = _target_dir(out_root, entry, layout)
        return fetch_subtitles(
            entry, target_dir, langs=langs, auto=auto, sub_format=sub_format,
            dry_run=dry_run,
        )

    max_workers = max(1, max_workers)
    if max_workers == 1 or len(entries) == 1:
        return [_run(e) for e in entries]

    results: List[Optional[SubtitleResult]] = [None] * len(entries)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {pool.submit(_run, e): i for i, e in enumerate(entries)}
        for fut in as_completed(future_to_idx):
            results[future_to_idx[fut]] = fut.result()
    return [r for r in results if r is not None]
