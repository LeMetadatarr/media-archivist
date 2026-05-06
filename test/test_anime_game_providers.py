"""Offline tests for AniListProvider, JikanAnimeProvider, JikanMangaProvider,
GoogleBooksProvider — all captured fixtures, no live HTTP calls."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mediavocab import MediaType
from metadatarr.resolve.entities import EntityKind
from mediavocab.models.signals import Signals
from media_archivist.providers import all_providers
from metadatarr.resolve.providers.anilist import AniListProvider
from metadatarr.resolve.providers.google_books import GoogleBooksProvider
from metadatarr.resolve.providers.jikan import JikanAnimeProvider, JikanMangaProvider

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Transport helpers (same pattern as test_new_providers.py)
# ---------------------------------------------------------------------------

class _FixtureTransport:
    def __init__(self, fixture_path: Path):
        self._body = fixture_path.read_bytes()

    def handle_request(self, request):
        import httpx
        return httpx.Response(200, content=self._body,
                              headers={"content-type": "application/json"},
                              request=request)


class _EmptyAnimeTransport:
    def handle_request(self, request):
        import httpx
        return httpx.Response(200,
                              content=b'{"data":{"Media":null}}',
                              headers={"content-type": "application/json"},
                              request=request)


class _EmptyListTransport:
    def handle_request(self, request):
        import httpx
        return httpx.Response(200,
                              content=b'{"data":[]}',
                              headers={"content-type": "application/json"},
                              request=request)


class _EmptyBooksTransport:
    def handle_request(self, request):
        import httpx
        return httpx.Response(200,
                              content=b'{"totalItems":0}',
                              headers={"content-type": "application/json"},
                              request=request)


def _make_get(transport):
    import httpx

    def _fake_get(url, *, params=None, headers=None, timeout=None, follow_redirects=False):
        return transport.handle_request(httpx.Request("GET", url))

    return _fake_get


def _make_post(transport):
    import httpx

    def _fake_post(url, *, json=None, timeout=None):
        return transport.handle_request(httpx.Request("POST", url))

    return _fake_post


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_all_new_providers_registered():
    keys = set(all_providers())
    assert {"anilist", "jikan_anime", "jikan_manga", "google_books"}.issubset(keys)


def test_all_new_providers_available():
    p = all_providers()
    for name in ("anilist", "jikan_anime", "jikan_manga", "google_books"):
        assert p[name].is_available(), f"{name} should be available"


# ---------------------------------------------------------------------------
# AniList — Cowboy Bebop (anime)
# ---------------------------------------------------------------------------

@pytest.fixture()
def anilist_anime(monkeypatch):
    import metadatarr.resolve.providers.anilist as mod
    transport = _FixtureTransport(FIXTURES / "anilist_cowboy_bebop.json")
    monkeypatch.setattr(mod, "httpx", MagicMock(post=_make_post(transport)))
    return AniListProvider()


def test_anilist_anime_returns_match(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m is not None
    assert m.provider == "anilist"
    assert m.signals.medium == MediaType.EPISODIC_SERIES


def test_anilist_anime_id(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m.external_ids.anilist_id == 1


def test_anilist_anime_title(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert "Cowboy Bebop" in m.signals.title


def test_anilist_anime_year(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m.signals.year == 1998


def test_anilist_anime_director_relation(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    directors = m.relations.get(EntityKind.DIRECTOR)
    assert directors, "expected director relation"
    names = [e.name for e in directors]
    assert any("Watanabe" in n for n in names)


def test_anilist_anime_studio_relation(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    studios = m.relations.get(EntityKind.STUDIO)
    assert studios
    assert studios[0].name == "Sunrise"
    assert studios[0].external_ids.anilist_studio_id == 14


def test_anilist_anime_confidence(anilist_anime):
    m = anilist_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m.confidence == 0.90


def test_anilist_skips_non_anime_manga():
    p = AniListProvider()
    assert p.lookup(Signals(title="X", medium=MediaType.MOVIE)) is None
    assert p.lookup(Signals(title="X", medium=MediaType.BOOK)) is None


def test_anilist_no_title_returns_none():
    assert AniListProvider().lookup(Signals()) is None


def test_anilist_no_httpx(monkeypatch):
    import metadatarr.resolve.providers.anilist as mod
    monkeypatch.setattr(mod, "httpx", None)
    assert AniListProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


def test_anilist_network_error(monkeypatch):
    import metadatarr.resolve.providers.anilist as mod
    import httpx

    def _fail(*a, **kw):
        raise httpx.ConnectError("timeout")

    monkeypatch.setattr(mod, "httpx", MagicMock(post=_fail))
    assert AniListProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


def test_anilist_empty_response(monkeypatch):
    import metadatarr.resolve.providers.anilist as mod
    monkeypatch.setattr(mod, "httpx", MagicMock(post=_make_post(_EmptyAnimeTransport())))
    assert AniListProvider().lookup(Signals(title="ZZZNOTFOUND", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


# ---------------------------------------------------------------------------
# AniList — Berserk (manga)
# ---------------------------------------------------------------------------

@pytest.fixture()
def anilist_manga(monkeypatch):
    import metadatarr.resolve.providers.anilist as mod
    transport = _FixtureTransport(FIXTURES / "anilist_berserk_manga.json")
    monkeypatch.setattr(mod, "httpx", MagicMock(post=_make_post(transport)))
    return AniListProvider()


def test_anilist_manga_returns_match(anilist_manga):
    m = anilist_manga.lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
    assert m is not None
    assert m.signals.medium == MediaType.COMIC


def test_anilist_manga_id(anilist_manga):
    m = anilist_manga.lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
    assert m.external_ids.anilist_id is not None


# ---------------------------------------------------------------------------
# Jikan — Cowboy Bebop (anime)
# ---------------------------------------------------------------------------

@pytest.fixture()
def jikan_anime(monkeypatch):
    import metadatarr.resolve.providers.jikan as mod
    transport = _FixtureTransport(FIXTURES / "jikan_cowboy_bebop.json")
    monkeypatch.setattr(mod, "httpx", MagicMock(get=_make_get(transport)))
    return JikanAnimeProvider()


def test_jikan_anime_returns_match(jikan_anime):
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m is not None
    assert m.provider == "jikan_anime"
    assert m.signals.medium == MediaType.EPISODIC_SERIES


def test_jikan_anime_mal_id(jikan_anime):
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m.external_ids.mal_id == 1


def test_jikan_anime_year(jikan_anime):
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m.signals.year == 1998


def test_jikan_anime_title_english(jikan_anime):
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert "Cowboy Bebop" in m.signals.title


def test_jikan_anime_studio_relation(jikan_anime):
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    studios = m.relations.get(EntityKind.STUDIO)
    assert studios


def test_jikan_anime_japanese_title_in_extra(jikan_anime):
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert "title_japanese" in m.external_ids.extra


def test_jikan_anime_year_filter(jikan_anime):
    # Year 1998 matches fixture; 1999 is outside the ±1 window so no filter applies
    m = jikan_anime.lookup(Signals(title="Cowboy Bebop", year=1998, medium=MediaType.EPISODIC_SERIES, content_genres=["anime"]))
    assert m is not None


def test_jikan_anime_skips_manga():
    assert JikanAnimeProvider().lookup(Signals(title="X", medium=MediaType.COMIC, content_genres=["manga"])) is None


def test_jikan_anime_no_title_returns_none():
    assert JikanAnimeProvider().lookup(Signals()) is None


def test_jikan_anime_no_httpx(monkeypatch):
    import metadatarr.resolve.providers.jikan as mod
    monkeypatch.setattr(mod, "httpx", None)
    assert JikanAnimeProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


def test_jikan_anime_network_error(monkeypatch):
    import metadatarr.resolve.providers.jikan as mod
    import httpx

    def _fail(*a, **kw):
        raise httpx.ConnectError("timeout")

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fail))
    assert JikanAnimeProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


def test_jikan_anime_empty_response(monkeypatch):
    import metadatarr.resolve.providers.jikan as mod
    monkeypatch.setattr(mod, "httpx", MagicMock(get=_make_get(_EmptyListTransport())))
    assert JikanAnimeProvider().lookup(Signals(title="ZZZNOTFOUND", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


# ---------------------------------------------------------------------------
# Jikan — Berserk (manga)
# ---------------------------------------------------------------------------

@pytest.fixture()
def jikan_manga(monkeypatch):
    import metadatarr.resolve.providers.jikan as mod
    transport = _FixtureTransport(FIXTURES / "jikan_berserk_manga.json")
    monkeypatch.setattr(mod, "httpx", MagicMock(get=_make_get(transport)))
    return JikanMangaProvider()


def test_jikan_manga_returns_match(jikan_manga):
    m = jikan_manga.lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
    assert m is not None
    assert m.provider == "jikan_manga"
    assert m.signals.medium == MediaType.COMIC


def test_jikan_manga_mal_id(jikan_manga):
    m = jikan_manga.lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
    assert m.external_ids.mal_id is not None


def test_jikan_manga_author_relation(jikan_manga):
    m = jikan_manga.lookup(Signals(title="Berserk", medium=MediaType.COMIC, content_genres=["manga"]))
    authors = m.relations.get(EntityKind.AUTHOR)
    assert authors
    # Miura Kentarou — MAL stores "Miura, Kentarou", provider should flip it
    names = [e.name for e in authors]
    assert any("Miura" in n for n in names)


def test_jikan_manga_skips_anime():
    assert JikanMangaProvider().lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


def test_jikan_manga_no_title_returns_none():
    assert JikanMangaProvider().lookup(Signals()) is None


# ---------------------------------------------------------------------------
# Google Books — The Hobbit
# ---------------------------------------------------------------------------

@pytest.fixture()
def google_books(monkeypatch):
    import metadatarr.resolve.providers.google_books as mod
    transport = _FixtureTransport(FIXTURES / "google_books_hobbit.json")
    monkeypatch.setattr(mod, "httpx", MagicMock(get=_make_get(transport)))
    return GoogleBooksProvider()


def test_google_books_returns_match(google_books):
    m = google_books.lookup(Signals(title="The Hobbit", artist="Tolkien", medium=MediaType.BOOK))
    assert m is not None
    assert m.provider == "google_books"
    assert m.signals.medium == MediaType.BOOK


def test_google_books_volume_id(google_books):
    m = google_books.lookup(Signals(title="The Hobbit", medium=MediaType.BOOK))
    assert m.external_ids.google_books_id == "UGmrEAAAQBAJ"


def test_google_books_isbn(google_books):
    m = google_books.lookup(Signals(title="The Hobbit", medium=MediaType.BOOK))
    assert m.external_ids.isbn_13 == "9780261102217"
    assert m.external_ids.isbn_10 == "0261102214"


def test_google_books_year(google_books):
    m = google_books.lookup(Signals(title="The Hobbit", medium=MediaType.BOOK))
    assert m.signals.year == 1937


def test_google_books_language(google_books):
    m = google_books.lookup(Signals(title="The Hobbit", medium=MediaType.BOOK))
    assert m.signals.language == "en"


def test_google_books_author_relation(google_books):
    m = google_books.lookup(Signals(title="The Hobbit", medium=MediaType.BOOK))
    authors = m.relations.get(EntityKind.AUTHOR)
    assert authors
    assert any("Tolkien" in a.name for a in authors)


def test_google_books_also_matches_audiobook(monkeypatch):
    import metadatarr.resolve.providers.google_books as mod
    transport = _FixtureTransport(FIXTURES / "google_books_hobbit.json")
    monkeypatch.setattr(mod, "httpx", MagicMock(get=_make_get(transport)))
    m = GoogleBooksProvider().lookup(Signals(title="The Hobbit", medium=MediaType.AUDIOBOOK))
    assert m is not None
    assert m.signals.medium == MediaType.AUDIOBOOK


def test_google_books_skips_other_media():
    p = GoogleBooksProvider()
    assert p.lookup(Signals(title="X", medium=MediaType.MOVIE)) is None
    assert p.lookup(Signals(title="X", medium=MediaType.EPISODIC_SERIES, content_genres=["anime"])) is None


def test_google_books_no_title_returns_none():
    assert GoogleBooksProvider().lookup(Signals()) is None


def test_google_books_no_httpx(monkeypatch):
    import metadatarr.resolve.providers.google_books as mod
    monkeypatch.setattr(mod, "httpx", None)
    assert GoogleBooksProvider().lookup(Signals(title="X", medium=MediaType.BOOK)) is None


def test_google_books_network_error(monkeypatch):
    import metadatarr.resolve.providers.google_books as mod
    import httpx

    def _fail(*a, **kw):
        raise httpx.ConnectError("timeout")

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fail))
    assert GoogleBooksProvider().lookup(Signals(title="X", medium=MediaType.BOOK)) is None


def test_google_books_empty_response(monkeypatch):
    import metadatarr.resolve.providers.google_books as mod
    monkeypatch.setattr(mod, "httpx", MagicMock(get=_make_get(_EmptyBooksTransport())))
    assert GoogleBooksProvider().lookup(Signals(title="ZZZNOTFOUND", medium=MediaType.BOOK)) is None
