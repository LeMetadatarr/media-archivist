"""Live check: index a known Internet Archive item."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from media_archivist import IAArchivist

# A long-stable public-domain reel.
ITEM_ID = "Popeye_forPresident"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ia.json"
        a = IAArchivist(db_path=str(db_path))
        try:
            a.archive(ITEM_ID)
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    if ITEM_ID not in a.db:
        print(f"FAIL: archive_item({ITEM_ID}) did not write a row")
        return 1
    print(f"PASS: archived IA item {ITEM_ID}")
    row = a.db[ITEM_ID]
    print(f"  title={row.get('title')!r} streams={len(row.get('streams') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
