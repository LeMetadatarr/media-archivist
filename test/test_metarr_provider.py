"""Metarr provider — offline shape + dispatch checks."""
from __future__ import annotations

import pytest

pytest.importorskip("metarr")

from media_archivist.models.entities import EntityKind
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers import all_providers
from media_archivist.providers.metarr import MetarrProvider


def test_provider_registered():
    assert "metarr" in all_providers()


def test_provider_skips_when_no_title():
    p = MetarrProvider()
    assert p.lookup(Signals()) is None


def test_provider_skips_book_medium():
    """Books / podcasts aren't covered by the Servarr proxies."""
    p = MetarrProvider()
    p._client = _StubClient()
    sig = Signals(title="Some Book", medium=Medium.BOOK)
    assert p.lookup(sig) is None


def test_provider_dispatches_to_movie():
    p = MetarrProvider()
    p._client = _StubClient()
    match = p.lookup(Signals(title="Inception", medium=Medium.MOVIE))
    assert match is not None
    assert match.signals.year == 2010
    assert match.external_ids.tmdb_movie == 27205


def test_provider_dispatches_to_tv():
    p = MetarrProvider()
    p._client = _StubClient()
    match = p.lookup(Signals(title="The Boys", medium=Medium.TV))
    assert match is not None
    assert match.external_ids.tvdb == 355567


def test_provider_emits_artist_relation_with_mbid():
    p = MetarrProvider()
    p._client = _StubClient()
    match = p.lookup(Signals(title="Random song", artist="Daft Punk",
                             medium=Medium.MUSIC))
    assert match is not None
    assert match.external_ids.musicbrainz_artist == "mb-daft-punk"
    rel = match.relations.get(EntityKind.ARTIST)
    assert rel and rel[0].external_ids.musicbrainz_artist == "mb-daft-punk"


def test_provider_falls_through_when_medium_unspecified():
    """No medium given → tries movie first, falls through to tv on miss."""
    p = MetarrProvider()
    p._client = _StubClient(movie_results=[], tv_match=True)
    match = p.lookup(Signals(title="Whatever"))
    assert match is not None
    assert match.signals.medium == Medium.TV


# ---------------------------------------------------------------------------
# Stub client — mirrors metarr.ArrMetadataClient's surface
# ---------------------------------------------------------------------------

class _StubMovie:
    title = "Inception"; tmdb_id = 27205; year = 2010


class _StubSeries:
    title = "The Boys"; tvdb_id = 355567; year = 2019


class _StubArtist:
    id = "mb-daft-punk"; name = "Daft Punk"


class _StubClient:
    def __init__(self, *, movie_results=None, tv_match=False) -> None:
        self._movie_results = movie_results
        self._tv_match = tv_match

    def search_movie(self, term):  # noqa: D401
        if self._movie_results is not None:
            return self._movie_results
        return [_StubMovie()]

    def search_series(self, term):
        if self._tv_match:
            return [_StubSeries()]
        return [_StubSeries()]

    def search_artist(self, term):
        return [_StubArtist()]
