# Changelog

## [Unreleased]

### Workspace consolidation

- **media-archivist now consumes metadatarr's resolver framework.** The
  parallel resolver-and-provider stack that lived in
  `media_archivist.providers` and `media_archivist.models.signals` has
  been removed; everything below is imported from
  `metadatarr.resolve.*`.
- `metadatarr` is a hard runtime dep.
- Local `Medium` enum dropped — use `mediavocab.MediaType` directly.
  Provider routing now uses the two-axis `(media_type, content_genres)`
  gate metadatarr added so anime/manga providers select on
  `content_genres=["anime"]/["manga"]` instead of a fake
  `MediaType.ANIME` value (mediavocab spec axiom 2: anime is genre, not
  type).
- `Signals` / `SignalConflict` / `compare` / `merged` / `signal_hash`
  now imported from `metadatarr.resolve.signals`.
- `ExternalIds` now imported from `metadatarr.resolve.external_ids`.
- `EntityRecord` / `EntityKind` / `ProviderEntity` / `EntitySidecar`
  now imported from `metadatarr.resolve.entities`. The
  metadatarr-side `EntityKind` gained `VOICE_ACTOR`, `STUDIO`,
  `CHARACTER` plus `to_mediavocab_kind()` / `to_mediavocab_role()`
  bridges for callers constructing canonical `mediavocab.Entity` /
  `Credit` records.

### Moved (media-archivist → metadatarr)

These provider modules were re-homed:

- `anilist`, `jikan` (anime/manga), `google_books`, `librivox`,
  `podcast_index` (Apple Podcasts), `tmdb`, `arr` (Sonarr/Radarr/
  Lidarr/Readarr).

### Deleted

- `media_archivist.providers.musicbrainz` and `…wikidata` (duplicates of
  metadatarr's).
- `media_archivist.providers.metadatarr` (wrapper made redundant by the
  registry consolidation).
- `media_archivist.providers.base` — provider base class moved to
  `metadatarr.resolve.base`.
- `media_archivist.models.signals` / `external_ids` / `entities` —
  redirected to metadatarr's modules.

### Kept

- `media_archivist.providers.metalarchives` (uses `pymetal`; lives here
  next to the source-DB orchestrator until the metalarchives data flow
  generalises).
- `canonicalize.py` — the source-DB orchestrator (sidecars, walks the
  local index, applies quarantine policy).
- `models/canonical_record.py`, `models/raw.py`, the index/discover/sync/
  CLI/server stack.

### MediaType-split downstream

- `Medium.TV` (now `MediaType.TV` from mediavocab) is reserved for live
  linear / IPTV broadcast channels. On-demand series catalogues
  (Sonarr, Anime, TV episodes from streaming) are
  `MediaType.EPISODIC_SERIES`. Tests and provider gates updated.
