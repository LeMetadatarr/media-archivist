"""Sidecar storage models for the canonical/quarantine maps."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import SignalConflict, Signals


CanonicalStatus = Literal["matched", "quarantined", "unmatched"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProviderHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    matched_at: str
    confidence: float


class CanonicalRecord(BaseModel):
    """One entry per *work* in the ``<db>.canonical.json`` sidecar."""

    model_config = ConfigDict(extra="forbid")

    canonical_id: str
    signals: Signals
    members: List[str] = Field(default_factory=list)
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    provider_log: List[ProviderHit] = Field(default_factory=list)
    created: str = Field(default_factory=_utcnow)
    last_updated: str = Field(default_factory=_utcnow)

    def touch(self) -> None:
        self.last_updated = _utcnow()


class QuarantineEntry(BaseModel):
    """One entry per quarantined row in the ``<db>.quarantine.json`` sidecar."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    candidate_canonical_id: Optional[str] = None
    conflicts: List[SignalConflict] = Field(default_factory=list)
    proposed_signals: Optional[Signals] = None
    first_seen: str = Field(default_factory=_utcnow)
    last_seen: str = Field(default_factory=_utcnow)


class CanonicalSidecar(BaseModel):
    """Top-level shape of ``<db>.canonical.json``."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    records: Dict[str, CanonicalRecord] = Field(default_factory=dict)


class QuarantineSidecar(BaseModel):
    """Top-level shape of ``<db>.quarantine.json``."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    entries: Dict[str, QuarantineEntry] = Field(default_factory=dict)
