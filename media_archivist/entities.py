"""Entity sidecar I/O and merge logic."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, List

from metadatarr.resolve.entities import (
    EntityKind,
    EntityRecord,
    EntitySidecar,
    ProviderEntity,
    Role,
    allocate_entity_id,
    _normalize_name,
)

LOG = logging.getLogger("media_archivist.entities")


def _entities_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".entities.json")


def load_entities(db_path: str) -> EntitySidecar:
    p = _entities_path(db_path)
    if not p.exists():
        return EntitySidecar()
    return EntitySidecar.model_validate(json.loads(p.read_text()))


def save_entities(db_path: str, sidecar: EntitySidecar) -> Path:
    p = _entities_path(db_path)
    p.write_text(sidecar.model_dump_json(indent=2))
    return p


def upsert_entity(sidecar: EntitySidecar, candidate: ProviderEntity, *,
                  role_hint: Role | None = None) -> str:
    """Insert or update a :class:`ProviderEntity`; return its ``entity_id``.

    Merge strategy (in order):

    1. External-ID match — if we already have a record whose id was derived
       from the same dominant external id, absorb new aliases/external_ids
       into it.
    2. Name-based collapse — if two providers give *different* external ids
       for the same entity (e.g. AniList's ``anilist_studio_id`` vs Jikan's
       ``mal_studio_id`` for "Sunrise"), the second upsert finds an existing
       record of the same kind with a matching normalized name and merges
       into it, accumulating both ids on one record.
    3. New record — genuinely unseen entity; allocate and insert.
    """
    kind = candidate.kind
    eid = allocate_entity_id(kind, name=candidate.name,
                             external_ids=candidate.external_ids)
    rec = sidecar.entities.get(eid)
    if rec is None:
        # Secondary: look for same kind + normalized name already under a
        # different id (cross-provider id mismatch for the same real entity).
        norm = _normalize_name(candidate.name)
        existing = next(
            (r for r in sidecar.entities.values()
             if r.kind == kind and _normalize_name(r.name) == norm),
            None,
        )
        if existing is not None:
            existing.merge_alias(candidate.name)
            existing.external_ids = existing.external_ids.merge(candidate.external_ids)
            existing.touch()
            return existing.id
        rec = EntityRecord(
            id=eid,
            kind=kind,
            name=candidate.name,
            external_ids=candidate.external_ids,
        )
        sidecar.entities[eid] = rec
    else:
        rec.merge_alias(candidate.name)
        rec.external_ids = rec.external_ids.merge(candidate.external_ids)
        rec.touch()
    return eid


def attach_work(sidecar: EntitySidecar, entity_id: str, canonical_id: str) -> None:
    rec = sidecar.entities.get(entity_id)
    if rec is None:
        LOG.warning("attach_work: entity %s not found — was upsert_entity called first?",
                    entity_id)
        return
    if canonical_id and canonical_id not in rec.works:
        rec.works.append(canonical_id)
        rec.touch()


def entities_by_kind(sidecar: EntitySidecar, kind: EntityKind) -> List[EntityRecord]:
    return [r for r in sidecar.entities.values() if r.kind == kind]
