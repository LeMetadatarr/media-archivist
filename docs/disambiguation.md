# Disambiguation, canonical IDs & external providers

`media_archivist` mints a **canonical_id** per *work* and links it to
authoritative external IDs (MusicBrainz, IMDb, TMDB, TVDB, ISBN, the
Arr stack). Two rows describe the same work only if **every overlapping
disambiguation signal agrees**; on any disagreement the row is
**quarantined** for review instead of silently collapsed.

## Identity model

```
local row id   sha1(source:url)            — never changes
   │
   ▼
canonical_id   sha1(immutable signal set)  — assigned per work
   │
   ▼
external_ids   mbid, tt-id, tmdb, tvdb, isbn, …  — populated by providers
```

- `local row id`: per-source-row, computed from the URL.
- `canonical_id`: opaque hex, allocated from the merged signal set
  (title, artist, year, country, runtime, medium, language) the first
  time a row matches a provider response. Stamped onto the raw row
  under `_meta.canonical_id`; indexed in the `<db>.canonical.json`
  sidecar.
- `external_ids`: any provider's authoritative IDs. Multiple providers
  can contribute (MusicBrainz recording + Wikidata Q-id + IMDb tt-id);
  no precedence — they coexist.

## Disambiguation signals

`mediavocab.Signals` carries the bag of facts we
compare. Tolerances are conservative:

| Signal     | Compared by               | Tolerance              |
| ---------- | ------------------------- | ---------------------- |
| `title`    | normalized fuzzy ratio    | ≥ 0.92                 |
| `artist`   | normalized fuzzy ratio    | ≥ 0.90                 |
| `year`     | absolute delta            | ±1                     |
| `country`  | exact ISO 3166 (case-insensitive) | exact          |
| `runtime`  | absolute delta            | ±5 s                   |
| `medium`   | exact enum                | exact (movie/tv/music/book/podcast) |
| `language`      | exact ISO 639-1                   | exact                  |
| `variant_kind`  | exact enum                        | exact                  |
| `region`        | exact ISO 3166 (case-insensitive) | exact                  |
| `source_format` | exact (case-insensitive)          | exact                  |
| `edition`       | exact (case-insensitive)          | exact                  |

Comparison rules (`mediavocab.compare_signals`):

- A signal absent on either side is **not** a disagreement.
- All overlapping signals must agree → matched.
- **Any single overlapping signal disagrees → quarantined.**

## Quarantine policy

When a provider responds but disagrees with our local signals on at
least one field, the row is added to `<db>.quarantine.json` with the
conflicting signals recorded. No `canonical_id` is stamped on the
row; its `_meta.canonical_status` becomes `"quarantined"`.

To resolve a quarantined row:

```bash
# accept — link to existing or allocate a new canonical_id from the proposal
media-archivist quarantine-resolve --db-file talks.json \
    --row-id <id> [--canonical-id <id>]

# reject — force a brand-new canonical_id distinct from the proposal
media-archivist quarantine-reject  --db-file talks.json --row-id <id>
```

`reject` salts the new id so it can never collide with the proposed
one — useful when two films share a name and a year but differ on
country / runtime / language.

## Storage layout

The source DB stays diff-stable. The only writes per row are:

```json
"_meta": {
  "canonical_id": "<sha1>",
  "canonical_status": "matched"   // | "quarantined" | "unmatched"
}
```

Two sidecars sit next to the DB:

| File                       | Schema                                     |
| -------------------------- | ------------------------------------------ |
| `<db>.canonical.json`      | `CanonicalSidecar` — `canonical_id → record` |
| `<db>.quarantine.json`     | `QuarantineSidecar` — `row_id → entry` |

Each `CanonicalRecord` carries `signals`, `members` (row ids that
collapsed into the work), `external_ids`, and a `provider_log` of
hits with timestamps + confidence.

## Built-in providers

All providers live in `metadatarr` and self-register on import. Missing
API keys or endpoint URLs disable the corresponding provider at runtime;
`media-archivist providers` reports which are active.

For the full table of ~24 providers (MusicBrainz, Wikidata, TMDB, AniList,
Jikan, Google Books, LibriVox, Apple Podcasts, *arr family, Discogs,
Blu-ray.com, DVDCompare, OpenLibrary, Anna's Archive, Bandcamp, SoundCloud,
YouTube/YT Music, Metal Archives, AudioDB, TVMaze, …) and their env-var
configuration keys, see [metadatarr resolver integration](./metadatarr.md).

### Modality routing

Providers declare `modality: ClassVar[Set[PlaybackModality]]` alongside
`media` and `genre_filter` (mediavocab spec §5.10). All three axes are
evaluated independently; a provider is skipped if any declared axis does
not match the request.

`PlaybackModality` (from `mediavocab`) carries: `AUDIO`, `VIDEO`,
`INTERACTIVE`, `TEXT`, `UNKNOWN`. Examples: `discogs` declares
`{AUDIO, VIDEO}`; `dvdcompare` and `arr_radarr` declare `{VIDEO}`;
`librivox`, `audiodb`, and `arr_lidarr` declare `{AUDIO}`;
`annas_archive` and `arr_readarr` declare `{TEXT}`; `wikidata` and
`youtube` are universal (empty set).

To restrict a resolve call to audio-only providers:

```python
from mediavocab import PlaybackModality
from mediavocab.models.signals import Signals

signals = Signals(title="Kind of Blue", modality=PlaybackModality.AUDIO)
```

`media_archivist.canonicalize.signals_from_entry()` does not currently
populate `modality` — the field is `None` on auto-generated signals and
therefore does not gate any provider. Callers that construct `Signals`
directly can set it. — `metadatarr/resolve/base.py:118`

### Provider contract

```python
from metadatarr.resolve.base import (
    MetadataProvider, ProviderMatch, register,
)

class MyProvider(MetadataProvider):
    name = "my_provider"
    media = {MediaType.MUSIC}

    def is_available(self) -> bool: ...
    def lookup(self, signals: Signals) -> Optional[ProviderMatch]: ...
    # Optional: override list_variants() to support signals.include_variants=True
    # def list_variants(self, external_ids, signals=None): ...

register(MyProvider())
```

`include_variants` (default `False`) is a fan-out control flag on `Signals`,
not a disambiguating signal. It does not appear in the comparison table above
and is not included in `signal_hash()`. When `True`, the orchestrator calls
`list_variants()` on each active provider after the primary `lookup()` step.
See [Release variants](./variants.md) for full details.

`ProviderMatch` is the pydantic carrier:

```python
ProviderMatch(
    provider="my_provider",
    confidence=0.93,                  # 0–1
    signals=Signals(...),             # what they think the work is
    external_ids=ExternalIds(...),    # authoritative IDs they produced
)
```

## CLI

```bash
# What's wired up given the current env?
media-archivist providers

# Run providers across the DB; fill canonical.json + stamp _meta.canonical_id.
media-archivist canonicalize --db-file talks.json \
    --providers musicbrainz --providers wikidata

# Inspect quarantine.
media-archivist quarantine-list --db-file talks.json

# Surfaces canonical_id / external_ids in the canonical view.
media-archivist list  --db-file talks.json --canonical \
    --where 'external_ids.imdb!=None'
```

## Verification

End-to-end:

```bash
media-archivist add        --db-file demo.json https://www.youtube.com/@SomeChannel
media-archivist canonicalize --db-file demo.json --providers wikidata
jq '.records[].external_ids' demo.canonical.json
```

`media-archivist providers` reports active providers; the orchestrator
no-ops cleanly when nothing is active. The 10 disambiguation tests
under `test/test_disambiguation.py` cover the full
match / quarantine / resolve / reject flow without network access.
