"""Dated snapshots of a DB and a structural diff between two snapshots."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from media_archivist.storage import EnvelopeJsonStorage


def snapshot(db_path: str, *, label: str = "") -> Path:
    """Copy the DB into ``<db_dir>/.snapshots/YYYYMMDDTHHMMSS[-label].json``."""
    src = Path(db_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    snap_dir = src.parent / ".snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    name = f"{stamp}-{label}.json" if label else f"{stamp}.json"
    out = snap_dir / name
    shutil.copy2(src, out)
    return out


def diff(a_path: str, b_path: str) -> Dict[str, List[str]]:
    """Compare two DB files; return added / removed / changed URLs."""
    a = EnvelopeJsonStorage(a_path)
    b = EnvelopeJsonStorage(b_path)
    a_keys = set(a.keys())
    b_keys = set(b.keys())
    added = sorted(b_keys - a_keys)
    removed = sorted(a_keys - b_keys)
    changed: List[str] = []
    for k in sorted(a_keys & b_keys):
        if _normalize(a[k]) != _normalize(b[k]):
            changed.append(k)
    return {"added": added, "removed": removed, "changed": changed}


def _normalize(row: Dict) -> str:
    """Hash a row ignoring volatile ``_meta`` fields like ``last_synced``."""
    cleaned = {k: v for k, v in row.items() if k != "_meta"}
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False)
