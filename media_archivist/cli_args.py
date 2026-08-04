"""Pydantic validation for CLI argument bundles.

Each subcommand handler builds the right ``CliArgs`` model from the
``argparse.Namespace`` before dispatching. This catches invalid combos
(no DB target, conflicting backend flags, missing required args) with a
clean error message instead of a deep ``TypeError``.
"""
from __future__ import annotations

import argparse
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Backend = Literal["youtube", "ia", "music", "bandcamp", "soundcloud"]
ExportFormat = Literal["json", "jsonl", "csv", "txt"]


class _BaseCliArgs(BaseModel):
    """Common DB-target + filter fields shared by every subcommand."""

    model_config = ConfigDict(extra="forbid")

    db: Optional[str] = None
    db_file: Optional[str] = None
    backend: Backend = "youtube"
    require: List[str] = Field(default_factory=list)
    blacklist: List[str] = Field(default_factory=list)
    min_duration: int = -1
    skip_explicit: bool = False
    only_audio: bool = False

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "_BaseCliArgs":
        if not self.db and not self.db_file:
            raise ValueError("pass --db NAME or --db-file PATH")
        if self.db and self.db_file:
            raise ValueError("pass --db or --db-file, not both")
        return self


class AddArgs(_BaseCliArgs):
    urls: List[str]


class UrlsArgs(_BaseCliArgs):
    grep: Optional[str] = None
    limit: int = 0
    where: Optional[str] = None
    canonical: bool = False
    has_stream: Optional[bool] = None
    source_filter: Optional[str] = None


class ListArgs(_BaseCliArgs):
    grep: Optional[str] = None
    limit: int = 0
    json_out: bool = False
    where: Optional[str] = None
    canonical: bool = False
    has_stream: Optional[bool] = None
    explicit_filter: Optional[bool] = None
    source_filter: Optional[str] = None


class DumpArgs(_BaseCliArgs):
    pass


class ExportArgs(_BaseCliArgs):
    format: ExportFormat = "jsonl"
    fields: Optional[str] = None
    grep: Optional[str] = None
    limit: int = 0
    output: Optional[str] = None
    where: Optional[str] = None
    canonical: bool = False
    has_stream: Optional[bool] = None
    source_filter: Optional[str] = None
    split: Optional[str] = None
    split_by: Optional[str] = None


class LinkArgs(_BaseCliArgs):
    duration_tolerance: float = 2.0


class ProvidersArgs(BaseModel):
    """media-archivist providers — no DB target needed."""

    model_config = ConfigDict(extra="forbid")


class CanonicalizeArgs(_BaseCliArgs):
    providers: List[str] = Field(default_factory=list)
    no_stamp: bool = False


class QuarantineListArgs(_BaseCliArgs):
    pass


class QuarantineResolveArgs(_BaseCliArgs):
    row_id: str
    canonical_id: Optional[str] = None


class QuarantineRejectArgs(_BaseCliArgs):
    row_id: str


class EnrichArgs(_BaseCliArgs):
    kinds: List[str]
    limit: int = 0
    overwrite: bool = False
    languages: str = "en"

    @model_validator(mode="after")
    def _at_least_one_kind(self) -> "EnrichArgs":
        if not self.kinds:
            raise ValueError(
                "enrich requires at least one --kind: lyrics, transcripts, content_type"
            )
        return self


class SnapshotArgs(_BaseCliArgs):
    label: Optional[str] = None


class DiffArgs(BaseModel):
    """diff doesn't take a DB target — it takes two DB paths positionally."""

    model_config = ConfigDict(extra="forbid")

    a: str
    b: str


class HubPublishArgs(_BaseCliArgs):
    repo: str
    jsonl_path: str
    description: str = ""
    license_id: str = "other"
    private: bool = False


class DiscoverArgs(_BaseCliArgs):
    kind: str
    query: str
    max_results: int = 50


class SyncArgs(_BaseCliArgs):
    rss: bool = False
    max_per_channel: int = 0

    @model_validator(mode="after")
    def _at_least_one_strategy(self) -> "SyncArgs":
        if not self.rss:
            raise ValueError("sync requires --rss")
        return self


class ServeArgs(_BaseCliArgs):
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False


class EntitiesListArgs(_BaseCliArgs):
    kind: Optional[str] = None
    limit: int = 0


class EntitiesShowArgs(_BaseCliArgs):
    entity_id: str


class EntitiesStatsArgs(_BaseCliArgs):
    pass


class StrmExportArgs(_BaseCliArgs):
    output_dir: str
    base_url: Optional[str] = None
    where: Optional[str] = None
    source_filter: Optional[str] = None
    has_stream: Optional[bool] = None
    limit: int = 0
    dry_run: bool = False
    layout: str = "by-source-artist"
    nfo: bool = False


class DedupeArgs(_BaseCliArgs):
    output: str
    prefer: str = "bandcamp,internet_archive,youtube_music,soundcloud,youtube"
    duration_tolerance: float = 2.0


class ImportArgs(_BaseCliArgs):
    path: str
    overwrite: bool = False


class MergeArgs(_BaseCliArgs):
    sources: List[str]
    overwrite: bool = False

    @model_validator(mode="after")
    def _at_least_one_source(self) -> "MergeArgs":
        if not self.sources:
            raise ValueError("merge requires at least one source DB path")
        return self


class StatsArgs(_BaseCliArgs):
    pass


class PruneArgs(_BaseCliArgs):
    unavailable: bool = False
    below: Optional[int] = None
    missing: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_action(self) -> "PruneArgs":
        if not (self.unavailable or self.below is not None or self.missing
                or self.blacklist):
            raise ValueError(
                "prune requires at least one of: --unavailable, --below, "
                "--missing, --blacklist"
            )
        return self


class BootstrapArgs(_BaseCliArgs):
    url: str


class TagLibraryArgs(BaseModel):
    """tag-library — scans a local folder; no DB target required."""

    model_config = ConfigDict(extra="forbid")

    path: str
    media: Literal["both", "video", "music"] = "both"
    nfo: bool = True
    dry_run: bool = False
    index: Optional[str] = None
    min_confidence: float = 0.5


class MonitorArgs(_BaseCliArgs):
    urls: List[str]
    interval: int = 120

    @model_validator(mode="after")
    def _no_ia_for_monitor(self) -> "MonitorArgs":
        if self.backend == "ia":
            raise ValueError("--ia is not supported with monitor")
        return self


def backend_from_namespace(ns: argparse.Namespace) -> Backend:
    """Collapse the mutually-exclusive backend flags into the discriminator."""
    if getattr(ns, "ia", False):
        return "ia"
    if getattr(ns, "music", False):
        return "music"
    if getattr(ns, "bandcamp", False):
        return "bandcamp"
    if getattr(ns, "soundcloud", False):
        return "soundcloud"
    return "youtube"


def from_namespace(model: type[_BaseCliArgs], ns: argparse.Namespace,
                   **overrides) -> _BaseCliArgs:
    """Build a CliArgs model from an argparse namespace."""
    fields = set(model.model_fields)
    raw = {k: v for k, v in vars(ns).items() if k in fields and v is not None}
    raw.setdefault("backend", backend_from_namespace(ns))
    raw.update(overrides)
    return model.model_validate(raw)
