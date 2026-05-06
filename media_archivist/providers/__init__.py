"""Provider registry — re-export of the metadatarr resolver registry.

The provider framework and every built-in resolver provider live in
``metadatarr.resolve``. media-archivist imports the metadatarr
registry as-is and exposes the same ``all_providers()`` /
``active_providers()`` helpers used by the canonicalize orchestrator.

There are currently no media-archivist-specific providers — every
resolver provider lives in metadatarr.
"""
from __future__ import annotations

from typing import Dict, List

# Importing metadatarr.resolve.providers triggers self-registration of
# every built-in provider (musicbrainz, wikidata, tmdb, anilist, jikan,
# google_books, librivox, apple_podcasts, arr_*, discogs, bluray_com,
# dvdcompare, openlibrary, annas_archive, bandcamp, soundcloud,
# youtube, youtube_music, metal_archives, audiodb, tvmaze, …).
import metadatarr.resolve.providers  # noqa: F401
from metadatarr.resolve.base import (
    MetadataProvider,
    ProviderMatch,
    _REGISTRY,
    register,
)


def all_providers() -> Dict[str, MetadataProvider]:
    return dict(_REGISTRY)


def active_providers() -> List[MetadataProvider]:
    return [p for p in all_providers().values() if p.is_available()]


__all__ = [
    "MetadataProvider",
    "ProviderMatch",
    "register",
    "all_providers",
    "active_providers",
]
