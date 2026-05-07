"""Run enrichers across the DB and merge results into ``_meta.enriched``."""
from __future__ import annotations

import logging
from enum import Enum
from typing import Sequence, Set, Tuple

from media_archivist.enrich.content_type import classify_youtube_row
from media_archivist.enrich.lyrics import fetch_bandcamp_lyrics
from media_archivist.enrich.transcripts import fetch_youtube_transcript
from media_archivist.models.enriched import EnrichedBlock
from media_archivist.storage import EnvelopeJsonStorage

LOG = logging.getLogger("media_archivist.enrich")


class EnrichKind(str, Enum):
    LYRICS = "lyrics"
    TRANSCRIPTS = "transcripts"
    CONTENT_TYPE = "content_type"


def available_enrichers() -> Set[EnrichKind]:
    """Subset of enrichers whose external dependency is importable."""
    out: Set[EnrichKind] = set()
    try:
        import py_bandcamp  # noqa: F401
        out.add(EnrichKind.LYRICS)
    except ImportError:
        pass
    import shutil
    if shutil.which("yt-dlp"):
        out.add(EnrichKind.TRANSCRIPTS)
    try:
        import tutubo.content_type  # noqa: F401
        out.add(EnrichKind.CONTENT_TYPE)
    except ImportError:
        pass
    return out


def _existing_enriched(row: dict) -> EnrichedBlock:
    meta = row.get("_meta") or {}
    raw = meta.get("enriched") or {}
    try:
        return EnrichedBlock.model_validate(raw)
    except Exception:
        return EnrichedBlock()


def _store_enriched(row: dict, block: EnrichedBlock) -> None:
    meta = dict(row.get("_meta") or {})
    meta["enriched"] = block.model_dump(mode="json", exclude_none=True)
    row["_meta"] = meta


def enrich(db_path: str, kinds: Sequence[EnrichKind], *,
           limit: int = 0,
           overwrite: bool = False,
           languages: Sequence[str] = ("en",)) -> Tuple[int, int]:
    """Run the requested enrichers across the DB.

    Returns ``(rows_processed, rows_modified)``. Skips rows where the
    requested block already exists unless ``overwrite=True``.
    """
    db = EnvelopeJsonStorage(db_path)
    processed = 0
    modified = 0
    for url, row in list(db.items()):
        if limit and processed >= limit:
            break
        processed += 1
        block = _existing_enriched(row)
        new_block = block.model_copy(deep=True)
        source = row.get("source") or ""
        changed = False

        if EnrichKind.LYRICS in kinds and (overwrite or new_block.lyrics is None):
            if source == "bandcamp":
                got = fetch_bandcamp_lyrics(url)
                if got is not None:
                    new_block.lyrics = got
                    changed = True

        if EnrichKind.TRANSCRIPTS in kinds and (overwrite or new_block.transcript is None):
            if source in {"youtube", "youtube_music"}:
                got = fetch_youtube_transcript(url, languages=languages)
                if got is not None:
                    new_block.transcript = got
                    changed = True

        if EnrichKind.CONTENT_TYPE in kinds and (overwrite or new_block.content_type is None):
            if source in {"youtube", "youtube_music"}:
                got = classify_youtube_row(row)
                if got is not None:
                    new_block.content_type = got
                    changed = True

        if changed:
            _store_enriched(row, new_block)
            db[url] = row
            modified += 1

    if modified:
        db.store()
    return processed, modified
