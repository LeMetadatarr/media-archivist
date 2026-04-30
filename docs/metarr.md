# metarr-backed providers

`metarr` ships typed clients for the public catalogues media_archivist
otherwise hand-rolls HTTP calls against. Each metarr client is exposed
as a separate provider — same `name` pattern as the `arr_*` providers —
so users can pick exactly which catalogues to consult via
`--providers metarr_radarr,metarr_openlibrary,…`.

| Provider              | Backed by                                    | Media | External ids produced       |
| --------------------- | -------------------------------------------- | ----- | --------------------------- |
| `metarr_skyhook`      | `ArrMetadataClient.search_series` (skyhook.sonarr.tv) | TV    | `tvdb`, year                |
| `metarr_radarr`       | `ArrMetadataClient.search_movie` (radarrapi.servarr.com) | movie | `tmdb_movie`, year          |
| `metarr_lidarr`       | `ArrMetadataClient.search_artist` (api.lidarr.audio) | music | `musicbrainz_artist`, artist relation with mbid |
| `metarr_openlibrary`  | `OpenLibraryClient.search` (openlibrary.org) | book  | `olid`, `isbn_10`, `isbn_13`, `author` relations with OLIDs |
| `metarr_bookinfo`     | `BookInfoClient.search` (Goodreads / Hardcover proxy) | book  | `goodreads` (work), `extra.goodreads_book`, `isbn_13`, `author` relation with `extra.goodreads_author` |

None of these need configuration — no env vars, no API keys, no
self-hosted instances.

## Why split it five ways

Earlier the integration was a single `metarr` umbrella provider that
dispatched on `signals.medium`. Splitting matches:

- the existing `arr_*` pattern (Sonarr / Radarr / Readarr / Lidarr are
  separate providers), so `media-archivist providers` and
  `--providers …` show one-name-one-endpoint;
- per-source rate-limit budgets — skipping `metarr_bookinfo` for a
  music DB no longer drags every other metarr endpoint along;
- enrichment composability — `metarr_openlibrary` and `metarr_bookinfo`
  emit complementary book ids (OLID/ISBN vs Goodreads), so users can
  enable both for fuller cross-references or one for less network noise.

## Install

```bash
pip install /path/to/api_clients/metarr
# or, once on PyPI:
pip install media_archivist[metarr]
```

`metarr` itself depends on `requests`, `pydantic>=2`, `bs4` and `lxml`.

## Activation

All five providers self-register when `metarr` imports cleanly:

```bash
$ media-archivist providers
[
  {"name": "metarr_bookinfo",    "active": true, "media": ["book"]},
  {"name": "metarr_lidarr",      "active": true, "media": ["music"]},
  {"name": "metarr_openlibrary", "active": true, "media": ["book"]},
  {"name": "metarr_radarr",      "active": true, "media": ["movie"]},
  {"name": "metarr_skyhook",     "active": true, "media": ["tv"]},
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
    --providers metarr_radarr --providers tmdb

# OpenLibrary + Goodreads for fuller book cross-references
media-archivist canonicalize --db-file books.json \
    --providers metarr_openlibrary --providers metarr_bookinfo
```

Both metarr book providers emit `EntityKind.AUTHOR` relations; running
both populates entity records with both an `openlibrary_author` OLID
and a `goodreads_author` id under `extra`, deduplicated to one entity
when names match.

## Verification

- 15 offline unit tests in
  [`test/test_metarr_provider.py`](../test/test_metarr_provider.py):
  one per provider for happy-path dispatch, plus medium-mismatch
  rejections, no-title short-circuits, registration assertions, and
  relation-emission checks for the music-artist and book-author
  cases. Stub clients mirror metarr's surface so no network is needed.
- Live check
  [`examples/live/check_metarr.py`](../examples/live/check_metarr.py)
  hits all five endpoints; latest run:
  - `metarr_radarr` Inception → tmdb 27205
  - `metarr_skyhook` The Boys → tvdb 355567
  - `metarr_lidarr` Daft Punk → mbid `056e4f3e-d505-4dad-8ec1-d04f521cbb56`
  - `metarr_openlibrary` The Hobbit → OL27482W (Tolkien OL26320A)
  - `metarr_bookinfo` The Hobbit → goodreads `1540236`, isbn13 `9783423085595`
