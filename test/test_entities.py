"""Entity layer — sidecar I/O, allocation, merge, --where dotted access."""
from __future__ import annotations

import json

import pytest

from media_archivist.canonicalize import canonicalize
from media_archivist.entities import (
    attach_work,
    load_entities,
    save_entities,
    upsert_entity,
)
from media_archivist.index import Index, WhereError
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.entities import (
    EntityKind,
    EntityRecord,
    EntitySidecar,
    ProviderEntity,
    allocate_entity_id,
)
from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import Medium, Signals
from media_archivist.providers.base import (
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
    media = {Medium.MUSIC, Medium.MOVIE}

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
                        medium=Medium.MUSIC),
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
                        medium=Medium.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
        relations={EntityKind.ARTIST: [ProviderEntity(
            kind=EntityKind.ARTIST, name="Foo",
            external_ids=ExternalIds(musicbrainz_artist="mb-artist-1"),
        )]},
    )))
    register(SecondStub(ProviderMatch(
        provider="stub_second", confidence=0.9,
        signals=Signals(title="Hello", artist="Foo", runtime=240,
                        medium=Medium.MUSIC),
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
                        medium=Medium.MUSIC),
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
                                runtime=signals.runtime, medium=Medium.MUSIC),
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
