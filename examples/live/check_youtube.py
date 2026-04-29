"""Live check: index a few videos from a stable public YouTube channel.

Goes through ``YoutubeArchivist`` end-to-end: tutubo channel scrape,
pydantic validation, envelope write. PASS iff at least one row lands
in the DB.

Run::

    python examples/live/check_youtube.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from media_archivist import YoutubeArchivist

CHANNEL = "https://www.youtube.com/@LinusTechTips"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "yt.json"
        a = YoutubeArchivist(db_path=str(db_path))
        try:
            # Limit by exhausting only the first slice — the iterator is lazy.
            from itertools import islice
            from tutubo.channel import Channel
            ch = Channel(CHANNEL)
            for video in islice(ch.videos, 3):
                a.archive_video(video)
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    n = len(a.video_urls)
    if n == 0:
        print("FAIL: no rows archived", file=sys.stderr)
        return 1
    print(f"PASS: archived {n} rows from {CHANNEL}")
    for url in a.video_urls[:3]:
        print(f"  - {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
