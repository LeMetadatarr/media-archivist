"""URL parsing helpers across YouTube and YT Music."""
from __future__ import annotations

import pytest

from media_archivist.music import _browse_id_from_url, _playlist_id_from_url
from media_archivist.youtube import _video_id_from_url


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=abc123", "abc123"),
    ("https://youtu.be/xyz789", "xyz789"),
    ("https://www.youtube.com/shorts/SHORT1", "SHORT1"),
    ("https://www.youtube.com/embed/EMBD42", "EMBD42"),
    ("https://www.youtube.com/live/LIVE99", "LIVE99"),
    ("https://www.youtube.com/watch?v=foo&list=PLx", "foo"),
])
def test_video_id_extraction(url, expected):
    assert _video_id_from_url(url) == expected


def test_video_id_invalid():
    with pytest.raises(ValueError):
        _video_id_from_url("https://www.youtube.com/")


def test_playlist_id_extraction():
    assert _playlist_id_from_url(
        "https://music.youtube.com/playlist?list=PLABC123"
    ) == "PLABC123"


def test_browse_id_extraction():
    assert _browse_id_from_url(
        "https://music.youtube.com/browse/MPREb_xxx"
    ) == "MPREb_xxx"
    assert _browse_id_from_url(
        "https://music.youtube.com/channel/UC123"
    ) == "UC123"
