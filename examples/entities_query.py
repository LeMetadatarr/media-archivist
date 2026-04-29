"""Query the entity sidecar — "every work by artist X" and similar.

Walks an existing canonicalized DB and demonstrates the entity-aware
query patterns. Run after ``media-archivist canonicalize`` has
populated ``<db>.entities.json``.

Usage::

    python examples/entities_query.py path/to/db.json [artist name]
"""
from __future__ import annotations

import sys
from pathlib import Path

from media_archivist import Index
from media_archivist.entities import load_entities
from media_archivist.models.entities import EntityKind


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: entities_query.py <db.json> [artist substring]",
              file=sys.stderr)
        return 2
    db_path = argv[0]
    needle = argv[1].lower() if len(argv) > 1 else None

    sidecar = load_entities(db_path)
    if not sidecar.entities:
        print(f"no entity sidecar at {Path(db_path).with_suffix('.entities.json')} — "
              f"run `media-archivist canonicalize` first", file=sys.stderr)
        return 1

    print(f"==> {len(sidecar.entities)} entities total")

    by_kind: dict[str, int] = {}
    for r in sidecar.entities.values():
        by_kind[r.kind.value] = by_kind.get(r.kind.value, 0) + 1
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print(f"   {kind:12} {n}")

    artists = [r for r in sidecar.entities.values()
               if r.kind == EntityKind.ARTIST]
    if needle:
        artists = [r for r in artists if needle in r.name.lower()]

    print(f"\n==> top 5 artists{' matching ' + repr(needle) if needle else ''}")
    artists.sort(key=lambda r: -len(r.works))
    for rec in artists[:5]:
        print(f"   {rec.name:40} {len(rec.works)} works "
              f"(mbid={rec.external_ids.musicbrainz_artist})")

    if artists:
        target = artists[0]
        print(f"\n==> works by {target.name!r}")
        idx = Index(db_path)
        where = f'"{target.name}" in relations.artist'
        for entry in idx.view(where=where, limit=10):
            print(f"   [{entry.source.value:9}] {entry.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
