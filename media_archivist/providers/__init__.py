"""External metadata providers — registry and lookup helpers.

The provider framework and all generic external-API providers live in
``metadatarr.resolve``. media-archivist imports the metadatarr registry
so that ``from metadatarr.resolve.providers`` triggers self-registration
of every built-in provider, and exposes them through the same
``all_providers()`` / ``active_providers()`` helpers used by the
canonicalize orchestrator.

The only media-archivist-specific provider that remains here is
``metalarchives`` (uses the ``pymetal`` scraper). Future media-archivist-
internal providers can be added next to it.
"""
from __future__ import annotations

from typing import Dict, List

# Importing metadatarr.resolve.providers triggers self-registration of every
# generic provider (musicbrainz, wikidata, tmdb, anilist, jikan, …).
import metadatarr.resolve.providers  # noqa: F401
from metadatarr.resolve.base import (
    MetadataProvider,
    ProviderMatch,
    _REGISTRY,
    register,
)

# media-archivist-specific providers — self-register on import.
try:
    from media_archivist.providers.metalarchives import (  # noqa: F401
        MetalArchivesProvider,
    )
except ImportError:  # pragma: no cover — pymetal is optional
    pass


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
