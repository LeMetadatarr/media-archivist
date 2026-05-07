"""Step 1 — index a single Internet Archive item.

Each archivist class wraps one upstream source. ``IAArchivist.archive(id)``
fetches metadata, normalises it, and writes one row to a JSON DB. The
DB is the persistence layer; canonicalisation comes later (see step 3).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from media_archivist import IAArchivist


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ia.json"
        a = IAArchivist(db_path=str(db_path))
        try:
            a.archive("Popeye_forPresident")      # known-stable public-domain reel
        except Exception as exc:
            print(f"SKIP: archive.org unreachable ({type(exc).__name__})")
            return
        if "Popeye_forPresident" not in a.db:
            print("SKIP: archive.org returned no metadata for the item")
            return
        row = a.db["Popeye_forPresident"]

    print(f"Archived 1 row from Internet Archive")
    print(f"  title:   {row.get('title')!r}")
    print(f"  streams: {len(row.get('streams') or [])}")
    print(f"  source:  {row.get('source')!r}")
    print(f"\nThe DB stores raw source-shaped rows. Step 2 will read them as")
    print(f"a canonical MediaEntry view.")


if __name__ == "__main__":
    main()
