"""Pydantic model for external authoritative IDs.

Known fields are first-class so the schema is explicit; unknown ones
land in :attr:`extra` (string -> string), so a new provider can ship
without breaking validation.
"""
from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExternalIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # MusicBrainz
    musicbrainz_recording: Optional[str] = None
    musicbrainz_release: Optional[str] = None
    musicbrainz_release_group: Optional[str] = None
    musicbrainz_work: Optional[str] = None
    musicbrainz_artist: Optional[str] = None

    # Video
    imdb: Optional[str] = None             # tt-id
    tmdb_movie: Optional[int] = None
    tmdb_tv: Optional[int] = None
    tvdb: Optional[int] = None

    # Books
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None
    olid: Optional[str] = None
    goodreads: Optional[str] = None

    # Linked-data hub
    wikidata: Optional[str] = None         # Q-id

    # People (TMDB/IMDb person ids when known via film/TV providers).
    tmdb_person: Optional[int] = None
    imdb_person: Optional[str] = None      # nm-id

    # Encyclopaedia Metallum (metal-archives.com) ids.
    metal_archives_band: Optional[int] = None
    metal_archives_release: Optional[int] = None
    metal_archives_song: Optional[int] = None
    metal_archives_label: Optional[int] = None
    metal_archives_artist: Optional[int] = None  # MA artist (lineup member) id

    # Anything else a provider produced that we don't have a slot for.
    extra: Dict[str, str] = Field(default_factory=dict)

    def merge(self, other: "ExternalIds") -> "ExternalIds":
        """Field-wise merge — non-None values from ``other`` override absent ones."""
        out = self.model_copy(deep=True)
        for name in type(self).model_fields:
            if name == "extra":
                continue
            cur = getattr(out, name)
            new = getattr(other, name)
            if cur in (None, "") and new not in (None, ""):
                setattr(out, name, new)
        merged_extra = dict(out.extra)
        merged_extra.update(other.extra)
        out.extra = merged_extra
        return out

    def is_empty(self) -> bool:
        return self == ExternalIds()
