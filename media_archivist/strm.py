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


def export_strm(db_path: str, output_dir: str, *,
                base_url: Optional[str] = None,
                source: Optional[str] = None,
                where: Optional[str] = None,
                has_stream: Optional[bool] = None,
                limit: int = 0,
                dry_run: bool = False) -> int:
    """Walk the canonical view; write one ``.strm`` per matching entry.

    Returns the number of files that were (or would be) written.
    """
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    idx = Index(db_path)
    written = 0
    for entry in idx.view(source=source, where=where,
                          has_stream=has_stream, limit=limit):
        if not (entry.stream or base_url or entry.url):
            continue
        artist = _safe(entry.artist or "")
        title = _safe(entry.title or entry.url)
        target = out_root / entry.source.value / artist / f"{title}.strm"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_strm_body(entry, base_url))
        written += 1
    return written
