"""TMDB provider — free key required (`MEDIA_ARCHIVIST_TMDB_KEY`).

Looks up movies + TV series by title (+ optional year). Returns
TMDB id, IMDb tt-id (when joined), runtime, country, year.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers.base import (
    MetadataProvider,
    ProviderMatch,
    register,
)

LOG = logging.getLogger("media_archivist.providers.tmdb")
_BASE = "https://api.themoviedb.org/3"


class TmdbProvider(MetadataProvider):
    name = "tmdb"
    media = {Medium.MOVIE, Medium.TV}

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.environ.get("MEDIA_ARCHIVIST_TMDB_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, **params) -> Dict[str, Any]:
        params = {"api_key": self.api_key, **params}
        resp = requests.get(f"{_BASE}{path}", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self.api_key and signals.title):
            return None
        kind = signals.medium
        try:
            if kind == Medium.MOVIE:
                return self._lookup_movie(signals)
            if kind == Medium.TV:
                return self._lookup_tv(signals)
            # Try both, pick the higher-popularity result.
            m = self._lookup_movie(signals)
            t = self._lookup_tv(signals)
            if m and t:
                return m if (m.confidence >= t.confidence) else t
            return m or t
        except requests.RequestException as e:
            LOG.warning("TMDB lookup failed: %s", e)
            return None

    def _lookup_movie(self, signals: Signals) -> Optional[ProviderMatch]:
        params = {"query": signals.title}
        if signals.year:
            params["primary_release_year"] = signals.year
        if signals.language:
            params["language"] = signals.language
        data = self._get("/search/movie", **params)
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        details = self._get(f"/movie/{top['id']}", append_to_response="external_ids")
        runtime_min = details.get("runtime")
        countries = details.get("production_countries") or []
        return ProviderMatch(
            provider=self.name,
            confidence=min(1.0, (top.get("popularity") or 1.0) / 100.0),
            signals=Signals(
                title=details.get("title"),
                year=int((details.get("release_date") or "0000")[:4]) or None,
                country=(countries[0]["iso_3166_1"] if countries else None),
                runtime=runtime_min * 60.0 if runtime_min else None,
                medium=Medium.MOVIE,
                language=details.get("original_language"),
            ),
            external_ids=ExternalIds(
                tmdb_movie=details["id"],
                imdb=(details.get("external_ids") or {}).get("imdb_id"),
            ),
        )

    def _lookup_tv(self, signals: Signals) -> Optional[ProviderMatch]:
        params = {"query": signals.title}
        if signals.year:
            params["first_air_date_year"] = signals.year
        if signals.language:
            params["language"] = signals.language
        data = self._get("/search/tv", **params)
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        details = self._get(f"/tv/{top['id']}", append_to_response="external_ids")
        countries = details.get("origin_country") or []
        runtimes = details.get("episode_run_time") or []
        return ProviderMatch(
            provider=self.name,
            confidence=min(1.0, (top.get("popularity") or 1.0) / 100.0),
            signals=Signals(
                title=details.get("name"),
                year=int((details.get("first_air_date") or "0000")[:4]) or None,
                country=countries[0] if countries else None,
                runtime=runtimes[0] * 60.0 if runtimes else None,
                medium=Medium.TV,
                language=details.get("original_language"),
            ),
            external_ids=ExternalIds(
                tmdb_tv=details["id"],
                imdb=(details.get("external_ids") or {}).get("imdb_id"),
                tvdb=(details.get("external_ids") or {}).get("tvdb_id"),
            ),
        )


register(TmdbProvider())
