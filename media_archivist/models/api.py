"""Request / response pydantic models for the HTTP server."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.models.canonical import MediaEntry


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


TaskStatus = Literal["queued", "running", "ok", "error"]


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["archive"] = "archive"
    url: str
    backend: Optional[Literal["youtube", "ia", "music", "bandcamp", "soundcloud"]] = None
    require: List[str] = Field(default_factory=list)
    blacklist: List[str] = Field(default_factory=list)
    min_duration: int = -1


class DownloadRequest(BaseModel):
    """Request to download a copy of an already-indexed entry to disk.

    Secondary to :class:`ArchiveRequest` — this never accepts a
    client-supplied destination path; the download directory is always
    server-configured (see ``media_archivist.streams.default_download_dir``
    / ``MEDIA_ARCHIVIST_DOWNLOAD_DIR``), so there is no path-traversal
    surface here.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["download"] = "download"
    entry_id: str
    format: str = "best"


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: TaskStatus = "queued"
    request: Union[ArchiveRequest, DownloadRequest] = Field(discriminator="kind")
    created: str = Field(default_factory=_utcnow)
    started: Optional[str] = None
    finished: Optional[str] = None
    error: Optional[str] = None
    rows_added: int = 0
    # download-only fields; unused (stay None/0) for kind="archive" tasks.
    progress: Optional[int] = None
    filepath: Optional[str] = None

    @property
    def kind(self) -> str:
        return self.request.kind


class EntryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    entries: List[MediaEntry]
    limit: int = 0
    offset: int = 0


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unhealthy"] = "ok"
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


class StreamHealthEntry(BaseModel):
    """One entry's stream-health probe result, over the wire."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    url: str
    source: str
    title: str
    status: Literal["ok", "dead", "expired", "no-stream", "gone"]
    checked_url: Optional[str] = None
    status_code: Optional[int] = None
    reason: Optional[str] = None


class StreamHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    counts: Dict[str, int]
    entries: List[StreamHealthEntry]


class ReResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    ok: bool
    old_stream: Optional[str] = None
    new_stream: Optional[str] = None
    error: Optional[str] = None


class SubscriptionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: str
    label: Optional[str] = None
    added_at: str
    last_synced_at: Optional[str] = None
    last_rows_added: int = 0
    last_error: Optional[str] = None
    auto_download: bool = False


class SubscriptionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    subscriptions: List[SubscriptionInfo]


class SubscriptionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: Optional[Literal["youtube", "ia", "music", "bandcamp", "soundcloud"]] = None
    label: Optional[str] = None
    auto_download: bool = False


class SubscriptionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


class SubscriptionSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: str
    ok: bool
    rows_added: int = 0
    error: Optional[str] = None
    dry_run: bool = False
    new_urls: List[str] = Field(default_factory=list)
    downloaded: List[str] = Field(default_factory=list)
    download_errors: Dict[str, str] = Field(default_factory=dict)


class SubscriptionSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    results: List[SubscriptionSyncResult]


class CollectionInfo(BaseModel):
    """A saved collection over the wire, plus its live match count."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    source: Optional[str] = None
    where: Optional[str] = None
    grep: Optional[str] = None
    has_stream: Optional[bool] = None
    explicit: Optional[bool] = None
    created_at: str
    count: int = 0


class CollectionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    collections: List[CollectionInfo]


class CollectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    where: Optional[str] = None
    source: Optional[str] = None
    grep: Optional[str] = None
    has_stream: Optional[bool] = None
    explicit: Optional[bool] = None
    description: Optional[str] = None


class CollectionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class CollectionEntriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    entries: List[MediaEntry]
