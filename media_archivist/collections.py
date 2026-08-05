# SPDX-License-Identifier: Apache-2.0
"""Saved collections (smart playlists) — a named, re-runnable filter.

A ``Collection`` is just a remembered set of :class:`~media_archivist.index.Index`
filter kwargs (``source``/``where``/``grep``/``has_stream``/``explicit``)
under a curator-chosen name — e.g. "Blender open movies" =
``source=='youtube' and grep=='blender'``. Running the saved filter through
:func:`collection_entries` always reflects the *current* DB contents, so a
collection is a live, browsable view rather than a frozen snapshot.

Stored as a ``<db>.collections.json`` sidecar, mirroring the
``<db>.subscriptions.json`` / ``<db>.canonical.json`` sidecar convention in
:mod:`media_archivist.subscriptions` / :mod:`media_archivist.canonicalize`.

:func:`export_collection` materializes a collection for Jellyfin/Kodi:
``.strm`` files (via :func:`media_archivist.strm.export_strm`, reusing its
filtering/layout/nfo logic unchanged) and/or an ``.m3u`` playlist file,
built with the same ``#EXTM3U``/``#EXTINF`` shape as the server's ``/m3u``
endpoint (see :mod:`media_archivist.server.routes`) — factored out here as
:func:`build_m3u` so both the file export and the live
``/collections/{name}/m3u`` endpoint share one implementation.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.index import Index, WhereError
from media_archivist.models.canonical import MediaEntry

LOG = logging.getLogger("media_archivist.collections")

__all__ = [
    "Collection",
    "CollectionSidecar",
    "WhereError",
    "load_collections",
    "save_collections",
    "add_collection",
    "remove_collection",
    "list_collections",
    "get_collection",
    "collection_entries",
    "collection_count",
    "build_m3u",
    "export_collection",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Collection(BaseModel):
    """A named saved query — a smart playlist over the DB's current contents."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    source: Optional[str] = None
    where: Optional[str] = None
    grep: Optional[str] = None
    has_stream: Optional[bool] = None
    explicit: Optional[bool] = None
    created_at: str = Field(default_factory=_utcnow)


class CollectionSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collections: List[Collection] = Field(default_factory=list)


def _collections_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".collections.json")


def load_collections(db_path: str) -> CollectionSidecar:
    p = _collections_path(db_path)
    if not p.exists():
        return CollectionSidecar()
    return CollectionSidecar.model_validate(json.loads(p.read_text()))


def save_collections(db_path: str, sidecar: CollectionSidecar) -> Path:
    """Atomically write the sidecar next to ``db_path``."""
    p = _collections_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sidecar.model_dump_json(indent=2))
        os.replace(tmp_path, p)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return p


def add_collection(db_path: str, name: str, *,
                    where: Optional[str] = None,
                    source: Optional[str] = None,
                    grep: Optional[str] = None,
                    has_stream: Optional[bool] = None,
                    explicit: Optional[bool] = None,
                    description: Optional[str] = None) -> Collection:
    """Add (or update in place) a saved collection; dedupes by ``name``.

    Validates the filter by test-evaluating it against an empty index up
    front (raises :class:`~media_archivist.index.WhereError` on a bad
    ``where`` expression) so a typo is caught at save time, not the first
    time someone browses the collection.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    if where:
        # Syntax-validate now; a bad --where should fail loudly here rather
        # than surfacing later as an opaque 400 from every consumer.
        import ast
        try:
            ast.parse(where, mode="eval")
        except SyntaxError as e:
            raise WhereError(f"invalid expression: {e.msg}") from e

    sidecar = load_collections(db_path)
    for coll in sidecar.collections:
        if coll.name == name:
            coll.description = description if description is not None else coll.description
            coll.source = source
            coll.where = where
            coll.grep = grep
            coll.has_stream = has_stream
            coll.explicit = explicit
            save_collections(db_path, sidecar)
            return coll

    coll = Collection(name=name, description=description, source=source,
                       where=where, grep=grep, has_stream=has_stream,
                       explicit=explicit)
    sidecar.collections.append(coll)
    save_collections(db_path, sidecar)
    return coll


def remove_collection(db_path: str, name: str) -> bool:
    """Remove the collection named *name*; return whether one was removed."""
    sidecar = load_collections(db_path)
    before = len(sidecar.collections)
    sidecar.collections = [c for c in sidecar.collections if c.name != name]
    if len(sidecar.collections) == before:
        return False
    save_collections(db_path, sidecar)
    return True


def list_collections(db_path: str) -> List[Collection]:
    return load_collections(db_path).collections


def get_collection(db_path: str, name: str) -> Optional[Collection]:
    for coll in list_collections(db_path):
        if coll.name == name:
            return coll
    return None


def _filter_kwargs(coll: Collection) -> dict:
    return dict(source=coll.source, where=coll.where, grep=coll.grep,
                has_stream=coll.has_stream, explicit=coll.explicit)


def collection_entries(db_path: str, coll: Collection, *,
                        limit: int = 0, offset: int = 0) -> List[MediaEntry]:
    """Run *coll*'s saved filter against the current DB via :class:`Index`.

    Never raises on a bad filter that snuck past :func:`add_collection`'s
    up-front syntax check (e.g. an unknown field referenced at eval time) —
    it re-raises :class:`WhereError` so callers can surface a clear message
    instead of a stack trace.
    """
    idx = Index(db_path)
    return idx.to_list(limit=limit, offset=offset, **_filter_kwargs(coll))


def collection_count(db_path: str, coll: Collection) -> int:
    idx = Index(db_path)
    return idx.count(**_filter_kwargs(coll))


def build_m3u(entries: List[MediaEntry]) -> str:
    """Render entries as an ``#EXTM3U`` playlist body.

    Mirrors the ``#EXTINF``/stream-line shape of the ``/m3u`` endpoint in
    :mod:`media_archivist.server.routes` exactly, so a collection's M3U and
    the ad-hoc ``/m3u?...`` endpoint look identical to a player.
    """
    lines = ["#EXTM3U"]
    for e in entries:
        secs = int(e.duration) if e.duration else -1
        artist = e.artist or ""
        title = e.title or e.url
        lines.append(f"#EXTINF:{secs},{artist} - {title}".rstrip(" -"))
        lines.append(e.stream or e.url)
    return "\n".join(lines)


def export_collection(db_path: str, coll: Collection, output_dir: str, *,
                       base_url: Optional[str] = None,
                       m3u: bool = False,
                       strm: bool = True,
                       layout: str = "by-source-artist",
                       nfo: bool = False) -> dict:
    """Materialize a collection on disk for Jellyfin/Kodi.

    Writes ``.strm`` files (reusing :func:`media_archivist.strm.export_strm`
    unchanged, scoped to the collection's saved filter) when ``strm`` is
    true, and/or a single ``<output_dir>/<name>.m3u`` playlist file when
    ``m3u`` is true. Returns ``{"strm_written": int, "m3u_path": str|None}``.

    Never raises on a bad saved filter — surfaces
    :class:`~media_archivist.index.WhereError` to the caller (CLI/route
    handlers turn that into a clear message / 400, not a crash).
    """
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    result = {"strm_written": 0, "m3u_path": None}

    if strm:
        from media_archivist.strm import export_strm
        result["strm_written"] = export_strm(
            db_path, str(out_root), base_url=base_url,
            source=coll.source, where=coll.where, has_stream=coll.has_stream,
            layout=layout, nfo=nfo,
        )

    if m3u:
        entries = collection_entries(db_path, coll)
        from media_archivist.strm import _safe
        m3u_path = out_root / f"{_safe(coll.name)}.m3u"
        m3u_path.write_text(build_m3u(entries) + "\n")
        result["m3u_path"] = str(m3u_path)

    return result
