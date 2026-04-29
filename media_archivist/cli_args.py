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


class LinkArgs(_BaseCliArgs):
    duration_tolerance: float = 2.0


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


class MonitorArgs(_BaseCliArgs):
    urls: List[str]
    interval: int = 120

    @model_validator(mode="after")
    def _no_ia_for_monitor(self) -> "MonitorArgs":
        if self.backend == "ia":
            raise ValueError("--ia is not supported with monitor")
        return self


def backend_from_namespace(ns: argparse.Namespace) -> Backend:
    """Collapse the four mutually-exclusive backend flags into the discriminator."""
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
