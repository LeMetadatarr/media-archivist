"""View adapter edge cases for every backend."""
from __future__ import annotations

from media_archivist.models.raw import Source
from media_archivist.views import to_media_entry


def test_youtube_view_carries_author_as_artist():
    raw = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=a",
        "videoId": "a",
        "title": "T",
        "author": "Some Channel",
        "duration": 300,
    }
    e = to_media_entry(raw)
    assert e.source == Source.YOUTUBE
    assert e.artist == "Some Channel"
    assert e.duration == 300.0


def test_youtube_music_uses_year_as_published():
    raw = {
        "source": "youtube_music",
        "url": "https://music.youtube.com/watch?v=q",
        "videoId": "q",
        "title": "S",
        "artist": "A",
        "year": 1999,
    }
    e = to_media_entry(raw)
    assert e.published == "1999"


def test_bandcamp_falls_back_to_artwork_for_thumbnail():
    raw = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "T",
        "artwork": "https://example/img.jpg",
    }
    e = to_media_entry(raw)
    assert e.thumbnail == "https://example/img.jpg"


def test_soundcloud_carries_artist():
    raw = {
        "source": "soundcloud",
        "url": "https://soundcloud.com/x/y",
        "title": "T",
        "artist": "A",
        "duration": 120.5,
    }
    e = to_media_entry(raw)
    assert e.duration == 120.5
    assert e.artist == "A"


def test_ia_parses_runtime_string():
    raw = {
        "source": "internet_archive",
        "url": "https://archive.org/details/foo",
        "title": "F",
        "duration": "1:02:03",
        "streams": ["https://archive.org/foo.mp4"],
    }
    e = to_media_entry(raw)
    assert e.duration == 3723.0
    assert e.stream == "https://archive.org/foo.mp4"


def test_ia_handles_numeric_runtime():
    raw = {
        "source": "internet_archive",
        "url": "https://archive.org/details/foo",
        "title": "F",
        "duration": 90,
    }
    e = to_media_entry(raw)
    assert e.duration == 90.0


def test_view_preserves_raw_dict():
    raw = {
        "source": "bandcamp",
        "url": "u",
        "title": "T",
        "extra": {"kept": True},
    }
    e = to_media_entry(raw)
    assert e.raw["extra"] == {"kept": True}
