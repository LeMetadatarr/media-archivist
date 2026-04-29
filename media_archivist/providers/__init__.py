"""External metadata providers — registry and lookup helpers.

Every provider is built into core; missing API keys / endpoint URLs
disable the relevant provider at runtime via :meth:`is_available`. Use
:func:`active_providers` to discover what is currently usable.
"""
from __future__ import annotations

from typing import Dict, List

from media_archivist.providers.base import (
    MetadataProvider,
    ProviderMatch,
    register,
)
# Import every built-in provider so it self-registers.
from media_archivist.providers.musicbrainz import MusicBrainzProvider  # noqa: F401
from media_archivist.providers.wikidata import WikidataProvider          # noqa: F401
from media_archivist.providers.tmdb import TmdbProvider                  # noqa: F401
from media_archivist.providers.arr import (                              # noqa: F401
    SonarrProvider,
    RadarrProvider,
    ReadarrProvider,
    LidarrProvider,
)


def all_providers() -> Dict[str, MetadataProvider]:
    from media_archivist.providers.base import _REGISTRY
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
