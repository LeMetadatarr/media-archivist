# Servarr metadata proxies (`metarr`)

A "no-self-hosting-needed" provider that queries four public catalogues
in one shot — the same Servarr metadata proxies Sonarr / Radarr /
Lidarr hit internally, plus OpenLibrary for books:

| Proxy                          | Used for       | Returns         |
| ------------------------------ | -------------- | --------------- |
| `skyhook.sonarr.tv/v1`         | TV series      | `tvdb_id`, year |
| `radarrapi.servarr.com/v1`     | Movies         | `tmdb_id`, year |
| `api.lidarr.audio/api/v0.4`    | Music artists  | MusicBrainz id  |
| `openlibrary.org/search.json`  | Books          | OLID, ISBN, author OLIDs |

This is the "no self-hosting, no API key" sibling of the existing
`arr_*` providers. Those need a running Sonarr/Radarr/Lidarr instance;
this one is always available wherever
[`metarr`](https://github.com/JarbasAl/metarr) is installed.

## Install

```bash
pip install /path/to/api_clients/metarr
# or, once on PyPI:
pip install media_archivist[metarr]
```

`metarr` itself depends on `requests`, `pydantic>=2`, and `bs4`.

## Activation

The provider self-registers when `metarr` imports:

```bash
$ media-archivist providers
[
  {"name": "metarr", "active": true, "media": ["movie", "music", "tv"]},
  ...
]
```

There's nothing to configure — no env var, no URL, no key.

## What it produces

| Signal medium | External ids on the work             | Relations populated |
| ------------- | ------------------------------------ | --- |
| `movie`       | `tmdb_movie`                         | (none — proxy doesn't return cast) |
| `tv`          | `tvdb`                               | (none) |
| `music`       | `musicbrainz_artist` (work-level)    | `artist` (with MBID) |
| `book`        | `olid`, `isbn_13`, `isbn_10`         | `author` (with `extra.openlibrary_author` OLIDs) |
| `podcast` / `other` | (skipped)                      | — |

Confidence is 0.85 for film/TV (single-result fallback) and 0.75 for
music artist matches.

## Pairing with the heavy hitters

The proxy returns *less* than a full Sonarr / Radarr instance does —
it carries the canonical id and year, but no cast / crew / runtime /
country. Pair `metarr` with TMDB or `arr_radarr` to get full credits
on top of the canonical id:

```bash
# tmdb supplies cast/director/producer; metarr is the cheap id seed
media-archivist canonicalize --db-file films.json \
    --providers metarr --providers tmdb
```

In that combo, both providers report the same `tmdb_movie` for
matching films, the canonical record stays single, and TMDB's
relations populate the entity sidecar with cast/crew.

## Verification

- 8 offline unit tests in
  [`test/test_metarr_provider.py`](../test/test_metarr_provider.py):
  registration, no-title short-circuit, podcast skip,
  movie / tv / music / book dispatch, artist + author relations with
  ids, fallthrough when medium is unspecified.
- Live check
  [`examples/live/check_metarr.py`](../examples/live/check_metarr.py)
  hits all four endpoints; current run: PASS (Inception → tmdb 27205,
  The Boys → tvdb 355567, Daft Punk → mbid `056e4f3e-…`, The Hobbit →
  olid OL27482W with author OL26320A).
