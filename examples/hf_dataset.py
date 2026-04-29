"""Load an exported JSONL into a 🤗 ``datasets`` Dataset for ML training.

Prereq:
    media-archivist export --db-file documentaries.json --format jsonl \\
        -o documentaries.jsonl

    pip install datasets
"""
from __future__ import annotations

from pathlib import Path

from datasets import Dataset  # type: ignore

JSONL_PATH = Path(__file__).parent / "documentaries.jsonl"


def main() -> None:
    ds = Dataset.from_json(str(JSONL_PATH))
    print(ds)
    print("first row:", ds[0])

    # Example: filter to entries with a non-empty description, keep dataset-friendly fields
    ds = ds.filter(lambda r: bool(r.get("description")))
    ds = ds.map(lambda r: {
        "id": r["videoId"],
        "url": r["url"],
        "title": r["title"],
        "description": r["description"],
        "tags": r.get("tags") or [],
    }, remove_columns=ds.column_names)
    print("after projection:", ds)
    # ds.push_to_hub("you/free-documentaries")  # if you want to publish


if __name__ == "__main__":
    main()
