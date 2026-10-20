# SPDX-License-Identifier: Apache-2.0
"""New videos the indexer finds also become rows of the JSON database.

The JSON database stays the archive: the Web UI, the CLI and the exports
read it. One store per listing, under the same lock the metadata fill uses,
on a freshly loaded copy, so a row written by another writer in between is
kept.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from media_archivist.ytindex.client import VideoItem, watch_url


def add_rows(json_path: Path, items: Iterable[VideoItem], *, channel: Optional[str] = None,
             playlist: Optional[str] = None) -> int:
    """Write a row for every video the database lacks; returns how many were written."""
    from media_archivist import ytmeta
    from media_archivist.models import RawYoutubeEntry
    from media_archivist.storage import EnvelopeJsonStorage

    items = list(items)
    if not items:
        return 0
    with ytmeta._write_lock:
        db = EnvelopeJsonStorage(str(json_path))
        added = 0
        for item in items:
            url = watch_url(item.video_id)
            if url in db:
                continue
            db[url] = RawYoutubeEntry(
                url=url, videoId=item.video_id, title=item.title,
                thumbnail=item.thumbnail, published=item.published or "",
                duration=item.duration, author=channel, playlist=playlist,
            ).model_dump(mode="json")
            added += 1
        if added:
            db.store()
    return added
