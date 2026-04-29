"""Encyclopaedia Metallum backend + provider — fully offline."""
from __future__ import annotations

import pytest

from media_archivist.metalarchives import _length_to_seconds
from media_archivist.models import (
    RawMetalArchivesEntry,
    Source,
)
from media_archivist.models.entities import EntityKind, allocate_entity_id
from media_archivist.models.external_ids import ExternalIds
from media_archivist.providers import all_providers
from media_archivist.providers.metalarchives import (
    MetalArchivesProvider,
    _length_to_seconds as provider_length,
)
from media_archivist.views import to_media_entry


def test_length_parser_mm_ss():
    assert _length_to_seconds("4:32") == 272.0
    assert _length_to_seconds("0:45") == 45.0


def test_length_parser_hh_mm_ss():
    assert _length_to_seconds("1:02:03") == 3723.0


def test_length_parser_returns_none_on_garbage():
    assert _length_to_seconds(None) is None
    assert _length_to_seconds("nope") is None


def test_provider_length_helper():
    assert provider_length("4:32") == 272.0
    assert provider_length(None) is None


def test_provider_registered_and_available():
    providers = all_providers()
    assert "metal_archives" in providers
    # Available iff pymetal is importable; in this venv it is.
    assert isinstance(providers["metal_archives"], MetalArchivesProvider)


def test_raw_entry_round_trip():
    e = RawMetalArchivesEntry(
        url="https://www.metal-archives.com/release.php?releaseID=1&songID=2",
        title="De Mysteriis Dom Sathanas",
        artist="Mayhem",
        album="De Mysteriis Dom Sathanas",
        band_id=67,
        release_id=1,
        song_id=2,
        duration=389.0,
        length="6:29",
        release_date="May 24th, 1994",
        release_type="full-length",
        country="Norway",
        genres=["Black Metal"],
        themes=["Anti-Christianity", "Death", "Evil"],
        label_name="Deathlike Silence Productions",
    )
    again = RawMetalArchivesEntry.model_validate(e.model_dump(mode="json"))
    assert again.source == Source.METAL_ARCHIVES
    assert again.duration == 389.0
    assert "Black Metal" in again.genres


def test_view_adapter_dispatches_to_metal_archives():
    raw = {
        "source": "metal_archives",
        "url": "https://www.metal-archives.com/release.php?releaseID=1&songID=2",
        "title": "Freezing Moon",
        "artist": "Mayhem",
        "album": "De Mysteriis Dom Sathanas",
        "duration": 386.0,
        "release_date": "May 24th, 1994",
        "tags": ["Black Metal"],
        "thumbnail": "https://example/cover.jpg",
        "band_id": 67, "release_id": 1, "song_id": 2,
    }
    e = to_media_entry(raw)
    assert e.source == Source.METAL_ARCHIVES
    assert e.title == "Freezing Moon"
    assert e.artist == "Mayhem"
    assert e.duration == 386.0
    assert "Black Metal" in e.tags
    assert e.thumbnail == "https://example/cover.jpg"


def test_external_ids_carries_metal_archives_fields():
    ext = ExternalIds(
        metal_archives_band=67,
        metal_archives_release=1,
        metal_archives_song=2,
        metal_archives_label=99,
    )
    again = ExternalIds.model_validate(ext.model_dump(mode="json"))
    assert again.metal_archives_band == 67
    assert again.metal_archives_label == 99


def test_dominant_external_for_artist_uses_ma_band():
    """Without a MusicBrainz mbid we fall back to the MA band id."""
    ma_only = allocate_entity_id(
        EntityKind.ARTIST, name="Mayhem",
        external_ids=ExternalIds(metal_archives_band=67),
    )
    by_name = allocate_entity_id(EntityKind.ARTIST, name="Mayhem")
    # Distinct from name-only because we have a different dominant id.
    assert ma_only != by_name
    # Two providers reporting the same MA band id converge.
    other = allocate_entity_id(
        EntityKind.ARTIST, name="MAYHEM",
        external_ids=ExternalIds(metal_archives_band=67),
    )
    assert ma_only == other


def test_dominant_external_for_label_uses_ma_label():
    a = allocate_entity_id(
        EntityKind.LABEL, name="Deathlike Silence",
        external_ids=ExternalIds(metal_archives_label=99),
    )
    b = allocate_entity_id(
        EntityKind.LABEL, name="Different Name Same Label",
        external_ids=ExternalIds(metal_archives_label=99),
    )
    assert a == b


def test_metalarchives_provider_skips_non_music(monkeypatch):
    from media_archivist.models.signals import Medium

    p = MetalArchivesProvider()
    if not p.is_available():
        pytest.skip("pymetal not installed in this environment")
    sig = Medium and __import__("media_archivist.models.signals",
                                fromlist=["Signals"]).Signals(
        title="Tenet", artist="Christopher Nolan", medium=Medium.MOVIE)
    assert p.lookup(sig) is None


def test_metalarchives_provider_no_artist_signal():
    from media_archivist.models.signals import Signals
    p = MetalArchivesProvider()
    if not p.is_available():
        pytest.skip("pymetal not installed in this environment")
    # No artist → must short-circuit (no upstream call).
    assert p.lookup(Signals(title="Untitled")) is None
