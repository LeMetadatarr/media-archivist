"""HuggingFace Hub publisher.

Pushes a JSONL export plus an auto-generated dataset card to a HF dataset
repository. ``huggingface_hub`` is an optional extra; the publisher
fails closed with a clear error when it is missing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from media_archivist.canonicalize import load_canonical, load_quarantine
from media_archivist.index import Index
from media_archivist.models.dataset_card import DEFAULT_LICENSES, DatasetCard

LOG = logging.getLogger("media_archivist.hub")


def build_card(db_path: str, *, name: str, description: str,
               license_id: str = "other") -> DatasetCard:
    """Construct a :class:`DatasetCard` from the DB envelope + sidecars."""
    idx = Index(db_path)
    meta = idx.meta
    sources_used = sorted(meta.source_mix.keys())
    licenses = {s: DEFAULT_LICENSES[s] for s in sources_used if s in DEFAULT_LICENSES}

    canonical_n: Optional[int] = None
    quarantined_n: Optional[int] = None
    try:
        canonical_n = len(load_canonical(db_path).records)
        quarantined_n = len(load_quarantine(db_path).entries)
    except Exception:
        pass

    return DatasetCard(
        name=name,
        description=description,
        license=license_id,
        total_entries=sum(meta.source_mix.values()) or len(idx),
        source_mix=dict(meta.source_mix),
        canonical_records=canonical_n,
        quarantined=quarantined_n,
        licenses_by_source=licenses,
    )


def publish(db_path: str, *, repo: str, jsonl_path: str,
            description: str = "", license_id: str = "other",
            private: bool = False, token: Optional[str] = None) -> str:
    """Push ``jsonl_path`` + an auto-generated README to ``repo`` on HF Hub.

    Returns the dataset URL.
    """
    try:
        from huggingface_hub import HfApi, create_repo  # noqa: WPS433
    except ImportError as e:
        raise RuntimeError(
            "huggingface_hub is required for hub publishing — "
            "install it via `pip install media_archivist[hub]`"
        ) from e

    api = HfApi(token=token)
    create_repo(repo, repo_type="dataset", exist_ok=True, private=private,
                token=token)
    name = repo.split("/", 1)[-1]
    card = build_card(db_path, name=name, description=description,
                      license_id=license_id)

    readme_path = Path(jsonl_path).with_suffix(".README.md")
    readme_path.write_text(card.to_markdown(), encoding="utf-8")

    api.upload_file(path_or_fileobj=str(jsonl_path),
                    path_in_repo="data.jsonl",
                    repo_id=repo, repo_type="dataset")
    api.upload_file(path_or_fileobj=str(readme_path),
                    path_in_repo="README.md",
                    repo_id=repo, repo_type="dataset")
    LOG.info("published %s entries to %s", card.total_entries, repo)
    return f"https://huggingface.co/datasets/{repo}"


def split_jsonl(rows: list, split_spec: str) -> dict:
    """Deterministically split ``rows`` by fingerprint hash.

    ``split_spec`` is ``"train:0.8,val:0.1,test:0.1"``. Returns
    ``{"train": [...], "val": [...], "test": [...]}``.
    """
    import hashlib

    parts = []
    total = 0.0
    for piece in split_spec.split(","):
        name, weight = piece.split(":")
        weight_f = float(weight)
        total += weight_f
        parts.append((name.strip(), weight_f))
    if total <= 0:
        raise ValueError("split weights must sum to a positive number")

    cumulative = []
    running = 0.0
    for name, weight_f in parts:
        running += weight_f / total
        cumulative.append((name, running))

    out: dict = {name: [] for name, _ in parts}
    for row in rows:
        # Use canonical id if present, else fall back to row id, else url.
        key = (row.get("canonical_id") or row.get("id")
               or row.get("url") or json.dumps(row, sort_keys=True))
        h = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
        bucket = (h % 10_000) / 10_000.0
        for name, threshold in cumulative:
            if bucket < threshold:
                out[name].append(row)
                break
    return out
