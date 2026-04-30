"""metarr-backed metadata providers.

`metarr` ships typed clients for the public catalogues we used to hand-roll
HTTP calls against. Each metarr client gets its own provider — same pattern
as the ``arr_*`` providers — so users can pick exactly which catalogues to
consult via ``--providers metarr_radarr,metarr_openlibrary,...``:

| Provider                | Backed by                       | Media |
| ----------------------- | ------------------------------- | --- |
| ``metarr_skyhook``      | ``ArrMetadataClient.search_series`` (skyhook.sonarr.tv) | TV |
| ``metarr_radarr``       | ``ArrMetadataClient.search_movie`` (radarrapi.servarr.com) | movie |
| ``metarr_lidarr``       | ``ArrMetadataClient.search_artist`` (api.lidarr.audio) | music |
| ``metarr_openlibrary``  | ``OpenLibraryClient.search`` (openlibrary.org) | book |
| ``metarr_bookinfo``     | ``BookInfoClient.search`` (Goodreads / Hardcover proxy) | book |

None of these need configuration — no env vars, no API keys, no
self-hosted instances.
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

try:
    from metarr import (  # noqa: WPS433
        ArrMetadataClient,
        BookInfoClient,
        OpenLibraryClient,
    )
    _METARR_AVAILABLE = True
except ImportError:
    ArrMetadataClient = None  # type: ignore[assignment]
    BookInfoClient = None  # type: ignore[assignment]
    OpenLibraryClient = None  # type: ignore[assignment]
    _METARR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Shared client cache — one instance per process per metarr client class.
# ---------------------------------------------------------------------------

_clients: dict = {}


def _client(cls):
    if cls is None:
        return None
    instance = _clients.get(cls)
    if instance is None:
        instance = cls()
        _clients[cls] = instance
    return instance


def _safe(call, *args, **kwargs):
    """Run a metarr call, swallow request errors, return falsy on failure."""
    try:
        return call(*args, **kwargs)
    except Exception as exc:
        LOG.warning("%s failed: %s", getattr(call, "__qualname__", call), exc)
        return None


# ---------------------------------------------------------------------------
# Servarr proxies
# ---------------------------------------------------------------------------

class _MetarrArrBase(MetadataProvider):
    """Shared scaffolding for Servarr-proxy providers (sonarr/radarr/lidarr)."""

    def is_available(self) -> bool:
        return _METARR_AVAILABLE


class MetarrSkyhookProvider(_MetarrArrBase):
    name = "metarr_skyhook"
    media = {Medium.TV}

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self.is_available() and signals.title):
            return None
        if signals.medium and signals.medium != Medium.TV:
            return None
        results = _safe(_client(ArrMetadataClient).search_series, signals.title) or []
        if not results:
            return None
        top = results[0]
        return ProviderMatch(
            provider=self.name,
            confidence=0.85,
            signals=Signals(title=top.title, year=top.year, medium=Medium.TV),
            external_ids=ExternalIds(tvdb=int(top.tvdb_id) if top.tvdb_id else None),
        )


class MetarrRadarrProvider(_MetarrArrBase):
    name = "metarr_radarr"
    media = {Medium.MOVIE}

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self.is_available() and signals.title):
            return None
        if signals.medium and signals.medium != Medium.MOVIE:
            return None
        results = _safe(_client(ArrMetadataClient).search_movie, signals.title) or []
        if not results:
            return None
        top = results[0]
        return ProviderMatch(
            provider=self.name,
            confidence=0.85,
            signals=Signals(title=top.title, year=top.year, medium=Medium.MOVIE),
            external_ids=ExternalIds(
                tmdb_movie=int(top.tmdb_id) if top.tmdb_id else None,
            ),
        )


class MetarrLidarrProvider(_MetarrArrBase):
    name = "metarr_lidarr"
    media = {Medium.MUSIC}

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self.is_available() and signals.artist):
            return None
        if signals.medium and signals.medium != Medium.MUSIC:
            return None
        results = _safe(_client(ArrMetadataClient).search_artist, signals.artist) or []
        if not results:
            return None
        top = results[0]
        relations = {EntityKind.ARTIST: [ProviderEntity(
            kind=EntityKind.ARTIST,
            name=top.name,
            external_ids=ExternalIds(musicbrainz_artist=top.id),
        )]}
        return ProviderMatch(
            provider=self.name,
            confidence=0.75,
            signals=Signals(artist=top.name, medium=Medium.MUSIC),
            external_ids=ExternalIds(musicbrainz_artist=top.id),
            relations=relations,
        )


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

class MetarrOpenLibraryProvider(MetadataProvider):
    name = "metarr_openlibrary"
    media = {Medium.BOOK}

    def is_available(self) -> bool:
        return _METARR_AVAILABLE

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self.is_available() and signals.title):
            return None
        if signals.medium and signals.medium != Medium.BOOK:
            return None
        query = signals.title
        if signals.artist:
            query = f"{signals.title} {signals.artist}"
        results = _safe(_client(OpenLibraryClient).search, query, limit=5) or []
        if not results:
            return None
        top = results[0]

        external = ExternalIds(olid=top.work_id)
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
                                 top.author_keys + [None] * len(top.author_names)):
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


class MetarrBookInfoProvider(MetadataProvider):
    """Goodreads-shaped book metadata via the Servarr ``rreading-glasses`` proxy."""

    name = "metarr_bookinfo"
    media = {Medium.BOOK}

    def is_available(self) -> bool:
        return _METARR_AVAILABLE

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (self.is_available() and signals.title):
            return None
        if signals.medium and signals.medium != Medium.BOOK:
            return None
        query = signals.title
        if signals.artist:
            query = f"{signals.title} {signals.artist}"
        client = BookInfoClient.goodreads()
        results = _safe(client.search, query) or []
        if not results:
            return None
        top = results[0]

        # Pull the work payload to recover title + ISBN + release year.
        work = _safe(client.get_work, top.work_id)
        title = signals.title
        year: Optional[int] = None
        isbn_13: Optional[str] = None
        author_name: Optional[str] = None
        if work:
            title = work.title or title
            release = work.release_date or work.release_date_raw
            if release and len(release) >= 4 and release[:4].isdigit():
                year = int(release[:4])
            for book in work.books:
                if isbn_13 is None and book.isbn13:
                    isbn_13 = book.isbn13
                    break

        relations: dict = {}
        if top.author_id:
            author = _safe(client.get_author, top.author_id)
            if author and author.name:
                author_name = author.name
                relations[EntityKind.AUTHOR] = [ProviderEntity(
                    kind=EntityKind.AUTHOR,
                    name=author.name,
                    external_ids=ExternalIds(extra={
                        "goodreads_author": str(top.author_id),
                    }),
                )]

        external = ExternalIds(
            goodreads=str(top.work_id),
            isbn_13=isbn_13,
            extra={"goodreads_book": str(top.book_id)} if top.book_id else {},
        )
        return ProviderMatch(
            provider=self.name,
            confidence=0.8,
            signals=Signals(
                title=title,
                artist=author_name,
                year=year,
                medium=Medium.BOOK,
            ),
            external_ids=external,
            relations=relations,
        )


# ---------------------------------------------------------------------------
# Registration — each metarr-backed provider self-registers; if metarr isn't
# importable, the providers stay in the registry but report
# ``is_available()`` = False, so ``media-archivist providers`` shows the gap.
# ---------------------------------------------------------------------------

register(MetarrSkyhookProvider())
register(MetarrRadarrProvider())
register(MetarrLidarrProvider())
register(MetarrOpenLibraryProvider())
register(MetarrBookInfoProvider())
