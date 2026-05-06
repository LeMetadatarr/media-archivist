"""Live check: canonicalize a music row against MusicBrainz, verify the
artist landed in the entity sidecar with an mbid.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from media_archivist.canonicalize import canonicalize
from metadatarr.resolve.entities import EntityRole
from media_archivist.storage import EnvelopeJsonStorage


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "music.json"
        db = EnvelopeJsonStorage(str(db_path))
        # No duration — MusicBrainz's top recording for this query is an
        # edit, and we'd quarantine on a runtime mismatch. The point of
        # this check is the entity allocation, not a precise runtime
        # match for the work itself.
        db["https://x.bandcamp.com/track/bohrap"] = {
            "source": "bandcamp",
            "url": "https://x.bandcamp.com/track/bohrap",
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
        }
        db.store()
        canonical, quarantine, entities = canonicalize(
            str(db_path), providers=["musicbrainz"],
        )
    artists = [r for r in entities.entities.values()
               if r.role == EntityRole.ARTIST]
    if not artists:
        print("FAIL: no artist entity allocated", file=sys.stderr)
        return 1
    queen = next((r for r in artists if "queen" in r.name.lower()), artists[0])
    if not queen.external_ids.musicbrainz_artist:
        print(f"FAIL: artist '{queen.name}' has no MBID", file=sys.stderr)
        return 1
    print(f"PASS: artist '{queen.name}' "
          f"mbid={queen.external_ids.musicbrainz_artist} "
          f"works={len(queen.works)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
