"""Offline demonstration of variant-aware signals, entities, and providers.

Run from the repo root:
    python examples/variants.py
"""
from __future__ import annotations

from typing import List, Optional

from mediavocab import MediaType
from metadatarr.resolve.entities import (
    EntityKind,
    ProviderEntity,
    allocate_entity_id,
)
from mediavocab.models import ExternalIds
from mediavocab import MediaType, VariantKind
from mediavocab.models.signals import Signals, compare_signals as compare, signal_hash
from metadatarr.resolve.base import (
    MetadataProvider,
    ProviderMatch,
    register,
)


# ---------------------------------------------------------------------------
# 1. Constructing Signals with variant fields
# ---------------------------------------------------------------------------
print("=== 1. Constructing Signals with variant fields ===")

signals = Signals(
    title="Blade Runner",
    year=1982,
    medium=MediaType.MOVIE,
    variant_kind=VariantKind.DIRECTORS,
    edition="The Final Cut",
    region="US",
    source_format="Blu-ray",
)
print(f"  variant_kind : {signals.variant_kind}")
print(f"  edition      : {signals.edition}")
print(f"  region       : {signals.region}")
print(f"  source_format: {signals.source_format}")
print(f"  include_variants (default): {signals.include_variants}")


# ---------------------------------------------------------------------------
# 2. Different variant_kind → conflict
# ---------------------------------------------------------------------------
print("\n=== 2. Different variant_kind → conflict ===")

theatrical = Signals(
    title="Blade Runner",
    medium=MediaType.MOVIE,
    variant_kind=VariantKind.THEATRICAL,
)
directors = Signals(
    title="Blade Runner",
    medium=MediaType.MOVIE,
    variant_kind=VariantKind.DIRECTORS,
)

conflicts = compare(theatrical, directors)
assert len(conflicts) == 1
assert conflicts[0].signal == "variant_kind"
print(f"  conflict: signal={conflicts[0].signal!r}  "
      f"ours={conflicts[0].ours!r}  theirs={conflicts[0].theirs!r}")


# ---------------------------------------------------------------------------
# 3. Same variant_kind → no conflict
# ---------------------------------------------------------------------------
print("\n=== 3. Same variant_kind → no conflict ===")

same_conflicts = compare(directors, directors)
assert same_conflicts == []
print("  no conflicts (as expected)")


# ---------------------------------------------------------------------------
# 4. signal_hash() distinguishes variants
# ---------------------------------------------------------------------------
print("\n=== 4. signal_hash() distinguishes variants ===")

h_theatrical = signal_hash(theatrical)
h_directors = signal_hash(directors)
assert h_theatrical != h_directors
print(f"  theatrical hash : {h_theatrical}")
print(f"  directors  hash : {h_directors}")


# ---------------------------------------------------------------------------
# 5. ExternalIds with physical-media variant fields
# ---------------------------------------------------------------------------
print("\n=== 5. ExternalIds with variant + physical-media fields ===")

ext = ExternalIds(
    imdb="tt0083658",
    fanedit_id=12345,
    derived_from_imdb="tt0083658",
    bluray_com_id=6543,
    dvdcompare_id="blade-runner-1982",
    discogs_release=7890123,
)
print(f"  imdb             : {ext.imdb}")
print(f"  fanedit_id       : {ext.fanedit_id}")
print(f"  derived_from_imdb: {ext.derived_from_imdb}")
print(f"  bluray_com_id    : {ext.bluray_com_id}")
print(f"  dvdcompare_id    : {ext.dvdcompare_id}")
print(f"  discogs_release  : {ext.discogs_release}")


# ---------------------------------------------------------------------------
# 6. ProviderEntity with EntityRole.RELEASE
# ---------------------------------------------------------------------------
print("\n=== 6. ProviderEntity with EntityRole.RELEASE ===")

release_entity = ProviderEntity(
    role=EntityRole.RELEASE,
    name="Blade Runner — The Final Cut (Blu-ray, US)",
    external_ids=ExternalIds(
        musicbrainz_release="a1b2c3d4-0000-0000-0000-000000000000",
        imdb="tt0083658",
    ),
)
print(f"  kind: {release_entity.kind}")
print(f"  name: {release_entity.name}")


# ---------------------------------------------------------------------------
# 7. allocate_entity_id for a RELEASE entity
# ---------------------------------------------------------------------------
print("\n=== 7. allocate_entity_id for a RELEASE entity ===")

# With musicbrainz_release → stable, reproducible
entity_id = allocate_entity_id(
    EntityRole.RELEASE,
    name=release_entity.name,
    external_ids=release_entity.external_ids,
)
entity_id2 = allocate_entity_id(
    EntityRole.RELEASE,
    name="anything else",
    external_ids=release_entity.external_ids,
)
assert entity_id == entity_id2, "dominant external id must produce stable id"
print(f"  entity_id (musicbrainz_release, stable): {entity_id}")

# With only fanedit_id → falls back to fanedit_id as dominant
ext_fanedit = ExternalIds(fanedit_id=99001)
entity_id_fe = allocate_entity_id(
    EntityRole.RELEASE,
    name="Some Fanedit",
    external_ids=ext_fanedit,
)
entity_id_fe2 = allocate_entity_id(
    EntityRole.RELEASE,
    name="ignored",
    external_ids=ext_fanedit,
)
assert entity_id_fe == entity_id_fe2
print(f"  entity_id (fanedit_id, stable):          {entity_id_fe}")

# Without any dominant id → name-based
ext_none = ExternalIds(imdb="tt0083658")  # imdb not dominant for RELEASE
entity_id_name = allocate_entity_id(
    EntityRole.RELEASE,
    name="Blade Runner — The Final Cut (Blu-ray, US)",
    external_ids=ext_none,
)
print(f"  entity_id (name-based):                  {entity_id_name}")


# ---------------------------------------------------------------------------
# 8. Stub list_variants() provider
# ---------------------------------------------------------------------------
print("\n=== 8. Stub list_variants() provider ===")


class FaneditProvider(MetadataProvider):
    """Demonstrates the list_variants() extension point."""

    name = "example_fanedit"
    media = {MediaType.MOVIE}

    def is_available(self) -> bool:
        return True

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        return None

    def list_variants(
        self,
        external_ids: ExternalIds,
        signals: Optional[Signals] = None,
    ) -> List[ProviderEntity]:
        if not external_ids.imdb:
            return []
        return [
            ProviderEntity(
                role=EntityRole.RELEASE,
                name=f"Blade Runner: Workprint [Fanedit] (from {external_ids.imdb})",
                external_ids=ExternalIds(
                    fanedit_id=99001,
                    derived_from_imdb=external_ids.imdb,
                ),
            )
        ]


register(FaneditProvider())

provider = FaneditProvider()
parent_ext = ExternalIds(imdb="tt0083658")
variants = provider.list_variants(parent_ext)
assert len(variants) == 1
assert variants[0].kind == EntityRole.RELEASE
print(f"  variant name     : {variants[0].name}")
print(f"  fanedit_id       : {variants[0].external_ids.fanedit_id}")
print(f"  derived_from_imdb: {variants[0].external_ids.derived_from_imdb}")

fan_out = Signals(title="Blade Runner", medium=MediaType.MOVIE, include_variants=True)
print(f"\n  include_variants=True: {fan_out.include_variants}")
print("  (orchestrator would call list_variants() on each active provider)")

print("\nAll assertions passed.")
