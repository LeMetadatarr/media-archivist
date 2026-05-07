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


class StatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    source_mix: Dict[str, int]
    canonical_records: int = 0
    quarantined: int = 0
    archivist_version: str
    db_path: str
