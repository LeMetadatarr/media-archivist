"""Disambiguation, quarantine and canonical-id flow — fully offline.

The orchestrator is exercised against a stub provider so no network is
needed. Real-provider HTTP behaviour is left to integration tests under
test/test_providers/ (TODO).
"""
from __future__ import annotations

import json
from typing import Optional

import pytest

from mediavocab import MediaType
from media_archivist.canonicalize import (
    canonicalize,
    load_canonical,
    load_quarantine,
    quarantine_reject,
    quarantine_resolve,
)
from media_archivist.models.canonical import stable_id
from mediavocab.models import ExternalIds
from media_archivist.models.raw import Source
from mediavocab.models.signals import Signals, compare_signals as compare, signal_hash
from mediavocab import VariantKind
from metadatarr.resolve.entities import EntityRole, ProviderEntity
from metadatarr.resolve.base import (
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
    media = {MediaType.MOVIE, MediaType.MUSIC}

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
                        medium=MediaType.MUSIC),
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
                        medium=MediaType.MUSIC),
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
        signals=Signals(title="Hello", artist="Foo", runtime=600, medium=MediaType.MUSIC),
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
        signals=Signals(title="Hello", artist="Foo", runtime=600, medium=MediaType.MUSIC),
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
        signals=Signals(title="Hello", artist="Foo", runtime=240, medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1", imdb=None),
    )))
    canonicalize(str(db_path), providers=["stub"])

    [entry] = list(Index(str(db_path)).view())
    assert entry.canonical_id is not None
    assert entry.canonical_status == "matched"
    assert entry.external_ids.musicbrainz_recording == "mb-1"


def test_canonicalize_runs_providers_concurrently(tmp_path, stub_registered):
    """Wall time for N slow providers should be ~1x sleep, not N×sleep."""
    import time

    class _SlowProvider(_StubProvider):
        name = "slow"
        def __init__(self, response, delay=0.08):
            super().__init__(response)
            self._delay = delay
        def lookup(self, signals):
            time.sleep(self._delay)
            return self.response

    class _SlowProvider2(_SlowProvider):
        name = "slow2"

    match = ProviderMatch(
        provider="slow", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240, medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
    )

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    p1 = _SlowProvider(match)
    p2 = _SlowProvider2(ProviderMatch(
        provider="slow2", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240, medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
    ))
    register(p1)
    register(p2)

    t0 = time.perf_counter()
    canonicalize(str(db_path), providers=["slow", "slow2"])
    elapsed = time.perf_counter() - t0

    # Sequential would be ~0.16 s; concurrent should be well under 0.14 s.
    assert elapsed < 0.14, f"canonicalize took {elapsed:.3f}s — expected concurrent <0.14s"


def test_canonicalize_fans_out_list_variants(tmp_path, stub_registered):
    """When signals.include_variants=True, list_variants() results become RELEASE entities."""
    from media_archivist.canonicalize import load_entities

    class _VariantProvider(_StubProvider):
        name = "variant_stub"

        def list_variants(self, external_ids, signals=None):
            return [
                ProviderEntity(
                    role=EntityRole.RELEASE,
                    name="Director's Cut",
                    external_ids=ExternalIds(fanedit_id=42),
                )
            ]

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    match = ProviderMatch(
        provider="variant_stub", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240,
                        medium=MediaType.MUSIC, include_variants=True),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
    )
    p = _VariantProvider(match)
    register(p)

    canonicalize(str(db_path), providers=["variant_stub"])

    entities = load_entities(str(db_path))
    roles = {e.role for e in entities.entities.values()}
    assert EntityRole.RELEASE in roles, "RELEASE entity should have been upserted"


def test_id_stability_across_reruns(tmp_path, stub_registered):
    """Re-running canonicalize on unchanged data must not perturb canonical_id."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", year=1999, runtime=240,
                        medium=MediaType.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    cid1 = EnvelopeJsonStorage(str(db_path))["a"]["_meta"]["canonical_id"]

    canonicalize(str(db_path), providers=["stub"])
    cid2 = EnvelopeJsonStorage(str(db_path))["a"]["_meta"]["canonical_id"]
    assert cid1 == cid2


# ---------------------------------------------------------------------------
# Improvement #1 — provider log deduplication across re-runs
# ---------------------------------------------------------------------------

def test_provider_log_does_not_grow_on_rerun(tmp_path, stub_registered):
    """Re-running canonicalize must not duplicate provider_log entries."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240, medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
    )))
    canonicalize(str(db_path), providers=["stub"])
    canonicalize(str(db_path), providers=["stub"])

    canonical = load_canonical(str(db_path))
    rec = next(iter(canonical.records.values()))
    # One entry per provider, not two.
    providers_logged = [h.provider for h in rec.provider_log]
    assert providers_logged.count("stub") == 1


# ---------------------------------------------------------------------------
# Improvement #2 — external ID conflict detection in _consolidate()
# ---------------------------------------------------------------------------

def test_external_id_conflict_quarantines_row(tmp_path, stub_registered):
    """Two providers giving different tmdb_movie IDs should quarantine the row."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "youtube", "url": "a", "videoId": "vid1",
               "title": "Tenet", "duration": 9000}
    db.store()

    class ProviderA(_StubProvider):
        name = "provider_a"

    class ProviderB(_StubProvider):
        name = "provider_b"

    agree_signals = Signals(title="Tenet", runtime=9000, medium=MediaType.MOVIE)
    register(ProviderA(ProviderMatch(
        provider="provider_a", confidence=0.9,
        signals=agree_signals,
        external_ids=ExternalIds(tmdb_movie=100),
    )))
    register(ProviderB(ProviderMatch(
        provider="provider_b", confidence=0.9,
        signals=agree_signals,
        external_ids=ExternalIds(tmdb_movie=999),  # conflicts!
    )))

    canonical, quarantine, _ = canonicalize(
        str(db_path), providers=["provider_a", "provider_b"])
    assert len(quarantine.entries) == 1
    assert len(canonical.records) == 0


# ---------------------------------------------------------------------------
# Improvement #4 — medium inference for YouTube rows with music metadata
# ---------------------------------------------------------------------------

def test_signals_from_entry_infers_music_for_youtube_with_album():
    from media_archivist.canonicalize import signals_from_entry
    from media_archivist.models.canonical import MediaEntry
    from media_archivist.models.raw import Source

    entry = MediaEntry.build(
        source=Source.YOUTUBE,
        url="https://youtube.com/watch?v=abc",
        title="Some Track",
        album="Some Album",
        artist="Some Artist",
        duration=240.0,
        raw={},
    )
    s = signals_from_entry(entry)
    assert s.medium == MediaType.MUSIC


def test_signals_from_entry_keeps_other_for_plain_youtube():
    from media_archivist.canonicalize import signals_from_entry
    from media_archivist.models.canonical import MediaEntry
    from media_archivist.models.raw import Source

    entry = MediaEntry.build(
        source=Source.YOUTUBE,
        url="https://youtube.com/watch?v=abc",
        title="Random Vlog",
        raw={},
    )
    s = signals_from_entry(entry)
    assert s.medium == MediaType.GENERIC


# ---------------------------------------------------------------------------
# Improvement #5 — _build_row_id_index is O(1) lookup after single scan
# ---------------------------------------------------------------------------

def test_quarantine_resolve_stamps_row_correctly(tmp_path, stub_registered):
    """quarantine_resolve must still stamp the correct row URL (regression guard)."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=600,  # conflict
                        medium=MediaType.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    row_id = next(iter(load_quarantine(str(db_path)).entries))
    assert quarantine_resolve(str(db_path), row_id) is True
    db2 = EnvelopeJsonStorage(str(db_path))
    assert db2["a"]["_meta"]["canonical_status"] == "matched"


def test_quarantine_reject_stamps_row_correctly(tmp_path, stub_registered):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=600,
                        medium=MediaType.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    row_id = next(iter(load_quarantine(str(db_path)).entries))
    assert quarantine_reject(str(db_path), row_id) is True
    db2 = EnvelopeJsonStorage(str(db_path))
    assert db2["a"]["_meta"]["canonical_status"] == "matched"


# ---------------------------------------------------------------------------
# Improvement #7 — provider log scoped per record (no stale log from prior ID)
# ---------------------------------------------------------------------------

def test_provider_log_cleared_when_canonical_id_changes(tmp_path, stub_registered):
    """After quarantine_resolve assigns a new canonical_id, the old record's
    log must not pollute the new one."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240)
    db.store()

    # First: provider disagrees → quarantine
    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=600, medium=MediaType.MUSIC),
    )))
    canonicalize(str(db_path), providers=["stub"])
    row_id = next(iter(load_quarantine(str(db_path)).entries))

    # Resolve manually — creates a fresh CanonicalRecord
    quarantine_resolve(str(db_path), row_id)
    canonical = load_canonical(str(db_path))
    assert len(canonical.records) == 1
    rec = next(iter(canonical.records.values()))
    # The record was hand-resolved, not from a provider hit — log must be empty.
    assert rec.provider_log == []


# ---------------------------------------------------------------------------
# MediaType routing — _providers_for and signals_from_entry _meta.medium
# ---------------------------------------------------------------------------

def test_providers_for_filters_by_medium():
    from media_archivist.canonicalize import _providers_for
    # MediaType moved to mediavocab
    from metadatarr.resolve.providers.anilist import AniListProvider
    from metadatarr.resolve.providers.jikan import JikanAnimeProvider

    all_p = [AniListProvider(), JikanAnimeProvider(), _StubProvider(ProviderMatch(
        provider="stub_movie", confidence=0.9, signals=Signals(title="X"),
    ))]
    # Override stub media to MOVIE for clarity
    all_p[-1].__class__.media = {MediaType.MOVIE}

    anime_only = _providers_for(all_p, MediaType.EPISODIC_SERIES,
                                  content_genres=["anime"])
    names = {p.name for p in anime_only}
    assert "anilist" in names
    assert "jikan_anime" in names
    assert "stub_movie" not in names


def test_providers_for_returns_all_for_other():
    from media_archivist.canonicalize import _providers_for
    # MediaType moved to mediavocab
    from metadatarr.resolve.providers.anilist import AniListProvider

    all_p = [AniListProvider()]
    # MediaType.GENERIC → no filtering, return all
    assert _providers_for(all_p, MediaType.GENERIC) == all_p
    assert _providers_for(all_p, None) == all_p


def test_providers_for_includes_universal_providers():
    """Providers with empty media set are always included."""
    from media_archivist.canonicalize import _providers_for
    # MediaType moved to mediavocab

    class _Universal(MetadataProvider):
        name = "universal"
        media = set()  # no restriction
        def is_available(self): return True
        def lookup(self, signals): return None

    p = _Universal()
    assert p in _providers_for([p], MediaType.EPISODIC_SERIES)
    assert p in _providers_for([p], MediaType.GAME)


def test_signals_from_entry_reads_meta_medium():
    from media_archivist.canonicalize import signals_from_entry
    from media_archivist.models.canonical import MediaEntry
    from media_archivist.models.raw import Source
    # MediaType moved to mediavocab

    entry = MediaEntry.build(
        source=Source.YOUTUBE,
        url="https://www.youtube.com/watch?v=abc",
        title="Spirited Away",
        raw={"_meta": {"medium": "anime"}},
    )
    s = signals_from_entry(entry)
    assert s.medium == MediaType.EPISODIC_SERIES


def test_signals_from_entry_reads_meta_enriched_content_type():
    from media_archivist.canonicalize import signals_from_entry
    from media_archivist.models.canonical import MediaEntry
    from media_archivist.models.raw import Source
    # MediaType moved to mediavocab

    entry = MediaEntry.build(
        source=Source.INTERNET_ARCHIVE,
        url="https://archive.org/details/xyz",
        title="Some Audiobook",
        raw={"_meta": {"enriched": {"content_type": {"label": "audiobook"}}}},
    )
    s = signals_from_entry(entry)
    assert s.medium == MediaType.AUDIOBOOK


def test_signals_from_entry_unknown_meta_medium_falls_back():
    from media_archivist.canonicalize import signals_from_entry
    from media_archivist.models.canonical import MediaEntry
    from media_archivist.models.raw import Source
    # MediaType moved to mediavocab

    entry = MediaEntry.build(
        source=Source.YOUTUBE,
        url="https://www.youtube.com/watch?v=abc",
        title="Unknown Content",
        raw={"_meta": {"medium": "bogus_type"}},
    )
    s = signals_from_entry(entry)
    # Unknown label → stays as source default (OTHER for YouTube)
    assert s.medium == MediaType.GENERIC


def test_music_row_without_artist_is_skipped(tmp_path):
    """Music row with no artist signal should be stamped unmatched, not crash."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "youtube_music", "url": "a", "videoId": "v1", "title": "Track"}
    db.store()
    canonical, quarantine, _ = canonicalize(str(db_path), providers=[])
    assert len(canonical.records) == 0
    assert len(quarantine.entries) == 0
