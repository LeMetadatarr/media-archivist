# Changelog

## [Unreleased]

This release completes the migration to `metadatarr` as the resolver
backbone. `media-archivist` now focuses on its core competency —
indexing local source-DBs and orchestrating canonicalization — and
delegates all provider/signal/entity logic to `metadatarr`. Released
after `metadatarr`'s next tag.

### Resolver

- `media_archivist.providers` is now a thin re-export of
  `metadatarr.resolve.providers`. The 24+ resolver providers, the
  `Provider` base class, and the registry all live in `metadatarr`.
  No parallel framework in this package.
- `Signals`, `SignalConflict`, `compare`, `merged`, `signal_hash` come
  from `metadatarr.resolve.signals`.
- `ExternalIds` comes from `metadatarr.resolve.external_ids`.
- `EntityRecord`, `ProviderEntity`, `EntitySidecar` come from
  `metadatarr.resolve.entities`.
- `metadatarr` is now a hard runtime dependency.

### Models

- **EntityKind / EntityRole split.** `EntityRecord` carries both:
  - `kind` — structural mediavocab type (PERSON, ORGANIZATION, …),
    used for canonical identity.
  - `role` — relational metadatarr role (DIRECTOR, AUTHOR,
    VOICE_ACTOR, STUDIO, CHARACTER, …), used for credits / linking.
- New helper `entities_by_role()` alongside the existing
  `entities_by_kind()` so callers can group sidecars by either axis.
- The local `Medium` enum is gone — use `mediavocab.MediaType`
  directly.

### Routing

- **End-to-end modality routing.** `signals_from_entry()` now derives
  `Signals.modality` from the entry's medium via
  `mediavocab.infer_modality()`. The two-axis
  `(media_type, content_genres)` gate from metadatarr now sees the
  correct modality for every row.
- Bandcamp and SoundCloud archivist rows route to AUDIO providers
  (MusicBrainz, Discogs, Last.fm, …) automatically.
- Plain YouTube / Internet Archive rows leave `modality=None` so the
  generic gate still applies.
- Anime / manga gating uses `content_genres=["anime"]` /
  `["manga"]` instead of a synthetic `MediaType.ANIME` (mediavocab
  spec axiom 2: anime is a genre, not a type).

### Bug fixes

- **SoundCloud archivist** was treating `nuvem_de_som` output as
  `dict`s after that library migrated to typed `Release` objects.
  Now consumes the typed objects correctly.
- **Entities live-availability check** was filtering on `r.role`
  (relational), so structural entity sidecars were skipped. Fixed to
  filter on `r.kind`.
- Example `check_metadatarr.py` used the retired `MediaType.TV`
  constant for streaming series; now uses `EPISODIC_SERIES` (TV is
  reserved for live/IPTV channels).

### Examples

- New `examples/learn/` zero-to-hero curriculum, five steps:
  1. `01_index_an_archive.py` — point the indexer at a local folder.
  2. `02_canonicalize.py` — run the resolver, write sidecars.
  3. `03_inspect_entities.py` — group by kind and by role.
  4. `04_modality_routing.py` — show how medium drives provider gate.
  5. `05_export_dataset.py` — dump a clean canonical dataset.

### Removed

- `media_archivist.metalarchives` (`MetalArchivesArchivist`) —
  metal-archives.com hosts no audio, so it never fit the indexer
  abstraction (which walks a local audio library). The metadatarr
  resolver provider for Encyclopaedia Metallum remains.
- `--metal-archives` CLI flag and `Source.METAL_ARCHIVES` enum
  variant.
- Local `Medium` enum (replaced by `mediavocab.MediaType`).
- Local resolver framework: `models/signals.py`,
  `models/external_ids.py`, `models/entities.py` (now re-exports
  from metadatarr).
- Duplicate resolver providers: `musicbrainz`, `wikidata`,
  `metalarchives`, and the `metadatarr` wrapper provider.
- Provider base class `media_archivist.providers.base` (moved to
  `metadatarr.resolve.base`).

### Kept

- `canonicalize.py` — the source-DB orchestrator (sidecars,
  quarantine, walks the local index).
- `models/canonical_record.py`, `models/raw.py`, and the full
  index / discover / sync / CLI / server stack.
