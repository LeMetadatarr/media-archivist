"""Envelope-aware JSON storage.

Wraps :class:`json_database.JsonStorage` (or :class:`JsonStorageXDG`) so that
the on-disk file is the :class:`media_archivist.models.MediaArchive` envelope
(``{"_meta": {...}, "entries": {...}}``), while in-memory the ``dict``
interface still maps URL → entry. The envelope is the only on-disk shape;
empty / missing files are initialised with a fresh :class:`ArchiveMeta`.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from json_database import JsonStorage, JsonStorageXDG

from media_archivist._atomic import atomic_write_json
from media_archivist.models import MediaArchive
from media_archivist.models.archive import ArchiveMeta


class _EnvelopeMixin:
    """Mixin that re-shapes loaded data into the envelope and writes it back."""

    meta: ArchiveMeta

    def _post_load(self) -> None:
        """Re-shape the loaded JSON envelope into an entries-only ``dict``."""
        d: Dict[str, Any] = self  # type: ignore[assignment]
        if not d:
            self.meta = ArchiveMeta()
            return
        if "_meta" not in d or "entries" not in d:
            raise ValueError(
                "DB file is not a valid MediaArchive envelope; expected "
                "top-level keys '_meta' and 'entries'"
            )
        arc = MediaArchive.load_dict(dict(d))
        d.clear()
        d.update(arc.entries)
        self.meta = arc.meta

    def store(self, path: Optional[str] = None) -> None:  # type: ignore[override]
        """Write the envelope to disk; keep in-memory shape as URL→entry."""
        d: Dict[str, Any] = self  # type: ignore[assignment]
        target = path or getattr(self, "path", None)
        if not target:
            return
        # Build the envelope without polluting our own state.
        arc = MediaArchive(meta=self.meta, entries=dict(d))
        arc.recompute_source_mix()
        arc.touch()
        atomic_write_json(os.path.expanduser(target), arc.dump_dict(), indent=4)


class EnvelopeJsonStorage(_EnvelopeMixin, JsonStorage):
    """Explicit-path storage with envelope semantics."""

    def __init__(self, path: str, disable_lock: bool = False) -> None:
        super().__init__(path, disable_lock=disable_lock)
        self._post_load()


class EnvelopeJsonStorageXDG(_EnvelopeMixin, JsonStorageXDG):
    """XDG-managed storage with envelope semantics."""

    def __init__(self, name: Optional[str] = None, *, subfolder: str = "media_archivist",
                 xdg_folder: Optional[str] = None) -> None:
        kwargs: Dict[str, Any] = {"subfolder": subfolder}
        if xdg_folder is not None:
            kwargs["xdg_folder"] = xdg_folder
        super().__init__(name, **kwargs)
        self._post_load()
