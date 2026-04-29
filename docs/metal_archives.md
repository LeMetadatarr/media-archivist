# Encyclopaedia Metallum

A heavy-metal-only data source: indexes bands, releases and songs from
[`metal-archives.com`](https://www.metal-archives.com/) via
[`pymetal`](https://github.com/OpenJarbas/pymetal). It is both a
**backend** (rows you index directly) and a **provider** (enrichment
of music rows from other backends).

## Install

```bash
pip install media_archivist[metal_archives]   # pulls in pymetal
```

`pymetal` itself depends on `curl_cffi`, `lxml`, and `random-user-agent`
to navigate the site without TLS-fingerprint blockers.

## CLI — backend

```bash
# Index every song from a band's full discography by band name
media-archivist add --metal-archives --db-file metal.json "Mayhem"

# Index a specific release by id (URL or numeric)
media-archivist add --metal-archives --db-file metal.json \
    "https://www.metal-archives.com/albums/Mayhem/De_Mysteriis_Dom_Sathanas/434"

# Same filter & view surfaces as every other backend
media-archivist list --db-file metal.json --canonical \
    --where 'duration > 240 and "Black Metal" in tags'
```

Each archived row is a **song**: the playable unit on Encyclopaedia
Metallum. Band-level metadata (genres, country, status) and release
metadata (album, release_date, label) ride along on the row so a
single song can be rendered without re-fetching.

## Stored fields

`RawMetalArchivesEntry`:

| Field          | Source                              |
| -------------- | ----------------------------------- |
| `url`          | canonical MA URL `release.php?releaseID=...&songID=...` |
| `title`        | song title                          |
| `artist`       | band name                           |
| `album`        | release title                       |
| `band_id`      | MA band id (`ma_id`)                |
| `release_id`   | MA release id                       |
| `song_id`      | MA song id                          |
| `duration`     | seconds (parsed from `MM:SS`)       |
| `length`       | raw `MM:SS` as MA renders it        |
| `release_date` | release date string                 |
| `release_type` | full-length / ep / demo / live / …  |
| `country`      | band country                        |
| `genres`       | band genre tags                     |
| `themes`       | band lyrical themes                 |
| `label_id`     | MA label id                         |
| `label_name`   | label name on the release           |
| `cover_url`    | release cover                       |
| `band_url`     | MA band URL                         |

## Provider — enrichment

The `metal_archives` provider is registered automatically and enabled
whenever `pymetal` imports cleanly. It activates inside
`canonicalize`:

```bash
media-archivist canonicalize --db-file metal.json \
    --providers metal_archives
```

Outputs:

| Field on `ExternalIds`        | When populated |
| ----------------------------- | --- |
| `metal_archives_band`         | always for music matches |
| `metal_archives_release`      | when a release was identified |
| `metal_archives_song`         | when a specific song was identified |
| `metal_archives_label`        | when the release credits a label |

Entity relations populated:

- `EntityKind.ARTIST` — the band, with `metal_archives_band` on the
  entity record.
- `EntityKind.ALBUM` — the release, with `metal_archives_release`.
- `EntityKind.LABEL` — the release's label, with
  `metal_archives_label`.

Any provider returning `metal_archives_band` for an artist that
already has a record (under any other id) merges into the same
`entity_id`, because the dominant-external lookup picks MBID first
and MA band id second. To avoid splitting an entity across MB and MA
during a single canonicalize run, run MusicBrainz first or in the
same invocation:

```bash
media-archivist canonicalize --db-file metal.json \
    --providers musicbrainz --providers metal_archives
```

## Querying

The standard `--where` works against the canonical view:

```bash
# Every Norwegian black-metal song longer than 6 minutes
media-archivist list --db-file metal.json --canonical \
    --where '"Black Metal" in tags and duration > 360 and source=="metal_archives"'

# Tracks that have a Metal-Archives release id but no MBID
media-archivist list --db-file metal.json --canonical \
    --where 'external_ids.metal_archives_release != None and external_ids.musicbrainz_recording == None'

# Tracks on a specific label by id
media-archivist list --db-file metal.json --canonical \
    --where 'external_ids.metal_archives_label == 7'
```

## Verification

- 12 offline unit tests under
  [`test/test_metal_archives.py`](../test/test_metal_archives.py)
  cover length parsing, raw model round-trip, view adapter dispatch,
  provider registration, ExternalIds round-trip, entity-id collisions
  via `metal_archives_band` / `metal_archives_label`.
- Live check
  [`examples/live/check_metal_archives.py`](../examples/live/check_metal_archives.py)
  archives an album-level discography slice and verifies songs land in
  the DB. Currently passing: 9 song rows from *De Mysteriis Dom
  Sathanas*.
