"""Servarr-metadata-proxy provider via :mod:`metarr`.

Hits the public proxies that Sonarr / Radarr / Lidarr query for their
own metadata — no self-hosting, no API keys:

- ``skyhook.sonarr.tv/v1``     → TVDB-shaped series metadata
- ``radarrapi.servarr.com/v1`` → TMDB-shaped movie metadata
- ``api.lidarr.audio/v0.4``    → MusicBrainz-shaped artist metadata

This is the "no-Arr-stack-needed" sibling of the ``arr_*`` providers:
those require the user to run their own Sonarr / Radarr / Lidarr; this
one is always available wherever ``metarr`` imports.
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

LOG = logging.getLogger("media_archivist.providers.metarr")


class MetarrProvider(MetadataProvider):
    """Single provider that dispatches to skyhook / radarr / lidarr / OpenLibrary by medium."""

    name = "metarr"
    media = {Medium.MOVIE, Medium.TV, Medium.MUSIC, Medium.BOOK}

    def __init__(self) -> None:
        try:
            from metarr import ArrMetadataClient, OpenLibraryClient  # noqa: WPS433
            self._client = ArrMetadataClient()
            self._ol = OpenLibraryClient()
            self._available = True
        except ImportError:
            self._client = None
            self._ol = None
            self._available = False

    def is_available(self) -> bool:
        return self._available

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self._available and signals.title):
            return None
        try:
            if signals.medium == Medium.MOVIE:
                return self._lookup_movie(signals)
            if signals.medium == Medium.TV:
                return self._lookup_tv(signals)
            if signals.medium == Medium.MUSIC and signals.artist:
                return self._lookup_artist(signals)
            if signals.medium == Medium.BOOK:
                return self._lookup_book(signals)
            if signals.medium is not None:
                # Caller specified a medium we don't cover (podcast, other, …).
                return None
            # Medium unspecified: try movie first, then tv. Cheap, idempotent.
            for kind_lookup in (self._lookup_movie, self._lookup_tv):
                got = kind_lookup(signals)
                if got is not None:
                    return got
            return None
        except Exception as exc:
            LOG.warning("metarr lookup failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Per-medium helpers
    # ------------------------------------------------------------------

    def _lookup_movie(self, signals: Signals) -> Optional[ProviderMatch]:
        results = self._client.search_movie(signals.title)
        if not results:
            return None
        top = results[0]
        return ProviderMatch(
            provider=self.name,
            confidence=0.85,
            signals=Signals(
                title=top.title,
                year=top.year,
                medium=Medium.MOVIE,
            ),
            external_ids=ExternalIds(
                tmdb_movie=int(top.tmdb_id) if top.tmdb_id else None,
            ),
        )

    def _lookup_tv(self, signals: Signals) -> Optional[ProviderMatch]:
        results = self._client.search_series(signals.title)
        if not results:
            return None
        top = results[0]
        return ProviderMatch(
            provider=self.name,
            confidence=0.85,
            signals=Signals(
                title=top.title,
                year=top.year,
                medium=Medium.TV,
            ),
            external_ids=ExternalIds(
                tvdb=int(top.tvdb_id) if top.tvdb_id else None,
            ),
        )

    def _lookup_book(self, signals: Signals) -> Optional[ProviderMatch]:
        # Bias the OpenLibrary search with the author when we have one.
        query = signals.title
        if signals.artist:
            query = f"{signals.title} {signals.artist}"
        results = self._ol.search(query, limit=5)
        if not results:
            return None
        top = results[0]

        external = ExternalIds(olid=top.work_id)
        # Search hits surface ISBNs as a flat list — pick the longest as ISBN-13
        # if it looks like one, the shortest as ISBN-10 otherwise.
        for raw_isbn in top.isbn or []:
            digits = raw_isbn.replace("-", "").replace(" ", "")
            if len(digits) == 13 and external.isbn_13 is None:
                external.isbn_13 = digits
            elif len(digits) == 10 and external.isbn_10 is None:
                external.isbn_10 = digits
            if external.isbn_10 and external.isbn_13:
                break

        relations: dict = {}
        if top.author_names:
            entries = []
            for name, key in zip(top.author_names,
                                 (top.author_keys + [None] * len(top.author_names))):
                ext = ExternalIds()
                if key:
                    ext.extra["openlibrary_author"] = key
                entries.append(ProviderEntity(
                    kind=EntityKind.AUTHOR, name=name, external_ids=ext,
                ))
            relations[EntityKind.AUTHOR] = entries

        language = (top.language[0] if top.language else None) or signals.language

        return ProviderMatch(
            provider=self.name,
            confidence=0.85,
            signals=Signals(
                title=top.title,
                year=top.first_publish_year,
                language=language,
                medium=Medium.BOOK,
            ),
            external_ids=external,
            relations=relations,
        )

    def _lookup_artist(self, signals: Signals) -> Optional[ProviderMatch]:
        results = self._client.search_artist(signals.artist)
        if not results:
            return None
        top = results[0]
        # Lidarr's metadata proxy returns artist info only — emit it as an
        # artist relation with the MBID, plus the work-level external id.
        relations: dict = {EntityKind.ARTIST: [ProviderEntity(
            kind=EntityKind.ARTIST,
            name=top.name,
            external_ids=ExternalIds(musicbrainz_artist=top.id),
        )]}
        return ProviderMatch(
            provider=self.name,
            confidence=0.75,
            signals=Signals(
                artist=top.name,
                medium=Medium.MUSIC,
            ),
            external_ids=ExternalIds(musicbrainz_artist=top.id),
            relations=relations,
        )


register(MetarrProvider())
