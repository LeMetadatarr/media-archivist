"""Pydantic models — every value entering or leaving the index is validated.

Layer 1 (raw): one model per backend, mirrors what each archivist writes today.
Layer 2 (canonical): a unified ``MediaEntry`` view computed on read (v0.3+).
Layer 3 (envelope): ``MediaArchive`` wraps the on-disk JSON file.
"""
from media_archivist.models.archive import ArchiveMeta, MediaArchive
from media_archivist.models.entities import (
    EntityKind,
    EntityRecord,
    EntitySidecar,
    ProviderEntity,
    Role,
    allocate_entity_id,
)
from media_archivist.models.raw import (
    RawBandcampEntry,
    RawEntry,
    RawIAEntry,
    RawSoundcloudEntry,
    RawYoutubeEntry,
    RawYoutubeMusicEntry,
    Source,
    parse_raw,
)

__all__ = [
    "ArchiveMeta",
    "MediaArchive",
    "EntityKind",
    "EntityRecord",
    "EntitySidecar",
    "ProviderEntity",
    "Role",
    "allocate_entity_id",
    "RawBandcampEntry",
    "RawEntry",
    "RawIAEntry",
    "RawSoundcloudEntry",
    "RawYoutubeEntry",
    "RawYoutubeMusicEntry",
    "Source",
    "parse_raw",
]
