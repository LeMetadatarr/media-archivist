"""Incremental refresh helpers.

``rss_sync`` reads each indexed YouTube channel's RSS feed and only
fetches entries newer than the most-recent ``published`` already stored.
This is order-of-magnitude faster than re-iterating the full channel
grid via tutubo each night.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

import requests
from tutubo.channel import Channel

from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.youtube import YoutubeArchivist, _video_id_from_url

LOG = logging.getLogger("media_archivist.sync")


# RSS feed URL pattern when we know the channel id.
_RSS_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


def _channels_in_db(db: EnvelopeJsonStorage) -> Set[str]:
    """Find every distinct YouTube channel URL referenced by stored entries.

    Returns a set of channel URLs (or canonical channel ids) that we can
    re-fetch RSS for. We rely on rows having an ``author`` or
    ``source_channel`` hint when present; otherwise the row's video page
    would have to be re-fetched, defeating the purpose.
    """
    out: Set[str] = set()
    for row in db.values():
        if row.get("source") != "youtube":
            continue
        # Common shapes carrying the channel pointer.
        for key in ("source_channel", "channel_url", "author"):
            value = row.get(key)
            if isinstance(value, str) and ("youtube.com" in value or value.startswith("UC")):
                out.add(value)
    return out


def _rss_url_for(channel_pointer: str) -> Optional[str]:
    """Build a YouTube RSS URL from a channel id, /channel/ URL, or @handle URL."""
    if channel_pointer.startswith("UC") and len(channel_pointer) == 24:
        return _RSS_TEMPLATE.format(cid=channel_pointer)
    parsed = urlparse(channel_pointer if "://" in channel_pointer
                      else f"https://www.youtube.com{channel_pointer}")
    parts = [p for p in parsed.path.split("/") if p]
    if "channel" in parts:
        idx = parts.index("channel")
        if idx + 1 < len(parts):
            return _RSS_TEMPLATE.format(cid=parts[idx + 1])
    # @handle / /c/ URLs require a one-off fetch via tutubo to discover the id.
    try:
        return Channel(channel_pointer).rss_url or None
    except Exception:
        return None


def _parse_rss(xml_text: str) -> List[dict]:
    """Return a list of ``{video_id, url, title, published}`` from a YT RSS feed."""
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: List[dict] = []
    for entry in root.findall("a:entry", ns):
        vid_id_el = entry.find("yt:videoId", ns)
        title_el = entry.find("a:title", ns)
        published_el = entry.find("a:published", ns)
        if vid_id_el is None or vid_id_el.text is None:
            continue
        vid_id = vid_id_el.text
        out.append({
            "video_id": vid_id,
            "url": f"https://www.youtube.com/watch?v={vid_id}",
            "title": title_el.text if title_el is not None else "",
            "published": published_el.text if published_el is not None else "",
        })
    return out


def _last_seen_iso(rows: Iterable[dict]) -> Optional[str]:
    """Return the most recent ISO ``published`` timestamp, or None."""
    best: Optional[str] = None
    for row in rows:
        ts = row.get("published")
        if isinstance(ts, str) and len(ts) >= 10:
            try:
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if best is None or ts > best:
                best = ts
    return best


def rss_sync(db_path: str, *, max_per_channel: int = 0,
             required_kwords=None,
             blacklisted_kwords=None,
             min_duration: int = -1) -> Dict[str, int]:
    """Pull each channel's RSS feed; archive entries we don't already have.

    Returns ``{channel_pointer: n_added}``.
    """
    db = EnvelopeJsonStorage(db_path)
    pointers = _channels_in_db(db)
    if not pointers:
        LOG.info("no channel pointers found in DB; nothing to sync via RSS")
        return {}

    archivist = YoutubeArchivist(
        db_path=db_path,
        required_kwords=required_kwords,
        blacklisted_kwords=blacklisted_kwords,
        min_duration=min_duration,
    )
    known_ids: Set[str] = set()
    for url in archivist.video_urls:
        try:
            known_ids.add(_video_id_from_url(url))
        except ValueError:
            continue

    added: Dict[str, int] = {}
    last_seen = _last_seen_iso(db.values())
    for ptr in pointers:
        rss = _rss_url_for(ptr)
        if not rss:
            LOG.warning("could not derive RSS URL for %s", ptr)
            continue
        try:
            resp = requests.get(rss, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            LOG.warning("RSS fetch failed for %s: %s", rss, e)
            continue
        items = _parse_rss(resp.text)
        n = 0
        for item in items:
            if item["video_id"] in known_ids:
                continue
            if last_seen and item["published"] and item["published"] < last_seen:
                continue
            try:
                archivist.archive_video(item["url"])
                n += 1
                if max_per_channel and n >= max_per_channel:
                    break
            except Exception:
                LOG.exception("archive_video failed for %s", item["url"])
        if n:
            added[ptr] = n
    return added
