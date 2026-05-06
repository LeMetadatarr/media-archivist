# media_archivist

Cross-source media indexer. Builds a local JSON database of stream metadata
from YouTube, YouTube Music, Internet Archive, Bandcamp and SoundCloud.

| Backend | Library | What you can index |
| --- | --- | --- |
| **YouTube** | [`tutubo`](https://github.com/OpenJarbas/tutubo) | channels, playlists, videos (no API key) |
| **YouTube Music** | `tutubo.ytmus` (via `ytmusicapi`) | tracks, albums, artists, playlists |
| **Internet Archive** | `internetarchive` | items, collections |
| **Bandcamp** | [`py_bandcamp`](https://github.com/JarbasAl/py_bandcamp) | tracks, albums, artists, tag/search |
| **SoundCloud** | [`nuvem_de_som`](https://github.com/JarbasAl/nuvem_de_som) | tracks, sets, profiles, search |

`media_archivist` is **metadata-only**: it indexes streams; it does not
download them. Pair it with [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (or
SoundCloud's `resolve_stream`, Bandcamp's `track.stream`) for on-demand
extraction, or use the JSON DB to drive dataset-collection scripts, recommender
experiments, OVOS skills, etc.

Ships as both a Python library and a `media-archivist` CLI.

## Install

```bash
pip install media_archivist                 # core (YouTube + IA + YT Music)
pip install media_archivist[bandcamp]       # + py_bandcamp
pip install media_archivist[soundcloud]     # + nuvem_de_som
pip install media_archivist[all]            # everything
```

## CLI

Every subcommand takes either:

- `--db-file PATH` — explicit path to a `.json` file (recommended for datasets
  you want to commit alongside scripts), **or**
- `--db NAME` — auto-place under XDG at `~/.local/share/media_archivist/<NAME>.json`.

```bash
# Index a channel, a playlist, or individual videos
media-archivist add --db-file talks.json https://www.youtube.com/@LinusTechTips
media-archivist add --db-file talks.json --blacklist "#shorts" \
    https://www.youtube.com/playlist?list=PL...

# Browse the DB
media-archivist list  --db-file talks.json --limit 20
media-archivist list  --db-file talks.json --grep "review" --json
media-archivist stats --db-file talks.json

# Pair with yt-dlp — index once, download on demand
media-archivist urls --db-file talks.json --grep "tutorial" | yt-dlp -a -

# Drop dead videos / unwanted titles
media-archivist prune --db-file talks.json --unavailable --blacklist sponsor

# Background-monitor a set of URLs (re-syncs every --interval seconds)
media-archivist monitor --db-file talks.json --interval 600 \
    https://www.youtube.com/@LinusTechTips \
    https://www.youtube.com/@SomeOtherChannel

# Internet Archive
media-archivist add --db-file ia_movies.json --ia classic_cartoons
media-archivist urls --db-file ia_movies.json | xargs -n1 -P4 wget

# YouTube Music — rich track metadata (artist, album, year, duration, explicit)
media-archivist add --db-file songs.json --music --skip-explicit "lo-fi beats"
media-archivist add --db-file songs.json --music \
    "https://music.youtube.com/playlist?list=PL..."

# Bandcamp — tracks have direct stream URLs in the entry
media-archivist add --db-file bandcamp.json --bandcamp \
    "https://artistname.bandcamp.com/album/some-album"
media-archivist add --db-file bandcamp.json --bandcamp "ambient drone"

# SoundCloud — search, profile, or set URLs
media-archivist add --db-file sc.json --soundcloud \
    "https://soundcloud.com/some-artist"
media-archivist add --db-file sc.json --soundcloud "footwork"
```

Pick the backend with `--ia`, `--music`, `--bandcamp`, or `--soundcloud`
(default: YouTube). Every other subcommand (`list`, `export`, `urls`, `prune`,
`merge`, `stats`, …) works the same way against any backend's DB.

DBs are plain JSON — edit, back up, version-control, share. With `--db NAME` the
file is managed under XDG via
[`json_database`](https://github.com/OpenJarbas/json_database).

## Building datasets

`media_archivist` is metadata-only: it indexes streams; downloads happen on
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
| `csv` | pandas, spreadsheets — list/dict fields auto-serialized to JSON strings |
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

# Channel — handles /channel/, /c/, /@handle, /user/
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
> plain channel scrapes. It **does** apply when length is available — i.e.
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
JSON dump — handy for distributing pre-built indexes.

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

- `remove_keyword(kwords)` — drop entries whose title matches any keyword
- `remove_missing(keys)` — drop entries missing any of the given fields
- `remove_below_duration(minutes)` — drop entries shorter than N minutes
- `sorted_entries()` — entries sorted by `upload_ts` (descending)

## Metadata providers — roadmap

`media-archivist canonicalize` enriches indexed entries with external IDs and
structured metadata. The table below tracks planned providers, their access
model, and implementation effort.

**Legend** — Access column:
- 🟢 **Free API** — public REST/GraphQL endpoint, no credentials required
- 🔑 **API key** — free registration, credentials passed via env var
- 🕷️ **Scraper** — no public API; HTML scraping required

### High priority (zero setup)

| Provider | Medium | Access | ID field(s) | Notes |
|---|---|---|---|---|
| **AniList** | Anime, Manga | 🟢 Free API (GraphQL) | `anilist_id` | Most active anime/manga DB; covers airing schedule, staff, studios |
| **Jikan** (MAL proxy) | Anime, Manga | 🟢 Free API (REST) | `mal_id` | Unofficial MyAnimeList REST proxy; largest raw catalogue |
| **Google Books** | Book, Audiobook | 🟢 Free API | `google_books_id` | ISBN resolution; fills edition gaps between OpenLibrary and Goodreads; 1,000 req/day without key |

### Medium priority (free key, one-time signup)

| Provider | Medium | Access | ID field(s) | Notes |
|---|---|---|---|---|
| **OpenCritic** | Game | 🔑 RapidAPI key | `opencritic_id` | Game search + review aggregator; requires RapidAPI subscription |
| **Trakt** | Movie, TV | 🔑 API key | `trakt_id` | ID cross-reference: resolves IMDb ↔ TMDB ↔ TVDB; key required for all endpoints |
| **RAWG** | Game | 🔑 API key | `rawg_id` | 500 k+ games, best general-purpose game catalogue |
| **Podcast Index** | Podcast, Audiodrama | 🔑 API key | `podcast_index_id` | Open data, 4 M+ feeds, episode GUIDs; key via podcastindex.org/developer |
| **Last.fm** | Music | 🔑 API key | — (supplements MusicBrainz) | Scrobble counts, tags, similar artists |

### Low priority (scraping required or restricted API)

| Provider | Medium | Access | ID field(s) | Notes |
|---|---|---|---|---|
| **AniDB** | Anime | 🕷️ Scraper / throttled API | `anidb_id` | Most precise release-level data (dub vs sub, specials, episodes); rate-limited without key |
| **Audible** | Audiobook | 🕷️ Scraper | `audible_asin` | No public API; Audnexus (above) is the preferred path |
| **IMDb** | Movie, TV | 🕷️ Scraper | `imdb` | Already used as an ID field; live querying requires scraping |
| **Letterboxd** | Movie | 🕷️ Scraper | — | No public API; community ratings only |
| **IGDB** | Game | 🔑 Twitch OAuth | `igdb_id` | Most authoritative game DB; OAuth setup cost |
| **Spotify** | Music, Podcast | 🔑 OAuth | — | Requires OAuth flow; audiobook/podcast coverage good but setup heavy |

### New mediums / entity kinds still needed

| Addition | Type | Required by |
|---|---|---|
| `MediaType.EPISODIC_SERIES + content_genres=["anime"]` | model | AniList, Jikan |
| `MediaType.COMIC + content_genres=["manga"]` | model | AniList, Jikan |
| `MediaType.GAME` | model | OpenCritic, RAWG, IGDB |
| `EntityKind.CHARACTER` | model | Anime/drama cast credits |
| `anilist_id`, `mal_id`, `anidb_id` | `ExternalIds` fields | Anime providers |
| `opencritic_id`, `rawg_id`, `igdb_id` | `ExternalIds` fields | Game providers |
| `google_books_id` | `ExternalIds` field | Google Books |
| `trakt_id` | `ExternalIds` field | Trakt |

## License

Apache-2.0
