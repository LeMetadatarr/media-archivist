"""Pydantic shape for the ``_meta.enriched`` block on raw rows.

Enrichment is *additive* — the orchestrator only ever fills in fields
that were missing. Nothing under ``enriched`` is part of the canonical
view's required schema; consumers that don't care about transcripts /
lyrics / content-type can ignore it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TranscriptCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float           # seconds
    end: float
    text: str


class TranscriptBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    auto_generated: bool = False
    cues: List[TranscriptCue] = Field(default_factory=list)
    fetched_at: str = Field(default_factory=_utcnow)


class LyricsBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source: str            # e.g. "bandcamp"
    fetched_at: str = Field(default_factory=_utcnow)


class ContentTypeBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str             # e.g. "documentary", "music_audio"
    confidence: Optional[float] = None
    classifier: str = "tutubo.content_type.classify_video"
    classified_at: str = Field(default_factory=_utcnow)


class EnrichedBlock(BaseModel):
    """Aggregate ``_meta.enriched`` shape — every field optional."""

    model_config = ConfigDict(extra="allow")  # forward-compat: new providers can add fields

    transcript: Optional[TranscriptBlock] = None
    lyrics: Optional[LyricsBlock] = None
    content_type: Optional[ContentTypeBlock] = None
