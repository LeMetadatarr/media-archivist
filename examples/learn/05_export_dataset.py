"""Step 5 — export the canonical view as a Hugging Face-style dataset.

After indexing + canonicalising, the canonical records are stable enough
to ship as a typed dataset. ``hf_dataset.py`` (sibling example) shows
the production pipeline; this one demonstrates the minimal shape.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from media_archivist.canonicalize import canonicalize
from media_archivist.index import Index
from media_archivist.storage import EnvelopeJsonStorage


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "music.json"
        db = EnvelopeJsonStorage(str(db_path))
        # Synthesise music rows; bandcamp-shaped is enough for canonicalize()
        # to project a Signals(medium=MUSIC) and route to musicbrainz.
        for url, title, artist in (
            ("https://x.bandcamp.com/album/bohrap",        "Bohemian Rhapsody",         "Queen"),
            ("https://x.bandcamp.com/album/whilemyguitar", "While My Guitar Gently Weeps", "The Beatles"),
        ):
            db[url] = {"source": "bandcamp", "url": url,
                       "title": title, "artist": artist}
        db.store()

        canonical, _, _ = canonicalize(str(db_path), providers=["musicbrainz"])

        # Export — every record becomes one row of typed JSON.
        records = []
        for url, rec in canonical.records.items():
            ids = rec.external_ids.model_dump(exclude_none=True, exclude_defaults=True)
            ids.pop("extra", None)
            records.append({
                "canonical_id": rec.canonical_id,
                "url":          url,
                "title":        rec.signals.title,
                "external_ids": ids,
            })

    print(f"Exported {len(records)} canonical records:")
    print(json.dumps(records, indent=2))
    print(f"\nThe full pipeline (examples/hf_dataset.py) ships this shape as a")
    print(f"Hugging Face dataset for downstream consumers.")


if __name__ == "__main__":
    main()
