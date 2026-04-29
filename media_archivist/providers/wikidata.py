"""Wikidata provider — free, no key. Q-id + cross-references (IMDb, TMDB,
TVDB, MB) when available.

Uses the ``wbsearchentities`` API to find candidate Q-ids by title,
then ``wbgetentities`` to read the cross-reference claims.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers.base import (
    MetadataProvider,
    ProviderMatch,
    register,
)

LOG = logging.getLogger("media_archivist.providers.wikidata")
_API = "https://www.wikidata.org/w/api.php"
_HEADERS = {
    "User-Agent": "media_archivist/0.1 ( https://github.com/TigreGotico/media-archivist )",
    "Accept": "application/json",
}

# Wikidata property → ExternalIds field mapping.
_PROP_MAP = {
    "P345": "imdb",                 # IMDb ID
    "P4947": "tmdb_movie",          # TMDB movie ID
    "P4983": "tmdb_tv",             # TMDB TV series ID
    "P4835": "tvdb",                # TVDB series ID
    "P436": "musicbrainz_release_group",  # MB release group ID
    "P434": "musicbrainz_artist",   # MB artist ID
    "P435": "musicbrainz_work",     # MB work ID
    "P648": "olid",                 # Open Library ID
    "P212": "isbn_13",
    "P957": "isbn_10",
    "P2969": "goodreads",
}


class WikidataProvider(MetadataProvider):
    name = "wikidata"
    media = {Medium.MOVIE, Medium.TV, Medium.MUSIC, Medium.BOOK, Medium.PODCAST}

    def is_available(self) -> bool:
        return True

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not signals.title:
            return None
        try:
            search = requests.get(_API, params={
                "action": "wbsearchentities",
                "search": signals.title,
                "language": signals.language or "en",
                "format": "json",
                "limit": 5,
            }, headers=_HEADERS, timeout=20).json()
        except requests.RequestException as e:
            LOG.warning("Wikidata search failed: %s", e)
            return None

        hits = search.get("search") or []
        if not hits:
            return None
        qid = hits[0]["id"]

        try:
            entity = requests.get(_API, params={
                "action": "wbgetentities",
                "ids": qid,
                "props": "claims|labels",
                "format": "json",
            }, headers=_HEADERS, timeout=20).json()
        except requests.RequestException as e:
            LOG.warning("Wikidata entity fetch failed: %s", e)
            return None

        claims = (entity.get("entities", {}).get(qid, {}).get("claims") or {})
        external = ExternalIds(wikidata=qid)
        for prop, field in _PROP_MAP.items():
            stmts = claims.get(prop) or []
            if not stmts:
                continue
            try:
                value = stmts[0]["mainsnak"]["datavalue"]["value"]
            except (KeyError, TypeError, IndexError):
                continue
            # Numeric IDs come as strings from Wikidata; coerce where needed.
            if field in {"tmdb_movie", "tmdb_tv", "tvdb"}:
                try:
                    setattr(external, field, int(value))
                except (TypeError, ValueError):
                    pass
            else:
                setattr(external, field, str(value))

        # Wikidata search results carry the entity's English title via "label".
        label = hits[0].get("label") or signals.title
        return ProviderMatch(
            provider=self.name,
            confidence=0.7,  # search hit, no scoring API
            signals=Signals(title=label),
            external_ids=external,
        )


register(WikidataProvider())
