# Servarr metadata proxies (`metarr`)

A "no-Arr-stack-needed" provider that queries the public Servarr
metadata proxies — the same endpoints Sonarr / Radarr / Lidarr hit
internally for show / movie / artist metadata:

| Proxy                          | Used for       | Returns         |
| ------------------------------ | -------------- | --------------- |
| `skyhook.sonarr.tv/v1`         | TV series      | `tvdb_id`, year |
| `radarrapi.servarr.com/v1`     | Movies         | `tmdb_id`, year |
| `api.lidarr.audio/api/v0.4`    | Music artists  | MusicBrainz id  |

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
| `book` / `podcast` / `other` | (skipped)                  | — |

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

- 7 offline unit tests in
  [`test/test_metarr_provider.py`](../test/test_metarr_provider.py):
  registration, no-title short-circuit, book skip, movie/tv/music
  dispatch, artist relation with MBID, fallthrough when medium is
  unspecified.
- Live check
  [`examples/live/check_metarr.py`](../examples/live/check_metarr.py)
  hits all three proxies; current run: PASS (Inception → tmdb 27205,
  The Boys → tvdb 355567, Daft Punk → mbid `056e4f3e-…`).
