"""Round-trip every Raw*Entry through model_validate / model_dump."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from media_archivist.models import (
    MediaArchive,
    RawBandcampEntry,
    RawIAEntry,
    RawSoundcloudEntry,
    RawYoutubeEntry,
    RawYoutubeMusicEntry,
    Source,
    parse_raw,
)
from pydantic import ValidationError as _PydanticValidationError


def test_youtube_round_trip():
    e = RawYoutubeEntry(url="https://www.youtube.com/watch?v=abc", videoId="abc",
                        title="t", duration=300, author="A")
    dumped = e.model_dump(mode="json")
    assert dumped["source"] == "youtube"
    assert dumped["videoId"] == "abc"
    again = RawYoutubeEntry.model_validate(dumped)
    assert again == e


def test_music_round_trip():
    e = RawYoutubeMusicEntry(url="https://music.youtube.com/watch?v=x",
                             videoId="x", title="song", artist="A",
                             album="Alb", duration=200, explicit=True)
    again = RawYoutubeMusicEntry.model_validate(e.model_dump(mode="json"))
    assert again.album == "Alb" and again.explicit is True


def test_bandcamp_round_trip():
    e = RawBandcampEntry(url="https://x.bandcamp.com/track/y", title="t",
                         duration=300, stream="s")
    again = RawBandcampEntry.model_validate(e.model_dump(mode="json"))
    assert again.source == Source.BANDCAMP


def test_soundcloud_round_trip():
    e = RawSoundcloudEntry(url="https://soundcloud.com/x/y", title="t",
                           duration=120.5)
    again = RawSoundcloudEntry.model_validate(e.model_dump(mode="json"))
    assert abs(again.duration - 120.5) < 1e-6


def test_ia_round_trip():
    e = RawIAEntry(url="https://archive.org/details/foo", title="F",
                   collection="c", streams=["a", "b"], images=["i"])
    again = RawIAEntry.model_validate(e.model_dump(mode="json"))
    assert again.streams == ["a", "b"]


def test_source_discriminator_rejects_mismatch():
    with pytest.raises(ValidationError):
        RawYoutubeEntry.model_validate({"source": "bandcamp", "url": "u",
                                        "videoId": "x"})


def test_parse_raw_dispatches_on_source():
    """The ``source`` field is the discriminator; URL inference is not done."""
    bc = parse_raw({"source": "bandcamp",
                    "url": "https://x.bandcamp.com/track/y", "title": "t"})
    assert bc.source == Source.BANDCAMP
    yt = parse_raw({"source": "youtube",
                    "url": "https://www.youtube.com/watch?v=q",
                    "videoId": "q", "title": "h"})
    assert yt.source == Source.YOUTUBE


def test_parse_raw_requires_source():
    with pytest.raises((KeyError, ValueError)):
        parse_raw({"url": "https://x.bandcamp.com/track/y", "title": "t"})


def test_archive_envelope_rejects_legacy_bare_mapping():
    """Legacy bare-mapping shape is no longer accepted."""
    with pytest.raises(_PydanticValidationError):
        MediaArchive.load_dict({"https://www.youtube.com/watch?v=a": {
            "source": "youtube", "url": "...", "videoId": "a", "title": "t",
        }})


def test_archive_envelope_v2_round_trip():
    arc = MediaArchive()
    arc.entries["u"] = {"source": "bandcamp", "url": "u", "title": "t"}
    arc.recompute_source_mix()
    arc.touch()
    dumped = arc.dump_dict()
    assert dumped["_meta"]["source_mix"] == {"bandcamp": 1}
    assert dumped["_meta"]["last_synced"] is not None
    again = MediaArchive.load_dict(dumped)
    assert again.entries == arc.entries
