"""Step 4 — query the entity sidecar.

Canonicalisation produces an ``EntitySidecar`` keyed by stable entity
id. Two utility functions group records:

- ``entities_by_role(side, EntityRole.X)`` — by relational role
  (DIRECTOR, ACTOR, AUTHOR, ARTIST, LABEL, …).
- ``entities_by_kind(side, EntityKind.X)`` — by structural mediavocab
  kind (PERSON / GROUP / ORGANISATION / SERIES / DEVICE / OTHER).

The two-axis split (``role`` vs ``kind``) was the EntityRole cleanup
landed earlier in 0.3.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from media_archivist.canonicalize import canonicalize
from media_archivist.entities import entities_by_role, entities_by_kind
from media_archivist.storage import EnvelopeJsonStorage
from mediavocab import EntityKind
from metadatarr.resolve.entities import EntityRole


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "music.json"
        db = EnvelopeJsonStorage(str(db_path))
        for url, title, artist in (
            ("https://x.bandcamp.com/album/bohrap",        "Bohemian Rhapsody", "Queen"),
            ("https://x.bandcamp.com/album/whilemyguitar", "While My Guitar Gently Weeps", "The Beatles"),
        ):
            db[url] = {"source": "bandcamp", "url": url, "title": title, "artist": artist}
        db.store()

        _, _, entities = canonicalize(str(db_path), providers=["musicbrainz"])

    artists = entities_by_role(entities, EntityRole.ARTIST)
    print(f"Artists in sidecar: {len(artists)}")
    for a in artists:
        print(f"  - {a.name:<20} mbid={a.external_ids.musicbrainz_artist}  works={len(a.works)}")

    print(f"\nGroup by structural kind (mediavocab.EntityKind):")
    for kind in (EntityKind.PERSON, EntityKind.GROUP, EntityKind.ORGANISATION):
        recs = entities_by_kind(entities, kind)
        if recs:
            print(f"  {kind.value:<14} {len(recs)} records")


if __name__ == "__main__":
    main()
