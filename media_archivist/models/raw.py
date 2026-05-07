"""Per-backend pydantic models.

Each model mirrors what its archivist writes today. They are *strict on read*
(``extra="forbid"`` in tests) so a backend that adds a new field without
updating the model fails CI. They are *lenient on construction* (defaults for
optional fields) so callers don't have to spell out every key.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Source(str, Enum):
    YOUTUBE = "youtube"
    YOUTUBE_MUSIC = "youtube_music"
    BANDCAMP = "bandcamp"
    SOUNDCLOUD = "soundcloud"
    INTERNET_ARCHIVE = "internet_archive"


class _RawEntryBase(BaseModel):
    """Common base — every raw entry knows its source and target URL."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source: Source
    url: str
    title: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    thumbnail: Optional[str] = None
    # Free-form per-archive metadata (e.g. ``playlist`` name, ``source_query``)
    # carried alongside the structured fields so callers can round-trip.
    extra: dict = Field(default_factory=dict)


class RawYoutubeEntry(_RawEntryBase):
    source: Literal[Source.YOUTUBE] = Source.YOUTUBE
    videoId: str
    is_live: bool = False
    published: str = ""
    views: str = ""
    description: str = ""
    duration: Optional[float] = None  # seconds, populated from search previews
    author: Optional[str] = None
    playlist: Optional[str] = None


class RawYoutubeMusicEntry(_RawEntryBase):
    source: Literal[Source.YOUTUBE_MUSIC] = Source.YOUTUBE_MUSIC
    videoId: str
    artist: Optional[str] = None
    album: str = ""
    year: Optional[int] = None
    duration: Optional[float] = None
    explicit: bool = False
    video_type: str = ""
    audio_only: bool = False
    music_video: bool = False
    views: str = ""
    playlist: Optional[str] = None
    playlist_id: Optional[str] = None
    album_browse_id: Optional[str] = None
    artist_browse_id: Optional[str] = None
    label: Optional[str] = None


class RawBandcampEntry(_RawEntryBase):
    source: Literal[Source.BANDCAMP] = Source.BANDCAMP
    artist: Optional[str] = None
    album: str = ""
    album_url: Optional[str] = None
    track_number: Optional[int] = None
    duration: Optional[float] = None  # seconds
    stream: Optional[str] = None
    artwork: Optional[str] = None


class RawSoundcloudEntry(_RawEntryBase):
    source: Literal[Source.SOUNDCLOUD] = Source.SOUNDCLOUD
    artist: Optional[str] = None
    artist_url: Optional[str] = None
    duration: Optional[float] = None  # seconds (converted from ms by the archivist)
    stream: Optional[str] = None
    source_query: Optional[str] = None
    source_url: Optional[str] = None


class RawIAEntry(_RawEntryBase):
    source: Literal[Source.INTERNET_ARCHIVE] = Source.INTERNET_ARCHIVE
    collection: Union[str, List[str]] = ""
    duration: Optional[Union[str, float]] = None  # IA returns a "HH:MM:SS" runtime string
    streams: List[str] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)


# Discriminated union — pydantic picks the right model from the ``source``
# field when validating an unknown raw row.
RawEntry = Annotated[
    Union[
        RawYoutubeEntry,
        RawYoutubeMusicEntry,
        RawBandcampEntry,
        RawSoundcloudEntry,
        RawIAEntry,
    ],
    Field(discriminator="source"),
]


def parse_raw(data: dict) -> _RawEntryBase:
    """Validate a raw dict into the right ``Raw*Entry`` subclass.

    The ``source`` field is required — there is no v0.1 inference path.
    """
    src = Source(data["source"])
    cls_map: dict[Source, type[_RawEntryBase]] = {
        Source.YOUTUBE: RawYoutubeEntry,
        Source.YOUTUBE_MUSIC: RawYoutubeMusicEntry,
        Source.BANDCAMP: RawBandcampEntry,
        Source.SOUNDCLOUD: RawSoundcloudEntry,
        Source.INTERNET_ARCHIVE: RawIAEntry,
    }
    return cls_map[src].model_validate(data)
