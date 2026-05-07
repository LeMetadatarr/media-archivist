"""Offline tests for LibriVoxProvider and ApplePodcastsProvider.

HTTP calls are intercepted via httpx transport stubs that replay the
captured fixtures in test/fixtures/.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from mediavocab import MediaType
from metadatarr.resolve.entities import EntityRole
from mediavocab.models.signals import Signals
from media_archivist.providers import all_providers
from metadatarr.resolve.providers.librivox import LibriVoxProvider
from metadatarr.resolve.providers.podcast_index import ApplePodcastsProvider

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# httpx transport stub
# ---------------------------------------------------------------------------

class _FixtureTransport:
    """Replay a pre-captured JSON fixture for any request."""

    def __init__(self, fixture_path: Path, status_code: int = 200):
        self._body = fixture_path.read_bytes()
        self._status_code = status_code

    def handle_request(self, request):
        import httpx
        return httpx.Response(
            self._status_code,
            content=self._body,
            headers={"content-type": "application/json"},
            request=request,
        )


class _EmptyTransport:
    """Returns an empty result payload."""

    def __init__(self, key: str):
        self._key = key  # top-level key that holds the list

    def handle_request(self, request):
        import httpx
        return httpx.Response(
            200,
            content=json.dumps({self._key: []}).encode(),
            headers={"content-type": "application/json"},
            request=request,
        )


def _patch_httpx(provider_module, transport):
    """Monkey-patch httpx.get inside the given provider module to use *transport*."""
    import httpx

    def _fake_get(url, *, params=None, headers=None, timeout=None, follow_redirects=False):
        req = httpx.Request("GET", url)
        return transport.handle_request(req)

    provider_module.httpx = MagicMock()
    provider_module.httpx.get = _fake_get
    provider_module.httpx.get.__name__ = "get"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_librivox_registered():
    assert "librivox" in all_providers()


def test_apple_podcasts_registered():
    assert "apple_podcasts" in all_providers()


def test_librivox_always_available():
    assert LibriVoxProvider().is_available() is True


def test_apple_podcasts_always_available():
    assert ApplePodcastsProvider().is_available() is True


# ---------------------------------------------------------------------------
# LibriVox — Dracula fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def librivox_fixture(monkeypatch):
    """Patch httpx inside the librivox provider module to use the Dracula fixture."""
    import metadatarr.resolve.providers.librivox as mod
    import httpx

    fixture = FIXTURES / "librivox_dracula.json"

    def _fake_get(url, *, params=None, headers=None, timeout=None, follow_redirects=False):
        req = httpx.Request("GET", url)
        return _FixtureTransport(fixture).handle_request(req)

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fake_get))
    return LibriVoxProvider()


def test_librivox_returns_match(librivox_fixture):
    m = librivox_fixture.lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK))
    assert m is not None
    assert m.provider == "librivox"
    assert m.signals.medium == MediaType.AUDIOBOOK
    assert m.signals.title == "Dracula"


def test_librivox_external_id(librivox_fixture):
    m = librivox_fixture.lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK))
    assert m.external_ids.librivox_id == 271


def test_librivox_year_from_copyright(librivox_fixture):
    m = librivox_fixture.lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK))
    assert m.signals.year == 1897


def test_librivox_language(librivox_fixture):
    m = librivox_fixture.lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK))
    assert m.signals.language == "English"


def test_librivox_author_relation(librivox_fixture):
    m = librivox_fixture.lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK))
    authors = m.relations.get(EntityRole.AUTHOR)
    assert authors and len(authors) == 1
    assert authors[0].name == "Bram Stoker"
    assert authors[0].external_ids.extra.get("librivox_author_id") == "138"


def test_librivox_skips_non_audiobook():
    p = LibriVoxProvider()
    assert p.lookup(Signals(title="Dracula", medium=MediaType.MOVIE)) is None


def test_librivox_no_title_returns_none():
    assert LibriVoxProvider().lookup(Signals()) is None


def test_librivox_empty_response(monkeypatch):
    import metadatarr.resolve.providers.librivox as mod
    import httpx

    def _fake_get(url, *, params=None, headers=None, timeout=None, follow_redirects=False):
        req = httpx.Request("GET", url)
        return _EmptyTransport("books").handle_request(req)

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fake_get))
    assert LibriVoxProvider().lookup(Signals(title="ZZZNOTFOUND", medium=MediaType.AUDIOBOOK)) is None


def test_librivox_network_error_returns_none(monkeypatch):
    import metadatarr.resolve.providers.librivox as mod
    import httpx

    def _fake_get(*a, **kw):
        raise httpx.ConnectError("timeout")

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fake_get))
    assert LibriVoxProvider().lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK)) is None


def test_librivox_no_httpx(monkeypatch):
    import metadatarr.resolve.providers.librivox as mod
    monkeypatch.setattr(mod, "httpx", None)
    result = LibriVoxProvider().lookup(Signals(title="Dracula", medium=MediaType.AUDIOBOOK))
    assert result is None


# ---------------------------------------------------------------------------
# ApplePodcastsProvider — Serial fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def apple_fixture(monkeypatch):
    import metadatarr.resolve.providers.podcast_index as mod
    import httpx

    fixture = FIXTURES / "apple_podcasts_serial.json"

    def _fake_get(url, *, params=None, headers=None, timeout=None, follow_redirects=False):
        req = httpx.Request("GET", url)
        return _FixtureTransport(fixture).handle_request(req)

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fake_get))
    return ApplePodcastsProvider()


def test_apple_podcasts_returns_match(apple_fixture):
    m = apple_fixture.lookup(Signals(title="Serial", medium=MediaType.PODCAST))
    assert m is not None
    assert m.provider == "apple_podcasts"
    assert m.signals.medium == MediaType.PODCAST
    assert "Serial" in m.signals.title


def test_apple_podcasts_external_id(apple_fixture):
    m = apple_fixture.lookup(Signals(title="Serial", medium=MediaType.PODCAST))
    assert m.external_ids.apple_podcast_id == 917918570


def test_apple_podcasts_host_relation(apple_fixture):
    m = apple_fixture.lookup(Signals(title="Serial", medium=MediaType.PODCAST))
    hosts = m.relations.get(EntityRole.HOST)
    assert hosts and hosts[0].name == "Serial Productions & The New York Times"


def test_apple_podcasts_audiodrama_uses_voice_actor(apple_fixture):
    m = apple_fixture.lookup(Signals(title="Serial", medium=MediaType.AUDIO_DRAMA))
    assert m is not None
    assert m.signals.medium == MediaType.AUDIO_DRAMA
    voice_actors = m.relations.get(EntityRole.VOICE_ACTOR)
    assert voice_actors and voice_actors[0].name == "Serial Productions & The New York Times"


def test_apple_podcasts_skips_wrong_medium():
    p = ApplePodcastsProvider()
    assert p.lookup(Signals(title="X", medium=MediaType.MOVIE)) is None
    assert p.lookup(Signals(title="X", medium=MediaType.BOOK)) is None
    assert p.lookup(Signals(title="X", medium=MediaType.MUSIC)) is None


def test_apple_podcasts_no_title_returns_none():
    assert ApplePodcastsProvider().lookup(Signals()) is None


def test_apple_podcasts_empty_response(monkeypatch):
    import metadatarr.resolve.providers.podcast_index as mod
    import httpx

    def _fake_get(url, *, params=None, headers=None, timeout=None, follow_redirects=False):
        req = httpx.Request("GET", url)
        return _EmptyTransport("results").handle_request(req)

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fake_get))
    assert ApplePodcastsProvider().lookup(Signals(title="ZZZNOTFOUND", medium=MediaType.PODCAST)) is None


def test_apple_podcasts_network_error_returns_none(monkeypatch):
    import metadatarr.resolve.providers.podcast_index as mod
    import httpx

    def _fake_get(*a, **kw):
        raise httpx.ConnectError("timeout")

    monkeypatch.setattr(mod, "httpx", MagicMock(get=_fake_get))
    assert ApplePodcastsProvider().lookup(Signals(title="Serial", medium=MediaType.PODCAST)) is None


def test_apple_podcasts_confidence(apple_fixture):
    m = apple_fixture.lookup(Signals(title="Serial", medium=MediaType.PODCAST))
    assert m.confidence == 0.75
