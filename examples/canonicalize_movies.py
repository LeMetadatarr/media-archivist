"""Canonicalize a movie index against keyless movie providers.

Builds a Skyhook/Wikidata-anchored movie dataset:

1. Indexes a YouTube channel of trailers/clips.
2. Runs `media-archivist canonicalize` against the Servarr Skyhook
   proxy plus Wikidata. Both are keyless — no setup, no API tokens.
3. Lists any quarantined rows for review.

Run::

    python examples/canonicalize_movies.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from media_archivist import YoutubeArchivist
from media_archivist.canonicalize import canonicalize
from media_archivist.providers import active_providers

HERE = Path(__file__).parent
DB = HERE / "movies.json"

CHANNELS = [
    "https://www.youtube.com/@FreeDocumentary",
]


def main() -> int:
    print("==> active providers:",
          ", ".join(p.name for p in active_providers()) or "(none)")

    if not DB.exists():
        print(f"==> indexing {len(CHANNELS)} channels into {DB}")
        archivist = YoutubeArchivist(db_path=str(DB))
        for url in CHANNELS:
            archivist.archive(url)

    print("==> canonicalizing")
    canonical, quarantine, entities = canonicalize(
        str(DB),
        providers=["skyhook", "wikidata"],
    )
    print(f"   {len(canonical.records)} canonical records, "
          f"{len(quarantine.entries)} quarantined, "
          f"{len(entities.entities)} entities")

    if quarantine.entries:
        print("==> first 5 quarantined rows:")
        for i, qe in enumerate(list(quarantine.entries.values())[:5]):
            print(f"   {qe.row_id[:10]} conflicts={qe.conflicts}")

    summary = HERE / "movies.canonical.summary.json"
    summary.write_text(json.dumps(
        {
            "total_records": len(canonical.records),
            "with_imdb": sum(1 for r in canonical.records.values()
                             if r.external_ids.imdb),
            "with_tmdb": sum(1 for r in canonical.records.values()
                             if r.external_ids.tmdb_movie),
            "quarantined": len(quarantine.entries),
            "entities_total": len(entities.entities),
            "entities_by_kind": {
                k: sum(1 for r in entities.entities.values() if r.kind.value == k)
                for k in {r.kind.value for r in entities.entities.values()}
            },
        },
        indent=2,
    ))
    print(f"==> wrote summary to {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
