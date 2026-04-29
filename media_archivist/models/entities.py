"""Entity layer — first-class people, albums, labels, channels.

Each :class:`EntityRecord` is a peer to :class:`CanonicalRecord`: it gives
us *our* stable id for an artist / actor / director / album / channel /
label, anchored to authoritative external ids (MusicBrainz mbid, TMDB
person id, Wikidata Q-id, …).

Every :class:`CanonicalRecord` then gains a ``relations`` mapping
``{role: [entity_id, ...]}`` so consumers can ask "every work whose
director is `e_abc`" without scanning freeform strings.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.models.external_ids import ExternalIds


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EntityKind(str, Enum):
    ARTIST = "artist"
    ALBUM = "album"
    LABEL = "label"
    CHANNEL = "channel"
    ACTOR = "actor"
    DIRECTOR = "director"
    PRODUCER = "producer"
    COMPOSER = "composer"
    WRITER = "writer"
    NARRATOR = "narrator"
    HOST = "host"
    AUTHOR = "author"
    OTHER = "other"


# Role on a CanonicalRecord.relations dict — same vocabulary as EntityKind,
# kept as a separate name so the relation can refer to entities of a
# different kind in unusual cases (a "director" role pointing at a
# kind=actor entity is a typo; we still allow it but warn).
Role = EntityKind


def _normalize_name(name: str) -> str:
    import re
    name = (name or "").lower()
    name = re.sub(r"[\W_]+", " ", name, flags=re.UNICODE)
    return " ".join(name.split())


def _dominant_external_id(ext: ExternalIds, kind: EntityKind) -> Optional[str]:
    """Return the most stable external id we know for ``kind``."""
    if kind == EntityKind.ARTIST:
        return (ext.musicbrainz_artist
                or (str(ext.metal_archives_band) if ext.metal_archives_band else None)
                or ext.wikidata
                or ext.extra.get("tmdb_person")
                or ext.extra.get("imdb_person"))
    if kind == EntityKind.ALBUM:
        return (ext.musicbrainz_release_group
                or ext.musicbrainz_release
                or (str(ext.metal_archives_release) if ext.metal_archives_release else None))
    if kind in {EntityKind.ACTOR, EntityKind.DIRECTOR, EntityKind.PRODUCER,
                EntityKind.COMPOSER, EntityKind.WRITER, EntityKind.NARRATOR,
                EntityKind.HOST}:
        return (ext.extra.get("tmdb_person")
                or ext.extra.get("imdb_person")
                or (str(ext.metal_archives_artist) if ext.metal_archives_artist else None)
                or ext.wikidata)
    if kind == EntityKind.AUTHOR:
        return (ext.olid or ext.extra.get("goodreads_author")
                or ext.wikidata)
    if kind == EntityKind.LABEL:
        return ((str(ext.metal_archives_label) if ext.metal_archives_label else None)
                or ext.extra.get("musicbrainz_label") or ext.wikidata)
    if kind == EntityKind.CHANNEL:
        return ext.extra.get("youtube_channel_id")
    return None


def allocate_entity_id(kind: EntityKind, *, name: str = "",
                       external_ids: Optional[ExternalIds] = None) -> str:
    """Deterministic entity id from external ids (preferred) or normalized name."""
    ext = external_ids or ExternalIds()
    dom = _dominant_external_id(ext, kind)
    if dom:
        seed = f"{kind.value}|ext:{dom}"
    else:
        seed = f"{kind.value}|name:{_normalize_name(name)}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


class ProviderEntity(BaseModel):
    """One entity contribution from a single provider response."""

    model_config = ConfigDict(extra="forbid")

    kind: EntityKind
    name: str
    role: Optional[Role] = None
    external_ids: ExternalIds = Field(default_factory=ExternalIds)


class EntityRecord(BaseModel):
    """One entry per *entity* in the ``<db>.entities.json`` sidecar."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EntityKind
    name: str
    aliases: List[str] = Field(default_factory=list)
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    members: List[str] = Field(default_factory=list)  # sub-entity ids (e.g. tracks under an album)
    works: List[str] = Field(default_factory=list)    # canonical_ids this entity participates in
    first_seen: str = Field(default_factory=_utcnow)
    last_updated: str = Field(default_factory=_utcnow)

    def touch(self) -> None:
        self.last_updated = _utcnow()

    def merge_alias(self, name: str) -> None:
        if not name or name == self.name:
            return
        if name in self.aliases:
            return
        self.aliases.append(name)


class EntitySidecar(BaseModel):
    """Top-level shape of ``<db>.entities.json``."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    entities: Dict[str, EntityRecord] = Field(default_factory=dict)
