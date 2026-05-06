# metadatarr-backed providers

`metadatarr` ships typed clients for the public catalogues media_archivist
otherwise hand-rolls HTTP calls against. Each metadatarr client is exposed
as a separate provider — same `name` pattern as the `arr_*` providers —
so users can pick exactly which catalogues to consult via
`--providers metadatarr_radarr,metadatarr_openlibrary,…`.

| Provider                  | Backed by                                                  | Media       | External ids produced       |
| ------------------------- | ---------------------------------------------------------- | ----------- | --------------------------- |
| `metadatarr_skyhook`      | `ArrMetadataClient.search_series` (skyhook.sonarr.tv)     | TV          | `tvdb`, year                |
| `metadatarr_radarr`       | `ArrMetadataClient.search_movie` (radarrapi.servarr.com)   | movie       | `tmdb_movie`, year          |
| `metadatarr_lidarr`       | `ArrMetadataClient.search_artist` (api.lidarr.audio)      | music       | `musicbrainz_artist`, artist relation with mbid |
| `metadatarr_openlibrary`  | `OpenLibraryClient.search` (openlibrary.org)               | book        | `olid`, `isbn_10`, `isbn_13`, `author` relations with OLIDs |
| `metadatarr_bookinfo`     | `BookInfoClient.search` (Goodreads / Hardcover proxy)     | book        | `goodreads` (work), `extra.goodreads_book`, `isbn_13`, `author` relation with `extra.goodreads_author` |
| `metadatarr_discogs`      | `DiscogsClient.search_film` / `.search` (discogs.com API) | movie/music | `discogs_release`, `source_format`, `country` |
| `metadatarr_bluray`       | `BlurayComClient.search` (blu-ray.com HTML scraper)        | movie/TV    | `bluray_com_id`, `source_format="Blu-ray"` |
| `metadatarr_dvdcompare`   | `DVDCompareClient.search` (dvdcompare.net HTML scraper)    | movie/TV    | `dvdcompare_id`, `imdb`, `edition`, `region` |

None of these need configuration — no env vars, no API keys, no
self-hosted instances. `metadatarr_discogs` optionally reads `DISCOGS_TOKEN`
for a higher rate limit (60 vs 25 req/min) but works without one.

## Why split it five ways

Earlier the integration was a single `metadatarr` umbrella provider that
dispatched on `signals.medium`. Splitting matches:

- the existing `arr_*` pattern (Sonarr / Radarr / Readarr / Lidarr are
  separate providers), so `media-archivist providers` and
  `--providers …` show one-name-one-endpoint;
- per-source rate-limit budgets — skipping `metadatarr_bookinfo` for a
  music DB no longer drags every other metadatarr endpoint along;
- enrichment composability — `metadatarr_openlibrary` and `metadatarr_bookinfo`
  emit complementary book ids (OLID/ISBN vs Goodreads), so users can
  enable both for fuller cross-references or one for less network noise.

## Install

```bash
pip install /path/to/api_clients/metadatarr
# or, once on PyPI:
pip install media_archivist[metadatarr]
```

`metadatarr` itself depends on `requests`, `pydantic>=2`, `bs4` and `lxml`.

## Activation

All eight providers self-register when `metadatarr` imports cleanly:

```bash
$ media-archivist providers
[
  {"name": "metadatarr_bookinfo",    "active": true, "media": ["book"]},
  {"name": "metadatarr_bluray",      "active": true, "media": ["movie", "tv"]},
  {"name": "metadatarr_discogs",     "active": true, "media": ["movie", "music", "tv"]},
  {"name": "metadatarr_dvdcompare",  "active": true, "media": ["movie", "tv"]},
  {"name": "metadatarr_lidarr",      "active": true, "media": ["music"]},
  {"name": "metadatarr_openlibrary", "active": true, "media": ["book"]},
  {"name": "metadatarr_radarr",      "active": true, "media": ["movie"]},
  {"name": "metadatarr_skyhook",     "active": true, "media": ["tv"]},
  ...
]
```

There's nothing to configure — no env var, no URL, no key.

## Pairing recipes

The Servarr proxies return canonical ids and a year, but no cast / crew
/ runtime / country. Pair them with TMDB or your self-hosted Arr stack
to get full credits:

```bash
# Cheap id seed + full cast/crew
media-archivist canonicalize --db-file films.json \
    --providers metadatarr_radarr --providers tmdb

# OpenLibrary + Goodreads for fuller book cross-references
media-archivist canonicalize --db-file books.json \
    --providers metadatarr_openlibrary --providers metadatarr_bookinfo

# Physical-media enrichment for a film archive
media-archivist canonicalize --db-file films.json \
    --providers metadatarr_radarr \
    --providers metadatarr_bluray \
    --providers metadatarr_dvdcompare \
    --providers metadatarr_discogs
```

All providers run concurrently per row (up to 8 threads), so adding the
physical-media providers costs only as long as the slowest one, not their
sum.

Both metadatarr book providers emit `EntityKind.AUTHOR` relations; running
both populates entity records with both an `openlibrary_author` OLID
and a `goodreads_author` id under `extra`, deduplicated to one entity
when names match.

## Physical-media providers

`metadatarr_discogs`, `metadatarr_bluray`, and `metadatarr_dvdcompare`
fill in the physical-release `ExternalIds` fields that have no other source:

| Field             | Provider             | What it unlocks                                      |
| ----------------- | -------------------- | ---------------------------------------------------- |
| `discogs_release` | `metadatarr_discogs` | Discogs pressing-level id; label, catalogue number, cover image in `extra` |
| `bluray_com_id`   | `metadatarr_bluray`  | blu-ray.com movie id; regional specs, audio tracks via `enrich` |
| `dvdcompare_id`   | `metadatarr_dvdcompare` | dvdcompare.net film id; infers `edition` + `region` + `source_format` signals |

`metadatarr_dvdcompare` is the only provider that writes `Signals.edition`
and `Signals.region` — it will cause quarantine for rows where the local
signals disagree on those fields, which is the correct behaviour (a US
theatrical cut and a UK director's cut are different records).

## Variant support

When `signals.include_variants=True`, the canonicalize orchestrator calls
`list_variants()` on each active provider after the primary `lookup()` step.
metadatarr's own providers are positioned to fan out two catalogues:

- **MusicBrainz release expansion** — can resolve a release-group MBID into
  individual `EntityKind.RELEASE` entities, each carrying a `musicbrainz_release`
  MBID.
- **pyfanedit** (optional dep) — can query IFDB by parent IMDb `tt-id` and return
  `EntityKind.RELEASE` entities with `fanedit_id` and `derived_from_imdb` populated.

**Current status:** `list_variants()` is available as an extension point on
`MetadataProvider` (`metadatarr/resolve/base.py`) but is not yet wired
into the CLI `canonicalize` command. The metadatarr providers currently use
metadatarr for primary `lookup()` only. Variant fan-out is planned for a future
release.

See [Release variants](./variants.md) for the full model.

## Verification

- 15 offline unit tests in
  [`test/test_metadatarr_provider.py`](../test/test_metadatarr_provider.py):
  one per provider for happy-path dispatch, plus medium-mismatch
  rejections, no-title short-circuits, registration assertions, and
  relation-emission checks for the music-artist and book-author
  cases. Stub clients mirror metadatarr's surface so no network is needed.
- Live check
  [`examples/live/check_metadatarr.py`](../examples/live/check_metadatarr.py)
  hits all five endpoints; latest run:
  - `metadatarr_radarr` Inception → tmdb 27205
  - `metadatarr_skyhook` The Boys → tvdb 355567
  - `metadatarr_lidarr` Daft Punk → mbid `056e4f3e-d505-4dad-8ec1-d04f521cbb56`
  - `metadatarr_openlibrary` The Hobbit → OL27482W (Tolkien OL26320A)
  - `metadatarr_bookinfo` The Hobbit → goodreads `1540236`, isbn13 `9783423085595`
