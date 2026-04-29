"""CliArgs pydantic validators — invalid combos are rejected with clear errors."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from media_archivist.cli_args import (
    AddArgs,
    ExportArgs,
    MergeArgs,
    MonitorArgs,
    PruneArgs,
)


def test_db_target_required():
    with pytest.raises(ValidationError, match="--db NAME or --db-file PATH"):
        AddArgs(urls=["u"])


def test_db_targets_mutually_exclusive():
    with pytest.raises(ValidationError, match="not both"):
        AddArgs(db="a", db_file="b", urls=["u"])


def test_prune_requires_action():
    with pytest.raises(ValidationError, match="at least one of"):
        PruneArgs(db_file="x.json")


def test_prune_with_blacklist_ok():
    PruneArgs(db_file="x.json", blacklist=["spam"])


def test_monitor_rejects_ia_backend():
    with pytest.raises(ValidationError, match="not supported with monitor"):
        MonitorArgs(db_file="x.json", urls=["u"], backend="ia")


def test_merge_requires_sources():
    with pytest.raises(ValidationError, match="at least one source"):
        MergeArgs(db_file="x.json", sources=[])


def test_export_format_constrained():
    with pytest.raises(ValidationError):
        ExportArgs(db_file="x.json", format="parquet")  # type: ignore[arg-type]


def test_extras_forbidden():
    with pytest.raises(ValidationError, match="Extra inputs"):
        AddArgs(db_file="x.json", urls=["u"], surprise=True)  # type: ignore[call-arg]
