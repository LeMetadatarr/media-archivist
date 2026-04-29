"""Bandcamp lyrics enrichment via :mod:`py_bandcamp`."""
from __future__ import annotations

import logging
from typing import Optional

from media_archivist.models.enriched import LyricsBlock

LOG = logging.getLogger("media_archivist.enrich.lyrics")


def fetch_bandcamp_lyrics(url: str) -> Optional[LyricsBlock]:
    """Return a :class:`LyricsBlock` for a Bandcamp track URL, or ``None``."""
    try:
        from py_bandcamp import BandCamp  # noqa: WPS433
    except ImportError:
        LOG.warning("py_bandcamp not installed — lyrics enrichment skipped")
        return None
    try:
        text = BandCamp.get_track_lyrics(url)
    except Exception:
        LOG.exception("Failed to fetch lyrics for %s", url)
        return None
    if not text or text == "lyrics unavailable":
        return None
    return LyricsBlock(text=text, source="bandcamp")
