"""Canonical cross-source view model.

A :class:`MediaEntry` is the unified shape that every backend's raw row
projects to via the adapters in :mod:`media_archivist.views`. The on-disk
format is **not** a `MediaEntry` — view models exist purely so consumers
can write source-agnostic queries, dedup, and dataset exports.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.raw import Source

CanonicalStatus = Literal["matched", "quarantined", "unmatched"]


def stable_id(source: Source, url: str) -> str:
    """Deterministic, opaque entry id — sha1 of ``source:url``."""
    return hashlib.sha1(f"{source.value}:{url}".encode("utf-8")).hexdigest()


class MediaEntry(BaseModel):
    """Unified read-time view of a row, regardless of backend."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: Source
    url: str
    title: str
    tags: List[str] = Field(default_factory=list)
    artist: Optional[str] = None
    album: Optional[str] = None
    duration: Optional[float] = None  # seconds
    published: Optional[str] = None   # ISO 8601 if derivable
    thumbnail: Optional[str] = None
    is_live: bool = False
    explicit: bool = False
    stream: Optional[str] = None
    canonical_id: Optional[str] = None
    canonical_status: Optional[CanonicalStatus] = None
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    raw: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(cls, *, source: Source, url: str, title: Optional[str],
              raw: Dict[str, Any], **fields) -> "MediaEntry":
        """Construct a ``MediaEntry`` with id and tags pre-filled."""
        return cls(
            id=stable_id(source, url),
            source=source,
            url=url,
            title=title or "",
            raw=raw,
            **fields,
        )
