"""Live check: search Bandcamp via py_bandcamp.

PASS iff the search returns at least one track and the BandcampArchivist
writes it to a temp DB.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    from media_archivist import BandcampArchivist
    if BandcampArchivist is None:
        raise ImportError
except ImportError:
    print("SKIP: install media_archivist[bandcamp]", file=sys.stderr)
    raise SystemExit(0)


QUERY = "ambient drone"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bc.json"
        a = BandcampArchivist(db_path=str(db_path))
        try:
            a.archive_search(QUERY, max_results=3)
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        n = len(a.video_urls)
    if n == 0:
        print(f"FAIL: bandcamp search '{QUERY}' returned no archivable tracks",
              file=sys.stderr)
        return 1
    print(f"PASS: archived {n} bandcamp tracks for '{QUERY}'")
    for url in a.video_urls[:3]:
        print(f"  - {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
