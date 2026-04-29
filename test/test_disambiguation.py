"""Disambiguation, quarantine and canonical-id flow — fully offline.

The orchestrator is exercised against a stub provider so no network is
needed. Real-provider HTTP behaviour is left to integration tests under
test/test_providers/ (TODO).
"""
from __future__ import annotations

import json
from typing import Optional

import pytest

from media_archivist.canonicalize import (
    canonicalize,
    load_canonical,
    load_quarantine,
    quarantine_reject,
    quarantine_resolve,
)
from media_archivist.models.canonical import stable_id
from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.raw import Source
from media_archivist.models.signals import Medium, Signals, compare, signal_hash
from media_archivist.providers.base import (
    MetadataProvider,
    ProviderMatch,
    _REGISTRY,
    register,
)
from media_archivist.storage import EnvelopeJsonStorage


# ---------------------------------------------------------------------------
# Stub provider
# ---------------------------------------------------------------------------

class _StubProvider(MetadataProvider):
    name = "stub"
    media = {Medium.MOVIE, Medium.MUSIC}

    def __init__(self, response: Optional[ProviderMatch] = None,
                 available: bool = True) -> None:
        self.response = response
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def lookup(self, signals):  # noqa: D401
        return self.response


@pytest.fixture
def stub_registered():
    """Replace the registry with just our stub for the duration of a test."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield _StubProvider
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _bandcamp(url: str, title: str, artist: str, dur: float) -> dict:
    return {"source": "bandcamp", "url": url, "title": title,
            "artist": artist, "duration": dur}


# ---------------------------------------------------------------------------
# Signals comparison
# ---------------------------------------------------------------------------

def test_signals_overlapping_agreement():
    a = Signals(title="Tenet", year=2020, country="US", runtime=9000)
    b = Signals(title="tenet", year=2020, country="US", runtime=9003)
    assert compare(a, b) == []


def test_signals_year_tolerance():
    a = Signals(year=2020)
    assert compare(a, Signals(year=2021)) == []
    assert len(compare(a, Signals(year=2018))) == 1


def test_signals_country_must_be_exact():
    a = Signals(country="US")
    assert len(compare(a, Signals(country="GB"))) == 1
    assert compare(a, Signals(country="us")) == []


def test_signal_hash_stable_across_normalization():
    a = Signals(title="Hello!  World", artist="Foo", year=1999, runtime=180)
    b = Signals(title="hello world",  artist="foo", year=1999, runtime=180)
    assert signal_hash(a) == signal_hash(b)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def test_canonicalize_matches_when_provider_agrees(tmp_path, stub_registered):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", year=1999, runtime=240,
                        medium=Medium.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
    )))
    canonical, quarantine, _ = canonicalize(str(db_path), providers=["stub"])
    assert len(canonical.records) == 1
    assert len(quarantine.entries) == 0
    rec = next(iter(canonical.records.values()))
    assert rec.external_ids.musicbrainz_recording == "mb-1"
    # Row got stamped with the canonical_id.
    db = EnvelopeJsonStorage(str(db_path))
    assert db["a"]["_meta"]["canonical_status"] == "matched"
    assert db["a"]["_meta"]["canonical_id"] == rec.canonical_id


def test_canonicalize_quarantines_on_signal_disagreement(tmp_path, stub_registered):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", runtime=600,  # 240 vs 600 → conflict
                        medium=Medium.MUSIC),
    )))
    canonical, quarantine, _ = canonicalize(str(db_path), providers=["stub"])
    assert len(canonical.records) == 0
    assert len(quarantine.entries) == 1
    qe = next(iter(quarantine.entries.values()))
    assert any(c.signal == "runtime" for c in qe.conflicts)
    db = EnvelopeJsonStorage(str(db_path))
    assert db["a"]["_meta"]["canonical_status"] == "quarantined"
    assert "canonical_id" not in db["a"]["_meta"]


def test_quarantine_resolve_lifts_status(tmp_path, stub_registered):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    # First a conflicting provider response → quarantine
    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", runtime=600, medium=Medium.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    quarantine = load_quarantine(str(db_path))
    row_id = next(iter(quarantine.entries.keys()))
    assert quarantine_resolve(str(db_path), row_id) is True

    canonical = load_canonical(str(db_path))
    assert any(row_id in r.members for r in canonical.records.values())
    db = EnvelopeJsonStorage(str(db_path))
    assert db["a"]["_meta"]["canonical_status"] == "matched"


def test_quarantine_reject_allocates_new_id(tmp_path, stub_registered):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()
    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", runtime=600, medium=Medium.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    row_id = next(iter(load_quarantine(str(db_path)).entries.keys()))

    # Reject — must allocate a NEW canonical_id distinct from any existing record.
    assert quarantine_reject(str(db_path), row_id) is True
    canonical = load_canonical(str(db_path))
    assert len(canonical.records) == 1
    rec = next(iter(canonical.records.values()))
    assert rec.members == [row_id]


def test_index_view_surfaces_canonical_ids(tmp_path, stub_registered):
    """After canonicalize(), Index.view() exposes canonical_id + external_ids."""
    from media_archivist.index import Index

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", runtime=240, medium=Medium.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1", imdb=None),
    )))
    canonicalize(str(db_path), providers=["stub"])

    [entry] = list(Index(str(db_path)).view())
    assert entry.canonical_id is not None
    assert entry.canonical_status == "matched"
    assert entry.external_ids.musicbrainz_recording == "mb-1"


def test_id_stability_across_reruns(tmp_path, stub_registered):
    """Re-running canonicalize on unchanged data must not perturb canonical_id."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", year=1999, runtime=240,
                        medium=Medium.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    cid1 = EnvelopeJsonStorage(str(db_path))["a"]["_meta"]["canonical_id"]

    canonicalize(str(db_path), providers=["stub"])
    cid2 = EnvelopeJsonStorage(str(db_path))["a"]["_meta"]["canonical_id"]
    assert cid1 == cid2
