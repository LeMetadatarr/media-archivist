"""Request / response pydantic models for the HTTP server."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.models.canonical import MediaEntry


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


TaskStatus = Literal["queued", "running", "ok", "error"]


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: Optional[Literal["youtube", "ia", "music", "bandcamp", "soundcloud"]] = None
    require: List[str] = Field(default_factory=list)
    blacklist: List[str] = Field(default_factory=list)
    min_duration: int = -1


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: TaskStatus = "queued"
    request: ArchiveRequest
    created: str = Field(default_factory=_utcnow)
    started: Optional[str] = None
    finished: Optional[str] = None
    error: Optional[str] = None
    rows_added: int = 0


class EntryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    entries: List[MediaEntry]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    version: str
    db_path: str


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    available: bool
    media: List[str] = Field(default_factory=list)
    modality: List[str] = Field(default_factory=list)
    genre_filter: List[str] = Field(default_factory=list)


class ProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    active: int
    providers: List[ProviderInfo]


class CanonicalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: Optional[List[str]] = None
    stamp_rows: bool = True
    max_workers: int = 8


class CanonicalizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_records: int
    quarantined: int
    entities: int


class QuarantineConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    candidate_canonical_id: Optional[str] = None
    conflicts: List[str] = Field(default_factory=list)


class QuarantineListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    entries: List[QuarantineConflict]


class QuarantineDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    decision: Literal["accept", "reject"]
    ok: bool


class StatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    source_mix: Dict[str, int]
    canonical_records: int = 0
    quarantined: int = 0
    archivist_version: str
    db_path: str
