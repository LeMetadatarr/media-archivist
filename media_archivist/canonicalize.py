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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from mediavocab import MediaType
from media_archivist.entities import (
    attach_work,
    load_entities,
    save_entities,
    upsert_entity,
)
from media_archivist.index import Index
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.canonical_record import (
    CanonicalRecord,
    CanonicalSidecar,
    ProviderHit,
    QuarantineEntry,
    QuarantineSidecar,
)
from metadatarr.resolve.entities import EntitySidecar
from mediavocab.models import ExternalIds
from mediavocab import MediaType
from mediavocab.models.signals import Signals, compare_signals as compare, merge_signals as merged, signal_hash
from media_archivist.providers import active_providers, all_providers
from metadatarr.resolve.base import MetadataProvider, ProviderMatch
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

_SOURCE_TO_MEDIUM: Dict[str, MediaType] = {
    "youtube": MediaType.GENERIC,        # YT is mixed; default OTHER unless tags hint
    "youtube_music": MediaType.MUSIC,
    "bandcamp": MediaType.MUSIC,
    "soundcloud": MediaType.MUSIC,
    "internet_archive": MediaType.GENERIC,
}

# Maps content_type classifier values (stored in _meta.enriched.content_type)
# or explicit medium tags to (MediaType, content_genres) tuples. Cultural
# genres (anime, manga) live in content_genres per spec axiom 2; the
# MediaType reflects the canonical schema (EPISODIC_SERIES for anime,
# COMIC for manga).
_CONTENT_TYPE_TO_MEDIUM: Dict[str, Tuple[MediaType, List[str]]] = {
    "movie": (MediaType.MOVIE, []),
    "film": (MediaType.MOVIE, []),
    "tv": (MediaType.EPISODIC_SERIES, []),
    "tv_show": (MediaType.EPISODIC_SERIES, []),
    "series": (MediaType.EPISODIC_SERIES, []),
    "music": (MediaType.MUSIC, []),
    "music_video": (MediaType.MUSIC_VIDEO, []),
    "book": (MediaType.BOOK, []),
    "audiobook": (MediaType.AUDIOBOOK, []),
    "audio_book": (MediaType.AUDIOBOOK, []),
    "podcast": (MediaType.PODCAST, []),
    "audiodrama": (MediaType.AUDIO_DRAMA, []),
    "audio_drama": (MediaType.AUDIO_DRAMA, []),
    "anime": (MediaType.EPISODIC_SERIES, ["anime"]),
    "manga": (MediaType.COMIC, ["manga"]),
    "game": (MediaType.GAME, []),
    "video_game": (MediaType.GAME, []),
}


def signals_from_entry(entry: MediaEntry) -> Signals:
    """Extract a Signals bag from a canonical row."""
    medium = _SOURCE_TO_MEDIUM.get(entry.source.value, MediaType.GENERIC)
    content_genres: List[str] = []

    # Honour an explicit medium tag written by a prior enrichment pass or by
    # the user into _meta.medium / _meta.enriched.content_type.label.
    raw = entry.raw or {}
    meta = raw.get("_meta") or {}
    explicit_medium = (
        meta.get("medium")
        or ((meta.get("enriched") or {}).get("content_type") or {}).get("label")
    )
    if explicit_medium:
        mapped = _CONTENT_TYPE_TO_MEDIUM.get(str(explicit_medium).lower().strip())
        if mapped:
            medium, extra_genres = mapped
            content_genres = list(extra_genres)

    # Upgrade YouTube/IA rows to MUSIC when they carry music-specific metadata.
    # This lets music providers match them without waiting for a content_type
    # enrichment pass first.
    if medium == MediaType.GENERIC and (entry.album or (entry.artist and entry.duration)):
        medium = MediaType.MUSIC

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
        content_genres=content_genres,
    )


# ---------------------------------------------------------------------------
# Provider orchestration
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_lookup(provider: "MetadataProvider", signals: Signals) -> Optional["ProviderMatch"]:
    try:
        return provider.lookup(signals)
    except Exception:
        LOG.exception("provider %s raised unexpectedly", provider.name)
        return None


def _safe_list_variants(provider: "MetadataProvider",
                        external_ids: ExternalIds,
                        signals: Signals) -> list:
    try:
        return provider.list_variants(external_ids, signals) or []
    except Exception:
        LOG.exception("provider %s list_variants raised unexpectedly", provider.name)
        return []


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


def _providers_for(providers: List[MetadataProvider], medium: Optional[MediaType],
                   content_genres: Optional[List[str]] = None,
                   ) -> List[MetadataProvider]:
    """Return the subset of *providers* that match *(medium, content_genres)*.

    Providers with an empty ``media`` set are treated as universal w.r.t.
    media type. ``genre_filter`` adds an optional secondary gate so
    anime/manga-only providers can be selected without a fake
    ``MediaType.ANIME`` value (see ``MetadataProvider`` docstring).

    When *medium* is ``None`` or ``GENERIC``, the routing is fully
    permissive — every provider is returned so nothing is silently skipped
    during early signal extraction.
    """
    if (not medium) or medium == MediaType.GENERIC:
        return list(providers)
    tags = set(content_genres or [])
    out = []
    for p in providers:
        if p.media and medium not in p.media:
            continue
        if p.genre_filter and not (tags & p.genre_filter):
            continue
        out.append(p)
    return out


def _external_id_conflicts(a: ExternalIds, b: ExternalIds) -> List[str]:
    """Return field names where both sides have non-None values that differ."""
    conflicts = []
    for field in type(a).model_fields:
        if field == "extra":
            continue
        va = getattr(a, field)
        vb = getattr(b, field)
        if va is not None and vb is not None and va != vb:
            conflicts.append(field)
    return conflicts


def _consolidate(matches: List[ProviderMatch], local: Signals
                 ) -> Tuple[Optional[Signals], ExternalIds, List[ProviderHit]]:
    """Merge provider matches; return (consolidated_signals, external_ids, log).

    Returns ``(None, …)`` if providers disagree on signals OR on a shared
    external ID — the caller will quarantine in both cases.
    """
    consolidated = local
    external = ExternalIds()
    log: List[ProviderHit] = []
    for m in matches:
        if compare(consolidated, m.signals):
            return None, external, log
        id_conflicts = _external_id_conflicts(external, m.external_ids)
        if id_conflicts:
            LOG.warning(
                "provider %s conflicts on external id(s) %s — quarantining",
                m.provider, id_conflicts,
            )
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
                 stamp_rows: bool = True,
                 max_workers: int = 8,
                 ) -> Tuple[CanonicalSidecar, QuarantineSidecar, EntitySidecar]:
    """Run providers across every row and update the sidecars.

    Returns the (canonical, quarantine, entities) sidecar triple after
    persisting all three. Stamps ``_meta.canonical_id`` /
    ``_meta.canonical_status`` on each row when ``stamp_rows=True``.
    """
    chosen = _select_providers(providers)
    if not chosen:
        LOG.warning("no providers active — canonicalization is a no-op")
    canonical = load_canonical(db_path)
    quarantine = load_quarantine(db_path)
    entities = load_entities(db_path)

    db = EnvelopeJsonStorage(db_path)
    idx = Index(db_path)
    rows: List[MediaEntry] = list(idx.view())

    for entry in rows:
        local = signals_from_entry(entry)
        # Skip rows we can't match: no title, or music rows with no artist signal.
        if not local.title:
            _stamp(db, entry.url, status="unmatched")
            continue
        if local.medium == MediaType.MUSIC and not local.artist:
            _stamp(db, entry.url, status="unmatched")
            continue

        matches: List[ProviderMatch] = []
        eligible = _providers_for(chosen, local.medium, local.content_genres)
        n_workers = min(len(eligible), max_workers)
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_safe_lookup, p, local): p for p in eligible}
            for fut in as_completed(futures):
                m = fut.result()
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
        for hit in log:
            rec.log_hit(hit)
        # Merge provider-supplied relations into the entity sidecar.
        for match in verified:
            for role, candidates in (match.relations or {}).items():
                for cand in candidates:
                    # Force the candidate's role to match the relations-dict key
                    # so the role-key on ProviderEntity always agrees with where
                    # it ended up in the relations map.
                    if cand.role != role:
                        cand = cand.model_copy(update={"role": role})
                    eid = upsert_entity(entities, cand)
                    rec.add_relation(role, eid)
                    attach_work(entities, eid, canonical_id)

        # Fan out to variant-aware providers when requested.
        # Check both the raw local signals and the consolidated result (a
        # provider match may have set include_variants=True via merged()).
        if local.include_variants or consolidated.include_variants:
            from metadatarr.resolve.entities import EntityRole as _EK
            variant_eligible = _providers_for(chosen, local.medium, local.content_genres)
            n_workers = min(len(variant_eligible), max_workers)
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                vfuts = {
                    pool.submit(_safe_list_variants, p, rec.external_ids, local): p
                    for p in variant_eligible
                }
                for vfut in as_completed(vfuts):
                    for variant in vfut.result():
                        if variant.role != _EK.RELEASE:
                            variant = variant.model_copy(update={"role": _EK.RELEASE})
                        eid = upsert_entity(entities, variant)
                        rec.add_relation(_EK.RELEASE, eid)
                        attach_work(entities, eid, canonical_id)

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
    save_entities(db_path, entities)
    return canonical, quarantine, entities


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
    url = _build_row_id_index(db).get(row_id)
    if url is not None:
        _stamp(db, url, status="matched", canonical_id=target_id)
        db.store()
    else:
        LOG.warning("quarantine_resolve: row_id %s not found in db", row_id)
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
    url = _build_row_id_index(db).get(row_id)
    if url is not None:
        _stamp(db, url, status="matched", canonical_id=new_id)
        db.store()
    else:
        LOG.warning("quarantine_reject: row_id %s not found in db", row_id)
    save_canonical(db_path, canonical)
    save_quarantine(db_path, quarantine)
    return True


def _build_row_id_index(db: EnvelopeJsonStorage) -> Dict[str, str]:
    """Build a {row_id → url} index in a single O(n) pass.

    Used by quarantine_resolve/reject so they don't each pay O(n).
    """
    from media_archivist.models.canonical import stable_id
    from media_archivist.models.raw import Source
    index: Dict[str, str] = {}
    for url, row in db.items():
        try:
            s = Source(row.get("source"))
        except Exception:
            LOG.debug("_build_row_id_index: skipping row with unknown source at %s", url)
            continue
        index[stable_id(s, url)] = url
    return index
