"""Step 3 — canonicalise rows against external sources.

``canonicalize(db_path, providers=[...])`` walks every row in your DB,
builds a ``Signals`` from the source-shaped fields, asks each provider
for cross-references, consolidates the answers, and writes a
canonical-record sidecar next to the DB.

The result: rows that previously had only a YouTube URL now carry an
imdb / tmdb_movie / wikidata Q-id and a stable canonical_id.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from media_archivist.canonicalize import canonicalize
from media_archivist.storage import EnvelopeJsonStorage


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "music.json"
        db = EnvelopeJsonStorage(str(db_path))
        # Synthesise a single bandcamp-shaped row so we don't depend on
        # any specific scrape.
        db["https://example.bandcamp.com/album/bohrap"] = {
            "source": "bandcamp",
            "url":    "https://example.bandcamp.com/album/bohrap",
            "title":  "Bohemian Rhapsody",
            "artist": "Queen",
        }
        db.store()

        canonical, quarantine, entities = canonicalize(
            str(db_path),
            providers=["musicbrainz"],            # keep it short for the demo
        )

    print(f"Canonical records: {len(canonical.records)}")
    print(f"Quarantine:        {len(quarantine.entries)}")
    print(f"Entities sidecar:  {len(entities.entities)}")

    for url, rec in list(canonical.records.items())[:1]:
        print(f"\nFor {url}:")
        print(f"  canonical_id      = {rec.canonical_id[:16]}…")
        print(f"  external_ids      = {rec.external_ids.model_dump(exclude_none=True)}")
        if entities.entities:
            artist = next(iter(entities.entities.values()))
            print(f"  artist entity     = {artist.name}  ({artist.role.value})")
            print(f"  artist mbid       = {artist.external_ids.musicbrainz_artist}")


if __name__ == "__main__":
    main()
