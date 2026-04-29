"""Canonicalize orchestrator — runs providers, applies quarantine policy,
maintains the canonical/quarantine sidecars and stamps ``_meta.canonical_*``
on each row.

The source DB is otherwise untouched (raw fields are not perturbed). Only
``_meta.canonical_id`` and ``_meta.canonical_status`` are written into the
row, so diffs stay minimal.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from media_archivist.index import Index
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.canonical_record import (
    CanonicalRecord,
    CanonicalSidecar,
    ProviderHit,
    QuarantineEntry,
    QuarantineSidecar,
)
from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import (
    Medium,
    Signals,
    compare,
    merged,
    signal_hash,
)
from media_archivist.providers import active_providers, all_providers
from media_archivist.providers.base import MetadataProvider, ProviderMatch
from media_archivist.storage import EnvelopeJsonStorage

LOG = logging.getLogger("media_archivist.canonicalize")


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------

def _canonical_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".canonical.json")


def _quarantine_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".quarantine.json")


def load_canonical(db_path: str) -> CanonicalSidecar:
    p = _canonical_path(db_path)
    if not p.exists():
        return CanonicalSidecar()
    return CanonicalSidecar.model_validate(json.loads(p.read_text()))


def save_canonical(db_path: str, sidecar: CanonicalSidecar) -> Path:
    p = _canonical_path(db_path)
    p.write_text(sidecar.model_dump_json(indent=2))
    return p


def load_quarantine(db_path: str) -> QuarantineSidecar:
    p = _quarantine_path(db_path)
    if not p.exists():
        return QuarantineSidecar()
    return QuarantineSidecar.model_validate(json.loads(p.read_text()))


def save_quarantine(db_path: str, sidecar: QuarantineSidecar) -> Path:
    p = _quarantine_path(db_path)
    p.write_text(sidecar.model_dump_json(indent=2))
    return p


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

_SOURCE_TO_MEDIUM: Dict[str, Medium] = {
    "youtube": Medium.OTHER,        # YT is mixed; default OTHER unless tags hint
    "youtube_music": Medium.MUSIC,
    "bandcamp": Medium.MUSIC,
    "soundcloud": Medium.MUSIC,
    "internet_archive": Medium.OTHER,
}


def signals_from_entry(entry: MediaEntry) -> Signals:
    """Extract a Signals bag from a canonical row."""
    medium = _SOURCE_TO_MEDIUM.get(entry.source.value, Medium.OTHER)
    raw_year = None
    if entry.published:
        try:
            raw_year = int(str(entry.published)[:4])
        except (TypeError, ValueError):
            raw_year = None
    return Signals(
        title=entry.title or None,
        artist=entry.artist or None,
        runtime=entry.duration,
        year=raw_year,
        medium=medium,
    )


# ---------------------------------------------------------------------------
# Provider orchestration
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _select_providers(names: Optional[Sequence[str]]) -> List[MetadataProvider]:
    if not names:
        return active_providers()
    registry = all_providers()
    out: List[MetadataProvider] = []
    for n in names:
        if n not in registry:
            raise ValueError(f"unknown provider: {n}")
        p = registry[n]
        if not p.is_available():
            LOG.warning("provider %s is not available (missing key/url); skipping", n)
            continue
        out.append(p)
    return out


def _consolidate(matches: List[ProviderMatch], local: Signals
                 ) -> Tuple[Optional[Signals], ExternalIds, List[ProviderHit]]:
    """Merge provider matches; return (consolidated_signals, external_ids, log).

    Returns ``(None, …)`` if any pair of provider matches disagree on a
    shared signal — the caller will quarantine.
    """
    consolidated = local
    external = ExternalIds()
    log: List[ProviderHit] = []
    for m in matches:
        conflicts = compare(consolidated, m.signals)
        if conflicts:
            return None, external, log
        consolidated = merged(consolidated, m.signals)
        external = external.merge(m.external_ids)
        log.append(ProviderHit(provider=m.provider, matched_at=_utcnow(),
                               confidence=m.confidence))
    return consolidated, external, log


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonicalize(db_path: str, *,
                 providers: Optional[Sequence[str]] = None,
                 stamp_rows: bool = True) -> Tuple[CanonicalSidecar, QuarantineSidecar]:
    """Run providers across every row and update the sidecars.

    Returns the (canonical, quarantine) sidecar pair after persisting both.
    Stamps ``_meta.canonical_id`` / ``_meta.canonical_status`` on each row
    when ``stamp_rows=True``.
    """
    chosen = _select_providers(providers)
    if not chosen:
        LOG.warning("no providers active — canonicalization is a no-op")
    canonical = load_canonical(db_path)
    quarantine = load_quarantine(db_path)

    db = EnvelopeJsonStorage(db_path)
    idx = Index(db_path)
    rows: List[MediaEntry] = list(idx.view())

    for entry in rows:
        local = signals_from_entry(entry)
        if not (local.title and (local.artist or local.medium not in {Medium.MUSIC})):
            _stamp(db, entry.url, status="unmatched")
            continue

        matches: List[ProviderMatch] = []
        for p in chosen:
            try:
                m = p.lookup(local)
            except Exception:
                LOG.exception("provider %s blew up on %s", p.name, entry.url)
                continue
            if m is not None:
                matches.append(m)

        # Verify each match against our local signals; collect conflicts.
        verified: List[ProviderMatch] = []
        first_conflict = None
        for m in matches:
            conflicts = compare(local, m.signals)
            if conflicts:
                first_conflict = (m, conflicts)
                continue
            verified.append(m)

        if first_conflict is not None and not verified:
            # Provider returned but disagreed — quarantine.
            m, conflicts = first_conflict
            cand_signals = merged(local, m.signals)
            cid = signal_hash(cand_signals)
            quarantine.entries[entry.id] = QuarantineEntry(
                row_id=entry.id,
                candidate_canonical_id=cid,
                conflicts=conflicts,
                proposed_signals=cand_signals,
            )
            _stamp(db, entry.url, status="quarantined")
            continue

        consolidated, external, log = _consolidate(verified, local)
        if consolidated is None:
            # Two providers disagreed with each other — quarantine.
            quarantine.entries[entry.id] = QuarantineEntry(
                row_id=entry.id,
                conflicts=[],
                proposed_signals=local,
            )
            _stamp(db, entry.url, status="quarantined")
            continue

        canonical_id = signal_hash(consolidated)
        rec = canonical.records.get(canonical_id) or CanonicalRecord(
            canonical_id=canonical_id, signals=consolidated,
        )
        rec.signals = merged(rec.signals, consolidated)
        rec.external_ids = rec.external_ids.merge(external)
        if entry.id not in rec.members:
            rec.members.append(entry.id)
        rec.provider_log.extend(log)
        rec.touch()
        canonical.records[canonical_id] = rec

        # Clear any prior quarantine for this row — we matched.
        quarantine.entries.pop(entry.id, None)

        if stamp_rows:
            _stamp(db, entry.url, status="matched", canonical_id=canonical_id)

    if stamp_rows:
        db.store()
    save_canonical(db_path, canonical)
    save_quarantine(db_path, quarantine)
    return canonical, quarantine


def _stamp(db: EnvelopeJsonStorage, url: str, *,
           status: str, canonical_id: Optional[str] = None) -> None:
    row = db.get(url)
    if row is None:
        return
    meta = dict(row.get("_meta") or {})
    meta["canonical_status"] = status
    if canonical_id is not None:
        meta["canonical_id"] = canonical_id
    elif status != "matched":
        meta.pop("canonical_id", None)
    row["_meta"] = meta
    db[url] = row


# ---------------------------------------------------------------------------
# Quarantine resolution
# ---------------------------------------------------------------------------

def quarantine_resolve(db_path: str, row_id: str,
                       canonical_id: Optional[str] = None) -> bool:
    """Accept a quarantined row.

    With ``canonical_id`` provided, link to that existing record. With it
    omitted, allocate a new canonical_id from the proposed signals.
    """
    canonical = load_canonical(db_path)
    quarantine = load_quarantine(db_path)
    if row_id not in quarantine.entries:
        return False
    qe = quarantine.entries.pop(row_id)
    target_id = canonical_id or qe.candidate_canonical_id
    if not target_id and qe.proposed_signals:
        target_id = signal_hash(qe.proposed_signals)
    if not target_id:
        return False

    rec = canonical.records.get(target_id) or CanonicalRecord(
        canonical_id=target_id,
        signals=qe.proposed_signals or Signals(),
    )
    if row_id not in rec.members:
        rec.members.append(row_id)
    rec.touch()
    canonical.records[target_id] = rec

    db = EnvelopeJsonStorage(db_path)
    url = _row_id_to_url(db, row_id)
    if url is not None:
        _stamp(db, url, status="matched", canonical_id=target_id)
        db.store()
    save_canonical(db_path, canonical)
    save_quarantine(db_path, quarantine)
    return True


def quarantine_reject(db_path: str, row_id: str) -> bool:
    """Reject the quarantine proposal — force a new, distinct canonical_id.

    The new id is derived from the row's locally-observed signals plus a
    random salt so it can never collide with the rejected proposal.
    """
    canonical = load_canonical(db_path)
    quarantine = load_quarantine(db_path)
    if row_id not in quarantine.entries:
        return False
    qe = quarantine.entries.pop(row_id)
    salt = hashlib.sha1(f"reject:{row_id}:{_utcnow()}".encode()).hexdigest()[:8]
    base = qe.proposed_signals or Signals()
    new_id = hashlib.sha1(f"{signal_hash(base)}|{salt}".encode()).hexdigest()
    rec = CanonicalRecord(
        canonical_id=new_id,
        signals=base,
        members=[row_id],
    )
    canonical.records[new_id] = rec

    db = EnvelopeJsonStorage(db_path)
    url = _row_id_to_url(db, row_id)
    if url is not None:
        _stamp(db, url, status="matched", canonical_id=new_id)
        db.store()
    save_canonical(db_path, canonical)
    save_quarantine(db_path, quarantine)
    return True


def _row_id_to_url(db: EnvelopeJsonStorage, row_id: str) -> Optional[str]:
    """Reverse-lookup: each row's MediaEntry id is sha1(source:url)."""
    from media_archivist.models.canonical import stable_id
    from media_archivist.models.raw import Source
    for url, row in db.items():
        try:
            s = Source(row.get("source"))
        except Exception:
            continue
        if stable_id(s, url) == row_id:
            return url
    return None
