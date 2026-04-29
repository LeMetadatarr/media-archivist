"""Live check: search SoundCloud via nuvem_de_som."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    from media_archivist import SoundCloudArchivist
    if SoundCloudArchivist is None:
        raise ImportError
except ImportError:
    print("SKIP: install media_archivist[soundcloud]", file=sys.stderr)
    raise SystemExit(0)


QUERY = "footwork"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sc.json"
        a = SoundCloudArchivist(db_path=str(db_path))
        try:
            a.archive_search(QUERY, limit=5)
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        n = len(a.video_urls)
    if n == 0:
        print(f"FAIL: soundcloud search '{QUERY}' returned no rows",
              file=sys.stderr)
        return 1
    print(f"PASS: archived {n} soundcloud tracks for '{QUERY}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
