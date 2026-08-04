"""Jellyfin / Kodi ``.strm`` export.

A ``.strm`` file is a one-line text file whose body is a URL. Jellyfin
and Kodi treat them as remote-media stubs: the player follows the URL
and streams the content directly. This is the simplest way to surface a
``media_archivist`` index inside Jellyfin without having Jellyfin
download anything.

Layout, by default::

    <output_dir>/
    ├── youtube/
    │   └── <safe artist or _unknown>/
    │       └── <safe title>.strm
    ├── bandcamp/...
    └── ...

Each ``.strm`` body points at the server's ``/strm/{entry_id}`` endpoint
when ``base_url`` is given; otherwise the entry's resolved
``stream`` field (or the watch URL as fallback) is written directly.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from media_archivist.index import Index
from media_archivist.models.canonical import MediaEntry

LOG = logging.getLogger("media_archivist.strm")

_SAFE = re.compile(r"[^\w\-. ]+")
_MAX_LEN = 120

#: Layouts available to :func:`export_strm`.
#:
#: ``by-source-artist`` (the default) reproduces the original,
#: pre-``layout`` behaviour: ``<output_dir>/<source>/<artist>/<title>.strm``.
#: It stays the default for back-compat.
LAYOUTS = ("by-source-artist", "flat", "by-source", "by-artist")


def _safe(name: str, default: str = "_unknown") -> str:
    name = (name or "").strip()
    if not name:
        return default
    name = _SAFE.sub("_", name)
    name = name.strip(" ._")
    return name[:_MAX_LEN] or default


def _strm_body(entry: MediaEntry, base_url: Optional[str]) -> str:
    if base_url:
        return f"{base_url.rstrip('/')}/strm/{entry.id}\n"
    return (entry.stream or entry.url) + "\n"


def _target_dir(out_root: Path, entry: MediaEntry, layout: str) -> Path:
    artist = _safe(entry.artist or "")
    if layout == "flat":
        return out_root
    if layout == "by-source":
        return out_root / entry.source.value
    if layout == "by-artist":
        return out_root / artist
    # "by-source-artist" (default / back-compat)
    return out_root / entry.source.value / artist


def _unique_target(directory: Path, basename: str, suffix: str,
                    seen: set) -> Path:
    """Return ``directory/basename.suffix``, disambiguated on collision.

    Two entries with the same sanitized title (e.g. same title from two
    different artists collapsed by a coarser layout) would otherwise
    clobber each other; append a short suffix derived from a counter to
    keep every file. ``seen`` tracks basenames already claimed under
    ``directory`` across the whole export call.
    """
    key = (str(directory), basename.lower())
    candidate = basename
    n = 1
    while (str(directory), candidate.lower()) in seen:
        n += 1
        candidate = f"{basename}-{n}"
    seen.add((str(directory), candidate.lower()))
    return directory / f"{candidate}{suffix}"


def export_strm(db_path: str, output_dir: str, *,
                base_url: Optional[str] = None,
                source: Optional[str] = None,
                where: Optional[str] = None,
                has_stream: Optional[bool] = None,
                limit: int = 0,
                dry_run: bool = False,
                layout: str = "by-source-artist",
                nfo: bool = False) -> int:
    """Walk the canonical view; write one ``.strm`` per matching entry.

    ``layout`` controls the folder structure (see :data:`LAYOUTS`); the
    default, ``by-source-artist``, matches pre-``layout`` behaviour so
    existing callers see unchanged output.

    When ``nfo`` is true, a ``.nfo`` sidecar (see
    :mod:`media_archivist.nfo`) is written next to each ``.strm`` with a
    matching basename, so Jellyfin/Kodi show real titles, artwork and
    tags instead of scraping the filename.

    Returns the number of ``.strm`` files that were (or would be) written.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    nfo_xml = None
    if nfo:
        from media_archivist.nfo import nfo_xml as _nfo_xml
        nfo_xml = _nfo_xml

    idx = Index(db_path)
    written = 0
    seen: set = set()
    for entry in idx.view(source=source, where=where,
                          has_stream=has_stream, limit=limit):
        if not (entry.stream or base_url or entry.url):
            continue
        title = _safe(entry.title or entry.url)
        directory = _target_dir(out_root, entry, layout)
        target = _unique_target(directory, title, ".strm", seen)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_strm_body(entry, base_url))
            if nfo_xml is not None:
                target.with_suffix(".nfo").write_text(nfo_xml(entry))
        written += 1
    return written
