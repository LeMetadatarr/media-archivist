"""metal-archives resolver provider — fully offline.

The metal-archives backend was removed in 0.2 — metal-archives.com is a
metadata catalogue, not a streaming source, so it didn't fit the
"index streams; download on demand" abstraction. The resolver provider
in metadatarr (``metadatarr.resolve.providers.metal_archives``) covers
the metadata side, including the `metal_archives_*` ExternalIds fields
and the artist/label dominant-id rules.
"""
from __future__ import annotations

import pytest

from mediavocab import MediaType
from mediavocab.models import ExternalIds
from mediavocab.models.signals import Signals
from metadatarr.resolve.entities import EntityKind, allocate_entity_id
from metadatarr.resolve.providers.metal_archives import MetalArchivesProvider

from media_archivist.providers import all_providers


def test_provider_registered_and_available():
    providers = all_providers()
    assert "metal_archives" in providers
    assert isinstance(providers["metal_archives"], MetalArchivesProvider)


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
    """Without a MusicBrainz mbid the MA band id is the dominant identifier."""
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


def test_metalarchives_provider_skips_non_music():
    p = MetalArchivesProvider()
    if not p.is_available():
        pytest.skip("pymetal not installed in this environment")
    sig = Signals(title="Tenet", artist="Christopher Nolan", medium=MediaType.MOVIE)
    assert p.lookup(sig) is None


def test_metalarchives_provider_no_artist_signal():
    p = MetalArchivesProvider()
    if not p.is_available():
        pytest.skip("pymetal not installed in this environment")
    # No artist → must short-circuit (no upstream call).
    assert p.lookup(Signals(title="Untitled")) is None
