"""metarr-backed providers — offline shape + dispatch checks."""
from __future__ import annotations

import pytest

pytest.importorskip("metarr")

from media_archivist.models.entities import EntityKind
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers import all_providers
from media_archivist.providers.metarr import (
    MetarrBookInfoProvider,
    MetarrLidarrProvider,
    MetarrOpenLibraryProvider,
    MetarrRadarrProvider,
    MetarrSkyhookProvider,
)


# ---------------------------------------------------------------------------
# Stub clients — mirror metarr's surface
# ---------------------------------------------------------------------------

class _StubMovie:
    title = "Inception"; tmdb_id = 27205; year = 2010


class _StubSeries:
    title = "The Boys"; tvdb_id = 355567; year = 2019


class _StubArtist:
    id = "mb-daft-punk"; name = "Daft Punk"


class _StubArrClient:
    def search_movie(self, term):
        return [_StubMovie()]
    def search_series(self, term):
        return [_StubSeries()]
    def search_artist(self, term):
        return [_StubArtist()]


class _StubBookHit:
    work_id = "OL27482W"
    title = "The Hobbit"
    first_publish_year = 1937
    author_names = ["J.R.R. Tolkien"]
    author_keys = ["OL26320A"]
    isbn = ["9780261103344", "0261103342"]
    language = ["eng"]


class _StubOLClient:
    def search(self, query, limit=10):
        return [_StubBookHit()]


class _StubBookInfoSearchHit:
    book_id = 5907
    work_id = 1540236
    author_id = 656983


class _StubBookInfoBook:
    isbn13 = "9783423085595"


class _StubBookInfoWork:
    title = "The Hobbit"
    release_date = "1937-09-21"
    release_date_raw = None
    books = [_StubBookInfoBook()]


class _StubBookInfoAuthor:
    name = "J.R.R. Tolkien"


class _StubBookInfoClient:
    @classmethod
    def goodreads(cls):
        return cls()
    def search(self, query):
        return [_StubBookInfoSearchHit()]
    def get_work(self, wid):
        return _StubBookInfoWork()
    def get_author(self, aid):
        return _StubBookInfoAuthor()


def _wire(provider, *, arr=False, ol=False, bookinfo=False):
    """Replace the cached metarr clients used by the provider under test."""
    from media_archivist.providers import metarr as mp
    mp._clients.clear()
    if arr:
        mp._clients[mp.ArrMetadataClient] = _StubArrClient()
    if ol:
        mp._clients[mp.OpenLibraryClient] = _StubOLClient()
    if bookinfo:
        mp.BookInfoClient = _StubBookInfoClient
    return provider


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_all_five_providers_registered():
    expected = {"metarr_skyhook", "metarr_radarr", "metarr_lidarr",
                "metarr_openlibrary", "metarr_bookinfo"}
    assert expected.issubset(set(all_providers()))


def test_old_umbrella_name_is_gone():
    assert "metarr" not in all_providers()


# ---------------------------------------------------------------------------
# Servarr proxies
# ---------------------------------------------------------------------------

def test_radarr_dispatches_movie():
    p = _wire(MetarrRadarrProvider(), arr=True)
    m = p.lookup(Signals(title="Inception", medium=Medium.MOVIE))
    assert m and m.external_ids.tmdb_movie == 27205
    assert m.signals.year == 2010


def test_radarr_skips_non_movie():
    p = _wire(MetarrRadarrProvider(), arr=True)
    assert p.lookup(Signals(title="X", medium=Medium.TV)) is None


def test_skyhook_dispatches_tv():
    p = _wire(MetarrSkyhookProvider(), arr=True)
    m = p.lookup(Signals(title="The Boys", medium=Medium.TV))
    assert m and m.external_ids.tvdb == 355567


def test_lidarr_emits_artist_relation():
    p = _wire(MetarrLidarrProvider(), arr=True)
    m = p.lookup(Signals(title="Anything", artist="Daft Punk",
                         medium=Medium.MUSIC))
    assert m and m.external_ids.musicbrainz_artist == "mb-daft-punk"
    rel = m.relations.get(EntityKind.ARTIST)
    assert rel and rel[0].external_ids.musicbrainz_artist == "mb-daft-punk"


def test_lidarr_requires_artist_signal():
    p = _wire(MetarrLidarrProvider(), arr=True)
    assert p.lookup(Signals(title="X", medium=Medium.MUSIC)) is None


# ---------------------------------------------------------------------------
# Books — OpenLibrary
# ---------------------------------------------------------------------------

def test_openlibrary_dispatches_book():
    p = _wire(MetarrOpenLibraryProvider(), ol=True)
    m = p.lookup(Signals(title="The Hobbit", artist="Tolkien",
                         medium=Medium.BOOK))
    assert m and m.external_ids.olid == "OL27482W"
    assert m.external_ids.isbn_13 == "9780261103344"
    rel = m.relations.get(EntityKind.AUTHOR)
    assert rel and rel[0].external_ids.extra.get("openlibrary_author") == "OL26320A"


def test_openlibrary_skips_non_book():
    p = _wire(MetarrOpenLibraryProvider(), ol=True)
    assert p.lookup(Signals(title="X", medium=Medium.MOVIE)) is None


# ---------------------------------------------------------------------------
# Books — Goodreads (BookInfo)
# ---------------------------------------------------------------------------

def test_bookinfo_returns_goodreads_ids():
    from media_archivist.providers import metarr as mp
    saved = mp.BookInfoClient
    mp.BookInfoClient = _StubBookInfoClient
    try:
        p = MetarrBookInfoProvider()
        m = p.lookup(Signals(title="The Hobbit", artist="Tolkien",
                             medium=Medium.BOOK))
    finally:
        mp.BookInfoClient = saved
    assert m and m.external_ids.goodreads == "1540236"
    assert m.external_ids.extra["goodreads_book"] == "5907"
    assert m.external_ids.isbn_13 == "9783423085595"
    assert m.signals.year == 1937
    rel = m.relations.get(EntityKind.AUTHOR)
    assert rel and rel[0].external_ids.extra["goodreads_author"] == "656983"


def test_bookinfo_skips_non_book():
    p = MetarrBookInfoProvider()
    assert p.lookup(Signals(title="X", medium=Medium.MOVIE)) is None


# ---------------------------------------------------------------------------
# No-title short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    MetarrRadarrProvider,
    MetarrSkyhookProvider,
    MetarrOpenLibraryProvider,
    MetarrBookInfoProvider,
])
def test_no_title_returns_none(cls):
    assert cls().lookup(Signals()) is None
