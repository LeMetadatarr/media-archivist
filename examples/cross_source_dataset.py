"""Build a cross-source music dataset and emit a deduped canonical JSONL.

This recipe shows how to:

1. Index the same artist on **three** sources (YouTube Music, Bandcamp,
   SoundCloud).
2. Run :func:`media_archivist.dedupe.link` to fingerprint duplicates across
   sources.
3. Run :func:`media_archivist.dedupe.dedupe` to emit a canonical JSONL,
   preferring sources that ship a direct stream URL.

Run::

    pip install media_archivist py_bandcamp nuvem_de_som
    python examples/cross_source_dataset.py "Aphex Twin"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from media_archivist import (
    BandcampArchivist,
    SoundCloudArchivist,
    YoutubeMusicArchivist,
)
from media_archivist.dedupe import dedupe, link, write_dedupe_jsonl

HERE = Path(__file__).parent


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: cross_source_dataset.py <artist query>", file=sys.stderr)
        return 2
    query = " ".join(argv)
    db_path = HERE / "cross_source.json"

    print(f"==> indexing {query!r} from three sources into {db_path}")
    YoutubeMusicArchivist(db_path=str(db_path)).archive_search(query)
    BandcampArchivist(db_path=str(db_path)).archive_search(query)
    SoundCloudArchivist(db_path=str(db_path)).archive_search(query)

    print("==> linking cross-source matches")
    links = link(str(db_path))
    print(f"   {len(links)} fingerprint groups across "
          f"{sum(len(v) for v in links.values())} entries")

    print("==> deduping (preferring direct-stream sources)")
    deduped = dedupe(str(db_path))
    out_path = HERE / "cross_source.canonical.jsonl"
    n = write_dedupe_jsonl(deduped, str(out_path))
    print(f"==> wrote {n} canonical rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
