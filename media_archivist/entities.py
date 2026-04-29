"""Entity sidecar I/O and merge logic."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, List

from media_archivist.models.entities import (
    EntityKind,
    EntityRecord,
    EntitySidecar,
    ProviderEntity,
    Role,
    allocate_entity_id,
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

    Conservative merge:

    - If we have any external id for the candidate, the entity_id is
      derived from it; matching records absorb new aliases/external_ids.
    - Else the entity_id is derived from the normalized name. Same name
      across providers collapses to one entity; the resulting record's
      ``external_ids`` accumulate.
    """
    kind = candidate.kind
    eid = allocate_entity_id(kind, name=candidate.name,
                             external_ids=candidate.external_ids)
    rec = sidecar.entities.get(eid)
    if rec is None:
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
        return
    if canonical_id and canonical_id not in rec.works:
        rec.works.append(canonical_id)
        rec.touch()


def entities_by_kind(sidecar: EntitySidecar, kind: EntityKind) -> List[EntityRecord]:
    return [r for r in sidecar.entities.values() if r.kind == kind]
