"""On-disk JSON envelope.

The legacy format is ``{url: entry, ...}``. The v0.2 envelope wraps this in
a ``{"_meta": {...}, "entries": {...}}`` shape so we can carry archive-level
metadata (created date, schema version, archivist version, source-mix) without
polluting the entry namespace.

Reading is backwards-compatible: ``MediaArchive.load_dict`` accepts either
the legacy bare-mapping or the v0.2 envelope.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.version import __version__

SCHEMA_VERSION = 2


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ArchiveMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = SCHEMA_VERSION
    archivist_version: str = __version__
    created: str = Field(default_factory=_utcnow)
    last_synced: Optional[str] = None
    source_mix: Dict[str, int] = Field(default_factory=dict)


class MediaArchive(BaseModel):
    """Validated wrapper around the on-disk JSON content."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: ArchiveMeta = Field(default_factory=ArchiveMeta, alias="_meta")
    entries: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def load_dict(cls, data: Dict[str, Any]) -> "MediaArchive":
        """Validate an envelope dict (``{"_meta": ..., "entries": ...}``)."""
        return cls.model_validate(data)

    def dump_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")

    def touch(self) -> None:
        self.meta.last_synced = _utcnow()

    def recompute_source_mix(self) -> None:
        mix: Dict[str, int] = {}
        for row in self.entries.values():
            src = row.get("source") or "unknown"
            mix[src] = mix.get(src, 0) + 1
        self.meta.source_mix = mix
