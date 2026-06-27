# Release variants

A *release variant* is a distinct version of a work that shares its origin but
differs in content, mastering, regional licensing, or community modification.
Examples: a director's cut, a deluxe album edition, a regional pressing, a
fanedit, an upscaled remaster.

`media_archivist` models variants as first-class `EntityKind.RELEASE` entities
and exposes the disambiguation signals needed to tell them apart.

## `VariantKind` enum

`mediavocab.VariantKind`

Values are grouped by the medium they typically apply to:

### Film variants

| Value         | Meaning                                              |
| ------------- | ---------------------------------------------------- |
| `theatrical`  | Original cinema release, unaltered.                  |
| `directors`   | Director's cut (additional / restructured footage).  |
| `extended`    | Extended cut with restored scenes.                   |
| `fanedit`     | Community-produced edit (registered on IFDB).        |
| `colorized`   | Colorized version of a black-and-white original.     |
| `upscaled`    | AI or manual upscale (e.g. 4K restoration).          |

### Album variants

| Value          | Meaning                                             |
| -------------- | --------------------------------------------------- |
| `standard`     | Original studio release.                            |
| `deluxe`       | Deluxe edition (bonus content added).               |
| `bonus_tracks` | Bonus-tracks-only variant (same base, extra tracks).|
| `reissue`      | Label reissue, potentially with new liner notes.    |
| `compilation`  | Compilation or anthology of existing material.      |

### Shared (film and music)

| Value       | Meaning                                                |
| ----------- | ------------------------------------------------------ |
| `regional`  | Region-specific release (different track list or cut). |
| `remastered`| Remastered audio or video; same content, new transfer. |
| `other`     | Any variant not covered above.                         |

## `Signals` fields

`mediavocab.Signals`

| Field              | Type                      | Default  | Purpose                                                                  |
| ------------------ | ------------------------- | -------- | ------------------------------------------------------------------------ |
| `variant_kind`     | `Optional[VariantKind]`   | `None`   | Which kind of variant this is.                                           |
| `edition`          | `Optional[str]`           | `None`   | Free-text edition name, e.g. `"Criterion Collection"`.                   |
| `region`           | `Optional[str]`           | `None`   | ISO 3166-1 alpha-2 release region (not origin country).                  |
| `source_format`    | `Optional[str]`           | `None`   | Physical or digital format: `"4K"`, `"Blu-ray"`, `"Vinyl"`, `"SACD"`. |
| `include_variants` | `bool`                    | `False`  | Fan-out flag — when `True`, variant-aware providers call `list_variants()`. |

### Comparison behaviour

`compare()` — treats all four
descriptive fields as exact matches (case-insensitive). An absent value on
either side is not a disagreement; two present values that differ produce a
`SignalConflict`.

`signal_hash()` — includes all four
fields so different variants of the same work get different canonical IDs.

`merged()` — first non-`None` value
wins per field. `include_variants` is ORed across all bags.

`include_variants` does **not** participate in comparison or hashing; it is a
fan-out control flag, not a disambiguating signal.

## `EntityKind.RELEASE`

`metadatarr.resolve.entities.EntityKind.RELEASE` — `metadatarr/resolve/entities.py`

A `RELEASE` entity represents a specific physical or digital release of a work:
a particular MusicBrainz release (not just the release group), a Discogs
pressing, or a fanedit registered on IFDB. It differs from `ALBUM`, which maps
to a *release group* (the abstract work), not any single pressing.

The dominant external id for `RELEASE` is resolved in
`_dominant_external_id()` — `metadatarr/resolve/entities.py`:

1. `musicbrainz_release` (MusicBrainz release MBID)
2. `fanedit_id` (IFDB WordPress post ID) — used when no MB release exists

## `ExternalIds` fields

`mediavocab.ExternalIds`

| Field             | Type            | Source                                          |
| ----------------- | --------------- | ----------------------------------------------- |
| `fanedit_id`      | `Optional[int]` | IFDB (Internet Fanedit Database) WordPress post ID. |
| `derived_from_imdb` | `Optional[str]` | Parent IMDb `tt-id` when this record is a variant of a film. |
| `discogs_release` | `Optional[int]` | Discogs numeric release ID (pressing-level, not master). |
| `bluray_com_id`   | `Optional[int]` | blu-ray.com movie ID.                           |
| `dvdcompare_id`   | `Optional[str]` | dvdcompare.net film slug / ID.                  |

## `MetadataProvider.list_variants()`

`metadatarr.resolve.base.MetadataProvider.list_variants` — `metadatarr/resolve/base.py`

```python
def list_variants(self, external_ids: ExternalIds,
                  signals: Optional[Signals] = None) -> List[ProviderEntity]:
```

Called when `signals.include_variants=True`. Returns a list of
`ProviderEntity` objects with `kind=EntityKind.RELEASE`. The default
implementation returns `[]`; override in variant-aware providers.

The caller passes the already-resolved `external_ids` for the primary work so
the provider knows which catalogue record to fan out from.

## Code examples

### Constructing `Signals` with variant fields

```python
from mediavocab import Signals, VariantKind

# Director's cut of a 1982 film released on Blu-ray in the US
signals = Signals(
    title="Blade Runner",
    year=1982,
    medium=MediaType.MOVIE,
    variant_kind=VariantKind.DIRECTORS,
    edition="The Final Cut",
    region="US",
    source_format="Blu-ray",
)
```

### Checking for conflicts between variants

```python
from mediavocab import Signals, VariantKind
from mediavocab.models.signals import compare_signals as compare

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
# → [SignalConflict(signal='variant_kind', ours='theatrical', theirs='directors')]
assert len(conflicts) == 1

same = compare(directors, directors)
assert same == []
```

### Differentiating variants via `signal_hash()`

```python
from mediavocab import Signals, VariantKind
from mediavocab.models.signals import signal_hash

h_theatrical = signal_hash(Signals(title="Blade Runner", medium=MediaType.MOVIE,
                                    variant_kind=VariantKind.THEATRICAL))
h_directors  = signal_hash(Signals(title="Blade Runner", medium=MediaType.MOVIE,
                                    variant_kind=VariantKind.DIRECTORS))
assert h_theatrical != h_directors
```

### Creating `ExternalIds` with variant fields

```python
from mediavocab import ExternalIds

ext = ExternalIds(
    imdb="tt0083658",              # Blade Runner (1982) — the parent film
    fanedit_id=12345,              # IFDB post ID for a specific fanedit
    derived_from_imdb="tt0083658", # this entity is derived from that film
    bluray_com_id=6543,
)
```

### Creating and allocating a `RELEASE` entity

```python
from metadatarr.resolve.entities import EntityKind, ProviderEntity, allocate_entity_id
from mediavocab import ExternalIds

ext = ExternalIds(musicbrainz_release="a1b2c3d4-...")

entity = ProviderEntity(
    kind=EntityKind.RELEASE,
    name="Blade Runner — The Final Cut (Blu-ray, US)",
    external_ids=ext,
)

entity_id = allocate_entity_id(EntityKind.RELEASE, name=entity.name,
                                external_ids=ext)
```

### Implementing a variant-aware provider

```python
from typing import List, Optional
from mediavocab import ExternalIds
from metadatarr.resolve.entities import EntityKind, ProviderEntity
from mediavocab import MediaType, Signals, VariantKind
from metadatarr.resolve.base import MetadataProvider, ProviderMatch, register


class MyFaneditProvider(MetadataProvider):
    name = "my_fanedit"
    media = {MediaType.MOVIE}

    def is_available(self) -> bool:
        return True  # no config needed

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        return None  # primary lookup not implemented

    def list_variants(self, external_ids: ExternalIds,
                      signals: Optional[Signals] = None) -> List[ProviderEntity]:
        """Fan out to known fanedits for the given IMDb id."""
        if not external_ids.imdb:
            return []
        # In a real provider, query IFDB here.
        return [
            ProviderEntity(
                kind=EntityKind.RELEASE,
                name="Blade Runner: Workprint (1982) [Fanedit]",
                external_ids=ExternalIds(
                    fanedit_id=99001,
                    derived_from_imdb=external_ids.imdb,
                ),
            )
        ]


register(MyFaneditProvider())
```
