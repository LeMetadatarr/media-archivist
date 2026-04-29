"""Discover + RSS-sync surface — pure unit tests, no network."""
from __future__ import annotations

import pytest

from media_archivist.discover import _resolve, supported_kinds
from media_archivist.sync import _last_seen_iso, _parse_rss, _rss_url_for


def test_supported_kinds_is_non_empty():
    kinds = list(supported_kinds())
    assert "documentaries" in kinds
    assert "podcasts" in kinds
    assert len(kinds) >= 20


def test_resolve_known_kind():
    factory, iterator = _resolve("documentaries")
    assert factory == "for_documentaries"
    assert iterator == "iterate_documentaries"


def test_resolve_unknown_kind():
    with pytest.raises(ValueError, match="unknown discover kind"):
        _resolve("nope")


def test_rss_url_from_channel_id():
    url = _rss_url_for("UC" + "A" * 22)
    assert url == "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAAAAAAAAAAAAAAAAAAAAA"


def test_rss_url_from_channel_url():
    url = _rss_url_for("https://www.youtube.com/channel/UCXYZ123")
    assert url and "channel_id=UCXYZ123" in url


def test_parse_rss_extracts_entries():
    xml = (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
        '<entry>'
        '<yt:videoId>abc123</yt:videoId>'
        '<title>Hello</title>'
        '<published>2026-01-15T12:00:00+00:00</published>'
        '</entry>'
        '<entry>'
        '<yt:videoId>def456</yt:videoId>'
        '<title>World</title>'
        '<published>2026-01-16T12:00:00+00:00</published>'
        '</entry>'
        '</feed>'
    )
    items = _parse_rss(xml)
    assert len(items) == 2
    assert items[0]["video_id"] == "abc123"
    assert items[0]["url"] == "https://www.youtube.com/watch?v=abc123"
    assert items[1]["title"] == "World"


def test_parse_rss_handles_garbage():
    assert _parse_rss("not xml at all") == []


def test_last_seen_iso_picks_max():
    rows = [
        {"published": "2026-01-15T00:00:00+00:00"},
        {"published": "2026-02-01T00:00:00+00:00"},
        {"published": ""},
        {"published": "garbage"},
        {},
    ]
    assert _last_seen_iso(rows) == "2026-02-01T00:00:00+00:00"
