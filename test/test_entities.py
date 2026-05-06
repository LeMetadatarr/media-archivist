"""Entity layer — sidecar I/O, allocation, merge, --where dotted access."""
from __future__ import annotations

import json

import pytest

from mediavocab import MediaType
from media_archivist.canonicalize import canonicalize
from media_archivist.entities import (
    attach_work,
    load_entities,
    save_entities,
    upsert_entity,
)
from media_archivist.index import Index, WhereError
from media_archivist.models.canonical import MediaEntry
from metadatarr.resolve.entities import (
    EntityKind,
    EntityRecord,
    EntitySidecar,
    ProviderEntity,
    allocate_entity_id,
)
from mediavocab.models import ExternalIds
from mediavocab.models.signals import Signals
from metadatarr.resolve.base import (
    MetadataProvider,
    ProviderMatch,
    _REGISTRY,
    register,
)
from media_archivist.storage import EnvelopeJsonStorage


# ---------------------------------------------------------------------------
# allocate_entity_id
# ---------------------------------------------------------------------------

def test_allocate_id_prefers_external_id_over_name():
    a = allocate_entity_id(EntityKind.ARTIST, name="A",
                           external_ids=ExternalIds(musicbrainz_artist="mb-x"))
    b = allocate_entity_id(EntityKind.ARTIST, name="B",
                           external_ids=ExternalIds(musicbrainz_artist="mb-x"))
    assert a == b


def test_allocate_id_normalizes_name():
    a = allocate_entity_id(EntityKind.ARTIST, name="Aphex Twin")
    b = allocate_entity_id(EntityKind.ARTIST, name="aphex   twin!")
    assert a == b


def test_allocate_id_distinct_kinds():
    a = allocate_entity_id(EntityKind.ARTIST, name="Foo")
    b = allocate_entity_id(EntityKind.DIRECTOR, name="Foo")
    assert a != b


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------

def test_sidecar_round_trip(tmp_path):
    sidecar = EntitySidecar()
    sidecar.entities["e1"] = EntityRecord(
        id="e1", kind=EntityKind.ARTIST, name="Foo",
    )
    save_entities(str(tmp_path / "db.json"), sidecar)
    again = load_entities(str(tmp_path / "db.json"))
    assert "e1" in again.entities


def test_upsert_entity_merges_aliases_and_external_ids():
    sidecar = EntitySidecar()
    eid = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.ARTIST, name="Aphex Twin",
        external_ids=ExternalIds(musicbrainz_artist="mb-x"),
    ))
    eid2 = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.ARTIST, name="AFX",  # alias
        external_ids=ExternalIds(musicbrainz_artist="mb-x", wikidata="Q23874"),
    ))
    assert eid == eid2
    rec = sidecar.entities[eid]
    assert "AFX" in rec.aliases
    assert rec.external_ids.wikidata == "Q23874"


def test_attach_work_is_idempotent():
    sidecar = EntitySidecar()
    eid = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.ARTIST, name="Foo",
    ))
    attach_work(sidecar, eid, "w-1")
    attach_work(sidecar, eid, "w-1")
    attach_work(sidecar, eid, "w-2")
    assert sidecar.entities[eid].works == ["w-1", "w-2"]


# ---------------------------------------------------------------------------
# canonicalize → entity sidecar
# ---------------------------------------------------------------------------

class _StubProvider(MetadataProvider):
    name = "stub_entities"
    media = {MediaType.MUSIC, MediaType.MOVIE}

    def __init__(self, response: ProviderMatch) -> None:
        self.response = response

    def is_available(self) -> bool:
        return True

    def lookup(self, signals: Signals):  # noqa: D401
        return self.response


@pytest.fixture
def stub_registry():
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def test_canonicalize_populates_entity_sidecar(tmp_path, stub_registry):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "duration": 240}
    db.store()

    register(_StubProvider(ProviderMatch(
        provider="stub_entities", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", runtime=240,
                        medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
        relations={
            EntityKind.ARTIST: [ProviderEntity(
                kind=EntityKind.ARTIST, name="Foo",
                external_ids=ExternalIds(musicbrainz_artist="mb-artist-1"),
            )],
            EntityKind.PRODUCER: [ProviderEntity(
                kind=EntityKind.PRODUCER, name="Bar",
            )],
        },
    )))

    _, _, entities = canonicalize(str(db_path), providers=["stub_entities"])
    kinds = {r.kind.value for r in entities.entities.values()}
    assert kinds == {"artist", "producer"}
    foo = [r for r in entities.entities.values() if r.name == "Foo"][0]
    assert foo.external_ids.musicbrainz_artist == "mb-artist-1"
    assert len(foo.works) == 1


def test_canonicalize_dedupes_artists_across_providers(tmp_path, stub_registry):
    """Two providers naming the same artist on the same work converge to one entity."""
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "duration": 240}
    db.store()

    class FirstStub(_StubProvider):
        name = "stub_first"

    class SecondStub(_StubProvider):
        name = "stub_second"

    register(FirstStub(ProviderMatch(
        provider="stub_first", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240,
                        medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
        relations={EntityKind.ARTIST: [ProviderEntity(
            kind=EntityKind.ARTIST, name="Foo",
            external_ids=ExternalIds(musicbrainz_artist="mb-artist-1"),
        )]},
    )))
    register(SecondStub(ProviderMatch(
        provider="stub_second", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240,
                        medium=MediaType.MUSIC),
        external_ids=ExternalIds(wikidata="Q1"),
        relations={EntityKind.ARTIST: [ProviderEntity(
            kind=EntityKind.ARTIST, name="Foo (alias)",
            external_ids=ExternalIds(musicbrainz_artist="mb-artist-1",
                                     wikidata="Q1"),
        )]},
    )))

    _, _, entities = canonicalize(str(db_path),
                                  providers=["stub_first", "stub_second"])
    artists = [r for r in entities.entities.values()
               if r.kind == EntityKind.ARTIST]
    assert len(artists) == 1, "providers sharing mb-artist-1 should converge"
    rec = artists[0]
    assert rec.external_ids.wikidata == "Q1"
    assert "Foo (alias)" in rec.aliases


# ---------------------------------------------------------------------------
# Index.view + --where dotted access
# ---------------------------------------------------------------------------

def test_index_view_resolves_relation_names(tmp_path, stub_registry):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "duration": 240}
    db.store()
    register(_StubProvider(ProviderMatch(
        provider="stub_entities", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240,
                        medium=MediaType.MUSIC),
        relations={EntityKind.ARTIST: [ProviderEntity(
            kind=EntityKind.ARTIST, name="Foo",
        )]},
    )))
    canonicalize(str(db_path), providers=["stub_entities"])

    [entry] = list(Index(str(db_path)).view())
    assert entry.relations.get("artist") == ["Foo"]


def test_where_supports_dotted_relations(tmp_path, stub_registry):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "duration": 240}
    db["b"] = {"source": "bandcamp", "url": "b", "title": "Goodbye",
               "artist": "Bar", "duration": 180}
    db.store()

    class PerArtistStub(_StubProvider):
        def lookup(self, signals):
            return ProviderMatch(
                provider="stub_entities",
                confidence=0.9,
                signals=Signals(title=signals.title, artist=signals.artist,
                                runtime=signals.runtime, medium=MediaType.MUSIC),
                relations={EntityKind.ARTIST: [ProviderEntity(
                    kind=EntityKind.ARTIST, name=signals.artist,
                )]},
            )

    register(PerArtistStub(None))
    canonicalize(str(db_path), providers=["stub_entities"])

    idx = Index(str(db_path))
    out = list(idx.view(where='"Foo" in relations.artist'))
    assert len(out) == 1 and out[0].url == "a"


def test_where_dotted_access_rejects_string_methods(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "youtube", "url": "a", "videoId": "a", "title": "T"}
    db.store()
    idx = Index(str(db_path))
    with pytest.raises(WhereError):
        list(idx.view(where="title.upper() == 'T'"))


# ---------------------------------------------------------------------------
# merge_alias normalization (improvement #3)
# ---------------------------------------------------------------------------

def test_merge_alias_dedupes_case_variants():
    rec = EntityRecord(id="e1", kind=EntityKind.ARTIST, name="The Beatles")
    rec.merge_alias("the beatles")   # normalized == primary name → skip
    rec.merge_alias("The Beatles!")  # normalized == primary name → skip
    assert rec.aliases == []


def test_merge_alias_dedupes_across_existing_aliases():
    rec = EntityRecord(id="e1", kind=EntityKind.ARTIST, name="The Beatles")
    rec.merge_alias("Beatles, The")
    rec.merge_alias("beatles, the")  # same normalized form → skip
    assert len(rec.aliases) == 1


def test_merge_alias_keeps_genuinely_different_alias():
    rec = EntityRecord(id="e1", kind=EntityKind.ARTIST, name="Aphex Twin")
    rec.merge_alias("AFX")
    assert "AFX" in rec.aliases


# ---------------------------------------------------------------------------
# attach_work warning for missing entity (improvement #6)
# ---------------------------------------------------------------------------

def test_attach_work_logs_warning_for_missing_entity(caplog):
    import logging
    sidecar = EntitySidecar()
    with caplog.at_level(logging.WARNING, logger="media_archivist.entities"):
        attach_work(sidecar, "nonexistent-id", "canonical-123")
    assert "nonexistent-id" in caplog.text


# ---------------------------------------------------------------------------
# Cross-provider entity merging (name-based collapse)
# ---------------------------------------------------------------------------

def test_upsert_collapses_same_name_different_external_ids():
    """AniList and Jikan emit the same studio under different external IDs.
    The second upsert should merge into the first record, not create a second."""
    from media_archivist.entities import upsert_entity
    from metadatarr.resolve.entities import EntityKind, EntitySidecar, ProviderEntity
    from mediavocab.models import ExternalIds

    sidecar = EntitySidecar()

    anilist_sunrise = ProviderEntity(
        kind=EntityKind.STUDIO,
        name="Sunrise",
        external_ids=ExternalIds(anilist_studio_id=14),
    )
    jikan_sunrise = ProviderEntity(
        kind=EntityKind.STUDIO,
        name="Sunrise",
        external_ids=ExternalIds(mal_studio_id=42),
    )

    eid1 = upsert_entity(sidecar, anilist_sunrise)
    eid2 = upsert_entity(sidecar, jikan_sunrise)

    # Same entity id — collapsed to one record
    assert eid1 == eid2
    assert len(sidecar.entities) == 1

    rec = sidecar.entities[eid1]
    # Both IDs accumulated on the single record
    assert rec.external_ids.anilist_studio_id == 14
    assert rec.external_ids.mal_studio_id == 42


def test_upsert_collapses_case_normalised_names():
    """Name comparison is case-insensitive (same normalized form collapses)."""
    from media_archivist.entities import upsert_entity
    from metadatarr.resolve.entities import EntityKind, EntitySidecar, ProviderEntity
    from mediavocab.models import ExternalIds

    sidecar = EntitySidecar()
    # "Studio BONES" and "Studio Bones" both normalize to "studio bones"
    e1 = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.STUDIO, name="Studio BONES",
        external_ids=ExternalIds(anilist_studio_id=11),
    ))
    e2 = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.STUDIO, name="Studio Bones",
        external_ids=ExternalIds(mal_studio_id=99),
    ))

    assert e1 == e2
    assert len(sidecar.entities) == 1
    rec = sidecar.entities[e1]
    assert rec.external_ids.anilist_studio_id == 11
    assert rec.external_ids.mal_studio_id == 99


def test_upsert_does_not_collapse_different_kind():
    """Same name but different kind must NOT be merged."""
    from media_archivist.entities import upsert_entity
    from metadatarr.resolve.entities import EntityKind, EntitySidecar, ProviderEntity
    from mediavocab.models import ExternalIds

    sidecar = EntitySidecar()
    upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.STUDIO, name="Bones",
        external_ids=ExternalIds(anilist_studio_id=11),
    ))
    upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.ARTIST, name="Bones",
        external_ids=ExternalIds(musicbrainz_artist="some-mbid"),
    ))
    assert len(sidecar.entities) == 2


def test_upsert_same_external_id_updates_in_place():
    """Exact same dominant external ID should update the existing record (original path)."""
    from media_archivist.entities import upsert_entity
    from metadatarr.resolve.entities import EntityKind, EntitySidecar, ProviderEntity
    from mediavocab.models import ExternalIds

    sidecar = EntitySidecar()
    eid = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.STUDIO, name="MAPPA",
        external_ids=ExternalIds(anilist_studio_id=569),
    ))
    eid2 = upsert_entity(sidecar, ProviderEntity(
        kind=EntityKind.STUDIO, name="Mappa",   # slightly different casing
        external_ids=ExternalIds(anilist_studio_id=569),
    ))
    assert eid == eid2
    assert len(sidecar.entities) == 1
    # "Mappa" normalizes to same string as "MAPPA" so merge_alias skips it
    rec = sidecar.entities[eid]
    assert rec.name == "MAPPA"
    assert rec.aliases == []


def test_dominant_external_id_prefers_anilist_staff_over_mal():
    """anilist_staff_id is preferred over mal_person_id for director entities."""
    from metadatarr.resolve.entities import EntityKind, _dominant_external_id
    from mediavocab.models import ExternalIds

    ext = ExternalIds(anilist_staff_id=97009, mal_person_id=5042)
    dom = _dominant_external_id(ext, EntityKind.DIRECTOR)
    assert dom == "97009"


def test_dominant_external_id_falls_back_to_mal_person():
    from metadatarr.resolve.entities import EntityKind, _dominant_external_id
    from mediavocab.models import ExternalIds

    ext = ExternalIds(mal_person_id=5042)
    dom = _dominant_external_id(ext, EntityKind.DIRECTOR)
    assert dom == "5042"


def test_dominant_external_id_studio_prefers_anilist():
    from metadatarr.resolve.entities import EntityKind, _dominant_external_id
    from mediavocab.models import ExternalIds

    ext = ExternalIds(anilist_studio_id=14, mal_studio_id=42)
    dom = _dominant_external_id(ext, EntityKind.STUDIO)
    assert dom == "14"
