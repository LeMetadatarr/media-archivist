"""Encyclopaedia Metallum metadata provider.

Looks up music rows against ``metal-archives.com`` via :mod:`pymetal`,
returning Metal-Archives ids (band / release / song / label) and entity
relations (artist, album, label).
"""
from __future__ import annotations

import logging
from typing import Optional

from media_archivist.models.entities import EntityKind, ProviderEntity
from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers.base import (
    MetadataProvider,
    ProviderMatch,
    register,
)

LOG = logging.getLogger("media_archivist.providers.metal_archives")


def _length_to_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    parts = value.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    return None


class MetalArchivesProvider(MetadataProvider):
    name = "metal_archives"
    media = {Medium.MUSIC}

    def __init__(self) -> None:
        try:
            from pymetal import MetalArchives  # noqa: WPS433
            self._client = MetalArchives()
            self._available = True
        except ImportError:
            self._client = None
            self._available = False

    def is_available(self) -> bool:
        return self._available

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self._available and signals.title and signals.artist):
            return None
        if signals.medium and signals.medium != Medium.MUSIC:
            return None

        try:
            song_hits = list(self._client.search_songs(
                song_title=signals.title, band_name=signals.artist,
            ))
        except Exception as exc:
            LOG.warning("metal-archives song search failed: %s", exc)
            return None

        if not song_hits:
            return None

        # search_songs returns (Band, Release, Song) tuples on MA.
        top = song_hits[0]
        band = getattr(top, "band", None)
        release = getattr(top, "release", None)
        song = getattr(top, "song", None)

        match_signals = Signals(
            title=getattr(song, "title", None) or signals.title,
            artist=getattr(band, "name", None),
            runtime=_length_to_seconds(getattr(song, "length", None)),
            country=getattr(band, "country", None),
            medium=Medium.MUSIC,
        )

        external = ExternalIds(
            metal_archives_band=getattr(band, "ma_id", None),
            metal_archives_release=getattr(release, "ma_id", None),
            metal_archives_song=getattr(song, "ma_id", None),
        )

        relations: dict = {}
        if band and band.name:
            relations[EntityKind.ARTIST] = [ProviderEntity(
                kind=EntityKind.ARTIST,
                name=band.name,
                external_ids=ExternalIds(
                    metal_archives_band=band.ma_id,
                ),
            )]
        if release and release.title:
            relations[EntityKind.ALBUM] = [ProviderEntity(
                kind=EntityKind.ALBUM,
                name=release.title,
                external_ids=ExternalIds(
                    metal_archives_release=release.ma_id,
                ),
            )]
        if release and getattr(release, "label_name", None):
            relations[EntityKind.LABEL] = [ProviderEntity(
                kind=EntityKind.LABEL,
                name=release.label_name,
                external_ids=ExternalIds(
                    metal_archives_label=getattr(release, "label_id", None),
                ),
            )]

        return ProviderMatch(
            provider=self.name,
            confidence=0.9 if song_hits else 0.0,
            signals=match_signals,
            external_ids=external,
            relations=relations,
        )


register(MetalArchivesProvider())
