"""Live check: search Encyclopaedia Metallum and archive 1 album of songs."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    from media_archivist import MetalArchivesArchivist
    if MetalArchivesArchivist is None:
        raise ImportError
except ImportError:
    print("SKIP: install media_archivist[metal_archives]", file=sys.stderr)
    raise SystemExit(0)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ma.json"
        a = MetalArchivesArchivist(db_path=str(db_path))
        # Mayhem's first studio LP — small, stable.
        # /albums/Mayhem/De_Mysteriis_Dom_Sathanas/<id>
        try:
            a.archive_album(434)  # Wikidata-confirmed release id (legacy)
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        n = len(a.video_urls)
    if n == 0:
        # Try a different release if the first wasn't reachable.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ma.json"
            a = MetalArchivesArchivist(db_path=str(db_path))
            try:
                a.archive_search("Mayhem", limit=1)
            except Exception as exc:
                print(f"FAIL fallback: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                return 1
            n = len(a.video_urls)
    if n == 0:
        print("FAIL: no songs archived from metal-archives", file=sys.stderr)
        return 1
    print(f"PASS: archived {n} metal-archives song rows")
    for url in a.video_urls[:3]:
        print(f"  - {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
