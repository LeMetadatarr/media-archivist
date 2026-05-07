"""Drive ``media_archivist`` from a Python script with the same validation
guarantees as the CLI.

The CLI's pydantic argument models are public. Build them programmatically
to fail fast on invalid combinations — no need to spawn the CLI as a
subprocess just to get input validation.

Run::

    python examples/scripted_export.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from media_archivist import YoutubeArchivist
from media_archivist.cli_args import ExportArgs

HERE = Path(__file__).parent
DB = HERE / "documentaries.json"


def main() -> int:
    if not DB.exists():
        print(f"run examples/index_documentaries.sh first to populate {DB}",
              file=sys.stderr)
        return 1

    # Validate parameters BEFORE doing any I/O. Invalid combos raise a
    # pydantic ValidationError with a clear message.
    args = ExportArgs(
        db_file=str(DB),
        format="csv",
        fields="videoId,title,url,published",
        limit=50,
    )

    archivist = YoutubeArchivist(db_path=args.db_file)
    fields = [f.strip() for f in (args.fields or "").split(",") if f.strip()]
    rows = archivist.sorted_entries()[: args.limit] if args.limit else archivist.sorted_entries()

    out = HERE / "documentaries_top50.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in rows:
            writer.writerow({k: entry.get(k) for k in fields})
    print(f"wrote {out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
