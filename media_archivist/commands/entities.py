"""Entity sidecar CLI command handlers."""
from __future__ import annotations

import json
import sys

from media_archivist.commands._helpers import _index_for


def cmd_entities_list(args) -> int:
    """Dump the entity sidecar as JSON (optionally filtered by --kind)."""
    from media_archivist.entities import load_entities

    db_path = args.db_file or _index_for(args).path
    sidecar = load_entities(db_path)
    rows = list(sidecar.entities.values())
    if args.kind:
        rows = [r for r in rows if r.kind.value == args.kind]
    if args.limit:
        rows = rows[: args.limit]
    json.dump([r.model_dump(mode="json") for r in rows], sys.stdout,
              indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_entities_show(args) -> int:
    """Show one entity by id, plus the works it participates in."""
    from media_archivist.canonicalize import load_canonical
    from media_archivist.entities import load_entities

    db_path = args.db_file or _index_for(args).path
    sidecar = load_entities(db_path)
    entity = sidecar.entities.get(args.entity_id)
    if entity is None:
        print(f"entity {args.entity_id} not found", file=sys.stderr)
        return 1
    canonical = load_canonical(db_path)
    works = [canonical.records[wid] for wid in entity.works
             if wid in canonical.records]
    out = {
        "entity": entity.model_dump(mode="json"),
        "works": [{"canonical_id": w.canonical_id,
                   "title": w.signals.title,
                   "members": w.members} for w in works],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_entities_stats(args) -> int:
    from media_archivist.entities import load_entities

    db_path = args.db_file or _index_for(args).path
    sidecar = load_entities(db_path)
    by_kind: dict[str, int] = {}
    works_per_kind: dict[str, int] = {}
    for rec in sidecar.entities.values():
        by_kind[rec.kind.value] = by_kind.get(rec.kind.value, 0) + 1
        works_per_kind[rec.kind.value] = (
            works_per_kind.get(rec.kind.value, 0) + len(rec.works)
        )
    json.dump({"total": len(sidecar.entities),
               "by_kind": by_kind,
               "works_per_kind": works_per_kind},
              sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
