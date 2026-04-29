"""Build a documentaries dataset programmatically.

Useful when you want to enrich entries with extra fields (license, source
channel, topic tags) before exporting.

Run: python examples/index_documentaries.py
"""
from __future__ import annotations

import json
from pathlib import Path

from media_archivist import YoutubeArchivist

HERE = Path(__file__).parent
DB_PATH = HERE / "documentaries.json"

CHANNELS = {
    "https://www.youtube.com/@FreeDocumentary": ["general"],
    "https://www.youtube.com/@FDSpace": ["space", "science"],
    "https://www.youtube.com/@FreeDocumentaryOcean": ["ocean", "nature"],
}


def main() -> None:
    archivist = YoutubeArchivist(
        db_path=str(DB_PATH),
        blacklisted_kwords=["#shorts", "trailer"],
    )

    for channel_url, topics in CHANNELS.items():
        before = len(archivist.video_urls)
        archivist.archive_channel(channel_url)
        new_urls = set(archivist.video_urls) - set(archivist.video_urls[:before])
        # tag the entries we just added with their source channel + topics
        for url in new_urls:
            entry = archivist.db[url]
            entry["source_channel"] = channel_url
            entry["topics"] = topics
            entry["license"] = "see channel — Free Documentary network"
            archivist.db[url] = entry
        archivist.db.store()
        print(f"  + {channel_url}: {len(new_urls)} new entries")

    # Export JSONL alongside the DB
    out_path = HERE / "documentaries.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for entry in archivist.sorted_entries():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"wrote {out_path} ({len(archivist.video_urls)} rows)")


if __name__ == "__main__":
    main()
