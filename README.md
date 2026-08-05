# media_archivist

media_archivist adds remote media as **streams** to your library. It indexes
YouTube, YouTube Music, Internet Archive, Bandcamp and SoundCloud into a local
JSON database, exports that index as `.strm` / M3U / RSS for Jellyfin and
Kodi, and resolves a fresh playable URL for any entry on demand — all
**without downloading anything**. Downloading is optional and secondary: a
one-click/one-command action for the rows you actually want on disk.

| Backend | Library | What you can index |
| --- | --- | --- |
| **YouTube** | [`tutubo`](https://github.com/LeMetadatarr/tutubo) | channels, playlists, videos (no API key) |
| **YouTube Music** | `tutubo.ytmus` (via `ytmusicapi`) | tracks, albums, artists, playlists |
| **Internet Archive** | `internetarchive` | items, collections |
| **Bandcamp** | [`py_bandcamp`](https://github.com/LeMetadatarr/py_bandcamp) | tracks, albums, artists, tag/search |
| **SoundCloud** | [`nuvem_de_som`](https://github.com/LeMetadatarr/nuvem_de_som) | tracks, sets, profiles, search |

`media_archivist` does the **streams** job only — index, resolve, play,
optionally download. It does **not** tag an existing media library; that's
[`metadatarr`](https://github.com/LeMetadatarr/metadatarr)'s job
(`metadatarr tag-library`), the cross-source metadata resolver media_archivist
itself calls into for `canonicalize`.

Ships as a Python library, a `media-archivist` CLI, a JSON HTTP API, and a
build-free Web UI on top of that API.

## Install

```bash
pip install media_archivist              # core (YouTube + IA + YT Music)
pip install media_archivist py_bandcamp  # + Bandcamp
pip install media_archivist nuvem_de_som # + SoundCloud
pip install media_archivist[all]         # + huggingface_hub, fastapi, uvicorn (hub publishing, HTTP service)
```

## Web UI

A build-free [htmx](https://htmx.org) Web UI ships with the server, no
Node, no JS build step. It gives you a dashboard, a paginated library browser
with an inline player, one-click archiving and downloading with live
progress, and a dedup quarantine queue with bulk accept/reject, all served
alongside the existing JSON API.

![Dashboard](docs/img/dashboard.png)

```bash
pip install "media-archivist[server]"
media-archivist serve --db-file talks.json
# open http://localhost:8000/
```

Or via Docker (already documented in [`docs/deploy.md`](docs/deploy.md)):

```bash
docker compose -f deploy/docker-compose.yml up
```

A quick tour of the pages:

- **Library** — filter by source, free-text grep, or the `where` DSL, with
  yes/no stream badges at a glance and paginated results ("Showing 1–50 of
  60" + Prev/Next). The `where` filter is sandboxed: it parses your
  expression with `ast` and walks a small allow-list of comparisons/booleans,
  it never calls Python's `eval`.
  ![Library](docs/img/library.png)
- **Entry detail** — click a row to open the detail drawer: an inline player
  (native `<audio>`/`<video>` when a direct stream URL is known, a lazy
  `youtube-nocookie` embed or a "▶ Play (yt-dlp)" button otherwise), an
  "↻ refresh stream" action for stale URLs, "Open original", full metadata,
  and, when `yt-dlp` is available, a "⬇ Download" button.
  ![Entry detail](docs/img/entry-detail.png)
- **Archive** — kick off a new archive job and watch it progress live, no
  page refresh.
  ![Archive](docs/img/archive.png)
- **Quarantine** — review dedup conflicts the resolver couldn't confidently
  match. Select multiple rows and "Accept selected" / "Reject selected" in
  one action, or decide row by row.
  ![Quarantine](docs/img/quarantine.png)
- **Subscriptions** — remember a channel/playlist/collection URL and "Sync
  now" re-archives every one of them; the backend's `archive()` dedupes on
  its own, so only genuinely new uploads land in the library.
- **Providers** — see which metadatarr providers are active at a glance.
  ![Providers](docs/img/providers.png)

It's responsive and themeable (dark/light), the same UI works fine on a
phone from your homelab network:

![Mobile](docs/img/mobile.png)

Like the rest of the HTTP service, the Web UI ships with **no built-in
authentication**. It's single-tenant, LAN-only by design, put it behind a
reverse proxy if you expose it beyond your local network, see
[`docs/deploy.md`](docs/deploy.md). Full page-by-page tour:
[`docs/webui.md`](docs/webui.md).

## Playback: `.strm` + a play-time yt-dlp hook

The point of streaming instead of downloading is that `media-archivist serve`
can hand Jellyfin/Kodi a URL that **resolves at play time**, not at
export time — so it survives YouTube CDN URLs expiring. Export `.strm` files
whose body is `<base_url>/strm/<id>?resolve=1` (or set
`MEDIA_ARCHIVIST_STRM_RESOLVE=1` on the server so plain `<base_url>/strm/<id>`
bodies resolve too):

```bash
media-archivist strm-export --db-file talks.json \
    --output-dir /var/lib/jellyfin/media/archivist \
    --base-url http://nas.local:8000
```

When Jellyfin/ffmpeg opens that URL at playback time, media-archivist
re-resolves a fresh direct media URL via yt-dlp and replies with a **302
redirect** to it, no downloading, no Jellyfin plugin needed. Add
`?mode=proxy` (or `MEDIA_ARCHIVIST_STRM_PROXY=1`) to have media-archivist
stream the bytes through itself instead of redirecting, for players that
can't follow redirects or reach the CDN directly. Resolution is
**source-aware**: Bandcamp and SoundCloud resolve via their own archivist
libs (no `yt-dlp` needed for those), YouTube (and anything else) goes through
`yt-dlp`. Full recipe, `.nfo` sidecars, and library layouts:
[`docs/jellyfin.md`](docs/jellyfin.md).

## Optional download

Streaming is the default; downloading a specific entry to disk is one
scheduler-backed action away — the "⬇ Download" button in the entry detail
drawer, or:

```bash
curl -X POST http://localhost:8000/entries/<id>/download
```

Files land under `MEDIA_ARCHIVIST_DOWNLOAD_DIR`, progress is tracked the same
way as archive jobs (poll `GET /tasks/{task_id}`), and the button/endpoint is
only offered when `yt-dlp` is actually available on the server.

## CLI

Every subcommand takes either:

- `--db-file PATH`, explicit path to a `.json` file (recommended for datasets
  you want to commit alongside scripts), **or**
- `--db NAME`, auto-place under XDG at `~/.local/share/media_archivist/<NAME>.json`.

```bash
# Index a channel, a playlist, or individual videos
media-archivist add --db-file talks.json https://www.youtube.com/@LinusTechTips
media-archivist add --db-file talks.json --blacklist "#shorts" \
    https://www.youtube.com/playlist?list=PL...

# Browse the DB
media-archivist list  --db-file talks.json --limit 20
media-archivist list  --db-file talks.json --grep "review" --json
media-archivist stats --db-file talks.json

# Pair with yt-dlp, index once, download on demand
media-archivist urls --db-file talks.json --grep "tutorial" | yt-dlp -a -

# Drop dead videos / unwanted titles
media-archivist prune --db-file talks.json --unavailable --blacklist sponsor

# Background-monitor a set of URLs (re-syncs every --interval seconds)
media-archivist monitor --db-file talks.json --interval 600 \
    https://www.youtube.com/@LinusTechTips \
    https://www.youtube.com/@SomeOtherChannel

# Subscriptions: remember a channel/playlist/collection so new uploads keep
# getting auto-indexed, without re-typing every URL each run (pairs with cron)
media-archivist subscribe --db-file talks.json https://www.youtube.com/@LinusTechTips
media-archivist subscribe --db-file talks.json --backend ia --label "Cartoons" \
    https://archive.org/details/classic_cartoons
media-archivist subscriptions --db-file talks.json
media-archivist sync-subscriptions --db-file talks.json   # archive() dedupes; only new uploads land
media-archivist unsubscribe --db-file talks.json https://www.youtube.com/@LinusTechTips

# Internet Archive
media-archivist add --db-file ia_movies.json --ia classic_cartoons
media-archivist urls --db-file ia_movies.json | xargs -n1 -P4 wget

# YouTube Music, rich track metadata (artist, album, year, duration, explicit)
media-archivist add --db-file songs.json --music --skip-explicit "lo-fi beats"
media-archivist add --db-file songs.json --music \
    "https://music.youtube.com/playlist?list=PL..."

# Bandcamp, tracks have direct stream URLs in the entry
media-archivist add --db-file bandcamp.json --bandcamp \
    "https://artistname.bandcamp.com/album/some-album"
media-archivist add --db-file bandcamp.json --bandcamp "ambient drone"

# SoundCloud, search, profile, or set URLs
media-archivist add --db-file sc.json --soundcloud \
    "https://soundcloud.com/some-artist"
media-archivist add --db-file sc.json --soundcloud "footwork"
```

Pick the backend with `--ia`, `--music`, `--bandcamp`, or `--soundcloud`
(default: YouTube). Every other subcommand (`list`, `export`, `urls`, `prune`,
`merge`, `stats`, …) works the same way against any backend's DB.

DBs are plain JSON, edit, back up, version-control, share. With `--db NAME` the
file is managed under XDG via
[`json_database`](https://github.com/TigreGotico/json_database).

## Homelab / HTTP service

`media-archivist serve` exposes a FastAPI HTTP API on port 8000. The Docker
image includes `yt-dlp` and stores everything under `/data`.

```bash
# One command brings up the service with a persistent named volume,
# automatic restart-on-reboot, and a /healthz healthcheck.
docker compose -f deploy/docker-compose.yml up -d
```

The service is **single-tenant, no authentication**. It is designed to run
on your LAN or behind your existing reverse proxy (Caddy, Traefik, nginx).
Do not expose port 8000 directly to the internet.

### Integration endpoints

| Endpoint | Purpose |
| -------- | ------- |
| `GET /strm/{id}` | Playable URL for `.strm` files. Plain `text/plain` by default; `?resolve=1` 302-redirects to a freshly yt-dlp-resolved stream at play time (`?mode=proxy` to proxy the bytes instead). |
| `GET /m3u` | M3U playlist of stream URLs. Accepts `source`, `where`, `has_stream`, `limit`. |
| `GET /feed.rss` | RSS feed for podcast clients or Freshrss. Accepts `limit`. |
| `GET /healthz` | Liveness check for Uptime Kuma, Docker, k8s. Returns `{status, version, db_path}`. |
| `GET /providers` | Inspect which metadatarr providers are active (`available`, `media`, `modality`, `genre_filter`). |
| `POST /canonicalize` | Run the resolver against the DB. Body: `{providers?, stamp_rows?, max_workers?}`. |
| `GET /quarantine` | List entries the resolver could not confidently match. |
| `POST /quarantine/{id}/accept` | Accept a quarantined row (optional `?canonical_id=` to link). |
| `POST /quarantine/{id}/reject` | Reject and force a fresh canonical_id. |
| `POST /entries/{id}/download` | Optional: enqueue a `yt-dlp` download of one entry to `MEDIA_ARCHIVIST_DOWNLOAD_DIR`. `503` if `yt-dlp` isn't available. |
| `GET /docs` | Auto-generated OpenAPI / Swagger UI. |

See [`docs/deploy.md`](docs/deploy.md) for the full route table, Systemd
unit, and reverse-proxy tips. For Jellyfin `.strm` export see
[`docs/jellyfin.md`](docs/jellyfin.md).

## Building datasets

`media_archivist` is metadata-only: it indexes streams. Downloads happen on
demand via `yt-dlp` (or any other tool that reads URLs). The `export`,
`import`, `merge`, and `stats` subcommands turn the JSON DB into a workable
dataset.

```bash
# Build an index of three channels into one explicit file
media-archivist add --db-file documentaries.json \
    https://www.youtube.com/@FreeDocumentary \
    https://www.youtube.com/@FDSpace \
    https://www.youtube.com/@FreeDocumentaryOcean

# Project specific fields → CSV (great for pandas / sklearn)
media-archivist export --db-file documentaries.json --format csv \
    --fields videoId,title,url,published,tags,description \
    -o documentaries.csv

# JSONL is the canonical "one-row-per-line" format for ML pipelines
media-archivist export --db-file documentaries.json --format jsonl \
    -o documentaries.jsonl

# Just URLs (txt) for downstream tools
media-archivist export --db-file documentaries.json --format txt \
    -o urls.txt

# Inspect coverage before training
media-archivist stats --db-file documentaries.json

# Merge per-topic indexes into a master dataset
media-archivist merge --db-file all_docs.json \
    space.json ocean.json nature.json --overwrite

# Round-trip: import an existing JSONL produced elsewhere
media-archivist import --db-file talks.json talks.jsonl --overwrite
```

### Output formats

| `--format` | Use case |
| --- | --- |
| `jsonl` *(default)* | streaming pipelines, HuggingFace `datasets`, `jq` |
| `json` | small datasets, human inspection |
| `csv` | pandas, spreadsheets, list/dict fields auto-serialized to JSON strings |
| `txt` | flat URL list for `yt-dlp -a -` / `wget -i` / `xargs` |

Combine with `--fields` to project only what you need, `--grep` to filter by
title substring, and `--limit N` to cap row count.

### Stored fields per video

| field | source |
| --- | --- |
| `videoId`, `url`, `title`, `thumbnail` | tutubo `Video` |
| `tags` | union of `Video.keywords` and inferred `Video.tags` |
| `is_live`, `published`, `views`, `description` | tutubo channel-grid metadata |
| `playlist` | only set when archived from a playlist |

See [`examples/`](./examples) for end-to-end dataset-creation scripts.

## YouTube (library)

```python
from media_archivist import YoutubeArchivist

archivist = YoutubeArchivist(
    db_path="./talks.json",       # explicit file (or use db_name="..." for XDG)
    blacklisted_kwords=["#shorts", "trailer"],
    required_kwords=[],           # all must appear in the title
)

# Channel, handles /channel/, /c/, /@handle, /user/
archivist.archive("https://www.youtube.com/@LinusTechTips")

# Playlist
archivist.archive("https://www.youtube.com/playlist?list=PL...")

# Single video (watch / youtu.be / shorts URLs)
archivist.archive("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

# All playlists of a channel
archivist.archive_channel_playlists("https://www.youtube.com/@LinusTechTips")

# Drop entries whose videos are no longer reachable
archivist.remove_unavailable()

for entry in archivist.sorted_entries():
    print(entry["title"], entry["url"])
```

> **Note on duration:** tutubo's bare `Channel.videos` / `Playlist.videos`
> iterators don't expose track length, so `--min-duration` is a no-op for
> plain channel scrapes. It **does** apply when length is available, i.e.
> with `--music` (YT Music tracks), `--bandcamp`, `--soundcloud`, `--ia`,
> and YouTube search-result previews. `published` is a relative string
> ("2 days ago") rather than a timestamp.

### Background monitor

```python
from media_archivist import YoutubeMonitor

mon = YoutubeMonitor(db_name="my_channels")
mon.start()
mon.monitor("https://www.youtube.com/@LinusTechTips")  # re-syncs every sync_interval
mon.sync("https://www.youtube.com/@SomeOtherChannel")  # one-shot
```

`YoutubeMonitor.bootstrap_from_url(url)` seeds an empty database from a remote
JSON dump, handy for distributing pre-built indexes.

## YouTube Music (library)

```python
from media_archivist import YoutubeMusicArchivist

m = YoutubeMusicArchivist(db_path="./songs.json", skip_explicit=True)
m.archive_search("lo-fi beats")
m.archive_playlist("https://music.youtube.com/playlist?list=PL...")
m.archive_album("MPREb_xxx")          # browseId
m.archive_artist("UCxxx")             # channelId
```

Each entry includes `artist`, `album`, `year`, `duration` (seconds), `explicit`,
`video_type` (`MUSIC_VIDEO_TYPE_ATV` etc.), `audio_only`, `music_video`.

## Bandcamp (library)

```python
from media_archivist import BandcampArchivist

bc = BandcampArchivist(db_path="./bandcamp.json")
bc.archive("https://artist.bandcamp.com/album/some-album")
bc.archive_artist("https://artist.bandcamp.com")
bc.archive_search("ambient drone")
```

Each entry stores `artist`, `album`, `track_number`, `duration` (seconds),
`thumbnail`, and **`stream`** (a direct audio URL when Bandcamp exposes one).

## SoundCloud (library)

```python
from media_archivist import SoundCloudArchivist

sc = SoundCloudArchivist(db_path="./sc.json", resolve_streams=True)
sc.archive("https://soundcloud.com/some-artist")     # profile
sc.archive("https://soundcloud.com/some-artist/sets/some-set")  # set
sc.archive_search("footwork")
```

`resolve_streams=True` calls `nuvem_de_som`'s stream resolver per track and
stores the resulting MP3/HLS URL under `stream`.

## Internet Archive (library)

```python
from media_archivist import IAArchivist

ia = IAArchivist(db_path="./ia_movies.json")
ia.archive("classic_cartoons")           # collection or single item id
ia.archive_item("Popeye_forPresident")
```

Stream URLs are filtered to formats in `IAArchivist.VALID_FORMATS`
(`MPEG2`, `Ogg Video`, `512Kb MPEG4`, `h.264`).

## Filtering helpers

All archivists inherit from `JsonArchivist`:

- `remove_keyword(kwords)`, drop entries whose title matches any keyword
- `remove_missing(keys)`, drop entries missing any of the given fields
- `remove_below_duration(minutes)`, drop entries shorter than N minutes
- `sorted_entries()`, entries sorted by `upload_ts` (descending)

## Metadata providers

`media-archivist canonicalize` enriches indexed entries with external IDs
and structured metadata via the cross-source resolver in
[`metadatarr`](https://github.com/LeMetadatarr/metadatarr). The provider
registry, dispatcher, and ~24 built-in providers (MusicBrainz, Wikidata,
TMDB, AniList, Jikan, Google Books, LibriVox, Apple Podcasts, *arr family,
Discogs, Blu-ray.com, DVDCompare, OpenLibrary, Anna's Archive, Bandcamp,
SoundCloud, YouTube / YouTube Music, Metal Archives, …) all live in
metadatarr and self-register on import. See
[`docs/metadatarr.md`](docs/metadatarr.md) for the full table.

The resolver gates providers on three independent axes: `media` (MediaType),
`modality` (PlaybackModality, AUDIO / VIDEO / TEXT / INTERACTIVE / UNKNOWN),
and `genre_filter` (genre tag set). Callers constructing `Signals` directly can
pass `modality=PlaybackModality.AUDIO` to restrict resolution to audio-only
providers. See [`docs/metadatarr.md`](docs/metadatarr.md#routing) for details.

## Related projects

- [`metadatarr`](https://github.com/LeMetadatarr/metadatarr), the cross-source
  metadata resolver used by `canonicalize`
- [`tutubo`](https://github.com/LeMetadatarr/tutubo), the YouTube / YouTube
  Music client backing the YouTube backends
- [`py_bandcamp`](https://github.com/LeMetadatarr/py_bandcamp), the Bandcamp
  client backing the Bandcamp backend
- [`nuvem_de_som`](https://github.com/LeMetadatarr/nuvem_de_som), the
  SoundCloud client backing the SoundCloud backend

## License

Apache-2.0
