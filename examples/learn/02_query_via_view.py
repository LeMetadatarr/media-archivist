"""Step 2 — read the DB as a unified ``MediaEntry`` view.

The on-disk format is per-backend (RawIaEntry, RawYoutubeEntry, …).
``Index`` projects each row to a ``MediaEntry`` — the unified, source-
agnostic shape — at read time. Filter / iterate without caring which
backend a row came from.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from media_archivist import IAArchivist
from media_archivist.index import Index


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ia.json"
        a = IAArchivist(db_path=str(db_path))
        # Index a couple of public-domain reels.
        for item in ("Popeye_forPresident", "ItsTheGreatPumpkinCharlieBrown"):
            try:
                a.archive(item)
            except Exception as exc:
                print(f"  (skipped {item}: {type(exc).__name__})")
        if not a.db:
            print("SKIP: archive.org unreachable; nothing indexed")
            return

        idx = Index(str(db_path))
        rows = list(idx.view(limit=3))
        print(f"MediaEntry projection (source-agnostic): {len(rows)} rows")
        for entry in rows:
            print(f"  - {entry.source.value:<18} {entry.title[:50]!r}")
            print(f"    duration={entry.duration}  url={entry.url[:60]}…")


if __name__ == "__main__":
    main()
