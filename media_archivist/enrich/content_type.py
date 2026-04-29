"""Content-type enrichment — wraps ``tutubo.content_type.classify_video``."""
from __future__ import annotations

import logging
from typing import Optional

from media_archivist.models.enriched import ContentTypeBlock

LOG = logging.getLogger("media_archivist.enrich.content_type")


def classify_youtube_row(row: dict) -> Optional[ContentTypeBlock]:
    """Run tutubo's classifier against a raw YouTube row."""
    try:
        from tutubo.content_type import classify_video  # noqa: WPS433
    except ImportError:
        LOG.warning("tutubo not installed — content_type enrichment skipped")
        return None
    title = row.get("title") or ""
    description = row.get("description") or ""
    is_live = bool(row.get("is_live"))
    channel_tags = row.get("tags") or []
    try:
        result = classify_video(
            title=title,
            description=description,
            is_live=is_live,
            channel_tags=channel_tags,
        )
    except Exception:
        LOG.exception("classify_video crashed on row")
        return None
    label = getattr(result, "name", None) or str(result)
    return ContentTypeBlock(label=label.lower())
