# Jellyfin / Kodi remote-media integration

Two-step recipe: run `media-archivist serve` somewhere reachable
(NAS, homelab box) and export `.strm` files into a directory Jellyfin
or Kodi treats as a library. The actual stream stays remote, your
Jellyfin server doesn't download or transcode anything.

## Why it works

`.strm` files are one-line text files whose body is a URL. Jellyfin
and Kodi follow that URL straight through to the player. Two flavours
of body work:

| Body                                   | When to use |
| -------------------------------------- | ----------- |
| Direct stream URL (Bandcamp / SoundCloud / IA) | Source already exposes a public MP3 / MP4, embedded directly. |
| `<base_url>/strm/<entry_id>`           | Indirect via `media-archivist serve`. The server resolves at request time and returns the current URL. Useful for sources whose URLs expire (YouTube). |

## End-to-end

```bash
# 1. Run the server somewhere routable
media-archivist serve --db-file /srv/media-archivist/index.json \
    --host 0.0.0.0 --port 8000

# 2. Export .strm files into a Jellyfin-watched directory
media-archivist strm-export \
    --db-file /srv/media-archivist/index.json \
    --output-dir /var/lib/jellyfin/media/archivist \
    --base-url http://nas.local:8000

# 3. Tell Jellyfin to scan the directory (it auto-detects .strm)
```

Output layout:

```
/var/lib/jellyfin/media/archivist/
├── bandcamp/
│   └── Aphex Twin/
│       ├── Avril 14th.strm
│       └── Xtal.strm
├── soundcloud/
│   └── ...
└── youtube/
    └── ...
```

Each `.strm` body is `http://nas.local:8000/strm/<entry_id>`, Jellyfin calls into the server when the user hits play, and the
server returns the resolved URL with `text/plain` content type.

## Recommended: play-time resolution with `?resolve=1`

Source URLs (YouTube especially) expire; the raw watch/listing URL still
resolves as an *entry*, but stops pointing at a directly playable file.
`/strm/{id}?resolve=1` turns the endpoint into a play-time resolution hook
instead of a static URL lookup.

Resolution is source-aware: Bandcamp and SoundCloud entries resolve via
their own archivist libs (`py_bandcamp` / `nuvem_de_som` — the same ones
that index them), so playback works for those even without `yt-dlp`
installed. Internet Archive entries are already direct download URLs, so
resolution is a no-op. YouTube and anything else fall back to `yt-dlp`
(either the `yt_dlp` Python package or the `yt-dlp` binary on `PATH`). If
a native lib is missing or fails, resolution falls back to yt-dlp rather
than failing outright.

`strm-export --base-url` writes each `.strm` body as
`<base_url>/strm/<entry_id>` (no query string). To get resolve-on-play
behaviour, set `MEDIA_ARCHIVIST_STRM_RESOLVE=1` on the server so every
`/strm/{id}` request resolves, regardless of the `.strm` body's query
string:

```bash
media-archivist strm-export \
    --db-file /srv/media-archivist/index.json \
    --output-dir /var/lib/jellyfin/media/archivist \
    --base-url "http://nas.local:8000"

# on the server:
MEDIA_ARCHIVIST_STRM_RESOLVE=1 media-archivist serve \
    --db-file /srv/media-archivist/index.json --host 0.0.0.0 --port 8000
```

(If you'd rather opt in per-entry instead of server-wide, hand-edit
the exported `.strm` files to append `?resolve=1` to each body — there
is currently no `strm-export` flag that does this for you.)

Jellyfin only *reads* the `.strm` body at library-scan time — it hands
that URL to ffmpeg at **play** time. Since the body is our endpoint,
ffmpeg opens `/strm/<id>?resolve=1` itself when the user hits play, and
the server resolves a fresh direct URL via yt-dlp right then and
**302-redirects** ffmpeg to it. ffmpeg follows redirects natively, so
this "just works" — no Jellyfin plugin, no baked-in URL to go stale,
and no downloading or transcoding on the media-archivist side. Every
playback re-resolves, so expired YouTube CDN URLs are a non-issue: the
`.strm` file never needs to be regenerated.

```bash
curl -I "http://nas.local:8000/strm/<entry_id>?resolve=1"
# HTTP/1.1 302 Found
# location: https://rr---sn-....googlevideo.com/videoplayback?...
```

`HEAD` is supported too (some players probe with `HEAD` before
`GET`), and returns the same redirect.

To make resolution the default for every `.strm` request without
`?resolve=1` on each `.strm` body — useful when you don't control how
the `.strm` files were exported — set
`MEDIA_ARCHIVIST_STRM_RESOLVE=1` in the server's environment;
`?resolve=0` still overrides it off per-request.

Resolution never breaks the `.strm` contract: if yt-dlp is unavailable
or fails to resolve (private/deleted video, network hiccup, ...), the
endpoint still returns a redirect — to the stored `stream`/`url`
instead of the freshly resolved one — and logs a warning. It never
returns an error status, since Jellyfin/Kodi need a usable response or
the item breaks in the library. Without `resolve` at all, `/strm/{id}`
keeps returning the classic `text/plain` body unchanged.

### Byte-proxy mode: `?mode=proxy`

Some players don't follow redirects, or the host running the player
can't reach the resolved CDN directly (e.g. it's on a network that
only routes through the NAS). Add `mode=proxy` (or set
`MEDIA_ARCHIVIST_STRM_PROXY=1` server-wide) and media-archivist fetches
the resolved URL itself and streams the bytes back, instead of
redirecting:

```bash
curl "http://nas.local:8000/strm/<entry_id>?resolve=1&mode=proxy" -o out.mp4
```

The client's `Range` header is forwarded upstream for seeking, and the
upstream `Content-Type` / `Content-Range` / `Accept-Ranges` /
`Content-Length` / status (`206` for a range request) are echoed back.
This is the belt-and-suspenders path — every played byte flows through
the media-archivist process, so prefer plain `?resolve=1` (redirect)
where the player can reach the CDN directly; it's lighter and the CDN
serves the bytes instead of your homelab box. Proxy mode also never
hard-fails: if the upstream fetch errors, it falls back to a redirect
and logs a warning.

The same resolver powers a "▶ Play (yt-dlp)" button in the WebUI's
entry detail drawer (and a "↻ refresh stream" affordance for entries
whose stored `stream` URL may have gone stale), shown whenever yt-dlp
is available.

## Without a running server

Skip `--base-url` to bake the resolved stream / watch URL straight
into each `.strm`:

```bash
media-archivist strm-export \
    --db-file ./music.json \
    --output-dir ./jellyfin-library \
    --has-stream
```

`--has-stream` keeps only entries whose direct stream URL is known
(Bandcamp, SoundCloud, Internet Archive). Useful for offline-friendly
libraries.

## Filters

The same filters as the canonical view apply:

```bash
media-archivist strm-export --db-file songs.json \
    --output-dir ./library \
    --base-url http://nas.local:8000 \
    --where 'duration > 120 and not explicit' \
    --source bandcamp
```

`--dry-run` prints the count without writing anything.

## Real titles, artwork and tags with `--nfo`

Without a sidecar, Jellyfin has to guess metadata from the filename.
Add `--nfo` and `strm-export` writes a `.nfo` XML file next to every
`.strm`, same basename, so Jellyfin's local-metadata reader picks up
the real title, artist/album, genre tags, runtime, release date and a
`<thumb>` URL straight from the index — no Jellyfin-side internet
metadata lookup needed, and no images are downloaded by
`media-archivist` itself; the `.nfo` just references the thumbnail
URL and lets Jellyfin fetch it.

```bash
media-archivist strm-export --db-file songs.json \
    --output-dir /var/lib/jellyfin/media/archivist \
    --base-url http://nas.local:8000 \
    --nfo
```

Music sources (`bandcamp`, `soundcloud`, `youtube_music`) get a
`<musicvideo>` NFO with `<artist>`/`<album>`; everything else
(`youtube`, `internet_archive`) gets a `<movie>` NFO with `<studio>`
and, where known, a `<uniqueid>`.

```
/var/lib/jellyfin/media/archivist/bandcamp/Aphex Twin/
├── Avril 14th.strm
└── Avril 14th.nfo
```

After adding files, point Jellyfin's library scan at the folder (or
wait for its watcher) — it re-reads sidecar NFOs on every scan, so
re-running `strm-export --nfo` after an archive refresh keeps the
library current.

## Library layout with `--layout`

`--layout` controls how `strm-export` groups files under
`--output-dir`:

| Value              | Path                                      | Notes |
| ------------------ | ------------------------------------------ | ----- |
| `by-source-artist` | `<output_dir>/<source>/<artist>/<title>.strm` | **Default.** Matches the original, pre-`--layout` output. |
| `flat`             | `<output_dir>/<title>.strm`                 | Everything in one folder — simplest to point a single Jellyfin library at. |
| `by-source`        | `<output_dir>/<source>/<title>.strm`        | One folder per archive backend, no artist nesting. |
| `by-artist`        | `<output_dir>/<artist or 'Unknown'>/<title>.strm` | Best for music-only exports where you want a classic artist-folder library. |

Filenames are sanitized (illegal/path characters stripped) and
deduplicated automatically — if two entries collapse to the same
sanitized name under the same folder, the later one gets a `-2`,
`-3`, ... suffix so nothing is silently overwritten.

```bash
media-archivist strm-export --db-file songs.json \
    --output-dir /var/lib/jellyfin/media/archivist \
    --nfo --layout by-artist --source bandcamp
```

## Collections — one URL per smart playlist

`strm-export`/`--where` filters an ad-hoc slice each time you run it. A
**collection** remembers the filter under a name, so it becomes a
browsable, always-live "smart playlist" — e.g. "Blender open movies" =
`source == 'youtube' and grep == 'blender'`. Once saved, the collection
gets a stable URL Jellyfin/Kodi/VLC can subscribe to directly:

```bash
media-archivist collection-add "Blender open movies" \
    --db-file /srv/media-archivist/index.json \
    --source youtube --grep blender \
    --description "CC-BY-licensed Blender Foundation films"

media-archivist collections --db-file /srv/media-archivist/index.json
# Blender open movies — CC-BY-licensed Blender Foundation films   matches=6
```

With the server running, `GET /collections/<name>/m3u` streams the
collection as an `#EXTM3U` playlist (`audio/x-mpegurl`) built from the
saved filter re-run against the *current* DB — point a player at
`http://nas.local:8000/collections/Blender%20open%20movies/m3u` and new
matches show up automatically as the library grows. The `/ui/collections`
page in the WebUI has an add-collection form and a "copy M3U link" button
per row.

To materialize a collection as `.strm` files (same layout/`--nfo` options
as `strm-export`) plus an optional `.m3u` file on disk:

```bash
media-archivist collection-export "Blender open movies" \
    --db-file /srv/media-archivist/index.json \
    --output-dir /var/lib/jellyfin/media/archivist/collections \
    --base-url http://nas.local:8000 --m3u
```

## Subtitles

`media-archivist` already pulls YouTube subtitle text into the DB via
`enrich --transcripts` (see [datasets.md](datasets.md)). `subtitles` turns
the same `yt-dlp` machinery into on-disk `.srt`/`.vtt` sidecar files,
so Jellyfin and Kodi show captions during playback:

```bash
media-archivist subtitles \
    --db-file ./talks.json \
    --output-dir ./jellyfin-library \
    --lang en,es
```

Sidecars use the same `--layout` and filename logic as `strm-export`
(default `by-source-artist`), so a subtitle file lands right next to
its matching `.strm`:

```
jellyfin-library/youtube/Some Channel/
├── A Talk.strm
├── A Talk.en.srt
└── A Talk.es.srt
```

`--lang` accepts a comma-separated list (default `en`); `--no-auto`
skips YouTube's auto-generated captions and only fetches manually
authored subtitle tracks; `--format srt` writes `.srt` instead of the
default `.vtt`. `--where`/`--source`/`--limit`/`--dry-run` behave like
every other subcommand. A video with no subtitles is reported as
`none`, not an error; if `yt-dlp` isn't installed the whole run is
reported as `skipped` rather than failing.

The WebUI's entry detail drawer also has a "💬 Fetch subtitles" button
(and a matching `POST /entries/{id}/subtitles` API endpoint) that
fetches on demand for a single entry, writing into the server's
download directory, whenever `yt-dlp` is available.

## Exports through the app API

A running server exports `.strm` files on request: one video, one playlist
or everything a subscription lists, into a folder Jellyfin watches.

```bash
curl -X POST http://nas.local:8000/api/v1/exports \
  -H 'Content-Type: application/json' \
  -d '{"scope": "playlist", "id": "youtube:playlist:PLyyyy",
       "target_dir": "/media/streams/youtube", "layout": "movies",
       "links": "signed", "refresh_jellyfin": true, "collection": true}'
```

The answer lists every file written and the export's id. Exporting the same
entry again into the same folder rewrites its files in place.
`DELETE /api/v1/exports/{id}` removes the files that export wrote and
nothing else: a file that was changed since, or that another export also
wrote, stays, and so does a folder that is not empty. The full contract is
in the app API reference, `docs/app-api.md`.

The `movies` layout writes `Title (Year) [videoid]/Title (Year) [videoid].strm`
with `movie.nfo` and the video's thumbnail as the poster, for a Movies
library. The `episodes` layout files each channel as a show and each upload
year as a season, `Channel/Season 2024/Channel - S2024E0503 - Title [videoid].strm`,
with `tvshow.nfo`, `season.nfo` and an episode `.nfo`, for a Shows library.
A video whose upload date is not known yet is left out of the `episodes`
layout and reported as skipped, and so is one whose episode `.strm` or `.nfo`
is already there with other content that no export wrote.

The server writes only below the folders listed, comma-separated, in
`MEDIA_ARCHIVIST_LIBRARY_ROOTS`. A target outside them, or one that leads
out of them through a symbolic link, is refused, and with no roots set
nothing is exported. No file is written through a symbolic link.

Each `.strm` file points at `/api/v1/stream/{id}` on the server, which
resolves a fresh stream on every play and redirects to it. When it cannot
for a reason of the video or the source, it plays a short status card that
says why, instead of failing or sending the player to a web page. A play is a request a person waits on, so
it takes no turn in the request spacing that holds back the indexer and the
downloads, and they do not hold it back. At most four plays resolve at once;
a fifth waits up to three seconds for a slot and is then answered `503` with
`Retry-After`, and when eight plays already wait it is answered `503` at
once, so waiting plays cannot take the server's worker threads. YouTube Music plays
are held to the same pause and bot check bookkeeping as YouTube plays. A resolved
address is kept for five minutes and plays of one entry share one
resolution, so a Jellyfin scan that probes one `.strm` file many times makes
one YouTube request, and the server's other routes stay as fast as they were.
`MEDIA_ARCHIVIST_PUBLIC_URL` sets the address written into the
files (for example `http://nas.local:8000`); without it the address the
export request arrived on is used. A subscription that exports
automatically has no request to take an address from, so it needs that
variable. Automatic export writes every entry a listing adds to the
subscription. For a subscription created with it switched on, the first
listing adds the whole back catalogue, so a channel of 2,000 videos becomes
2,000 `.strm` files, and fetching their posters takes one spaced request each,
about five and a half hours at the default spacing. Turn it on after the first
listing to export only what comes later.

`"links": "signed"` adds a signature to each link, so a player outside the
networks exempt from the API key plays that one video without the key, and
the key never lands in a file. The signature opens `/api/v1/stream/{id}`
for that entry and no other route. The signing secret is
`MEDIA_ARCHIVIST_LINK_SECRET`; without it the server generates one and
keeps it in its index. Changing the secret revokes every signed link, and
the files are then exported again. `POST /api/v1/entries/{id}/link` hands
out one signed link, with `{"expires_in": seconds}` for one that expires.

With `MEDIA_ARCHIVIST_JELLYFIN_URL` and `MEDIA_ARCHIVIST_JELLYFIN_API_KEY`
set, `refresh_jellyfin` asks Jellyfin to scan its libraries once the posters
are on disk, and `collection` on a playlist export makes a Jellyfin
collection named like the playlist, holding exactly its exported videos, or
updates the one made before. The server waits up to ten minutes for
Jellyfin to scan the new items and finds them by their YouTube id, so
Jellyfin may see the library under another path. A collection the server
did not make is never changed, and none is made when its name is taken.
`GET /api/v1/exports/{id}` reports what happened.

### Status cards

Four cards exist, each ten seconds of video served from the package:

| Card | Shown when |
|---|---|
| `rate-limited` | the source's bot check, a 403 or a 429 answered this play, or per-video requests are paused after one |
| `unavailable` | the video is private, removed, blocked in the country or for members only |
| `age-restricted` | the source asks the viewer to confirm their age or sign in |
| `could-not-play` | any other failure: an unclassified network or extractor error, a premiere that has not started, a format that is not offered |

A play that only could not get its turn, because all four play slots stayed
taken for three seconds, eight plays already wait, or resolution timed out,
is answered `503` with `Retry-After: 60` and no card. That failure says
nothing about the video, and a card would be recorded as its content.

A card is an MPEG transport stream, answered `200` with
`Content-Type: video/mp2t` and sent in chunks, with no `Content-Length` and
no `Accept-Ranges`, and a `Range` header is ignored. Jellyfin probes `.strm`
targets with ffprobe during a library scan, and ffprobe starts with
`Range: bytes=0-`, so a card cannot be told from a play by its request. A
transport stream of unknown length reports no duration, so the scan records
none and the film keeps its real runtime when the failure is over. A
`HEAD` request is answered `503` while a play cannot be resolved. A
successful resolution answers `HEAD` as it always did. The signature and
expiry check runs before any of this, so a bad link is `401` and never a
card. A card is never cached, is sent with `Cache-Control: no-store`, and
writes nothing to the index.

The `status_cards` setting is `on` by default. With `off`, a play that
cannot be resolved for a reason of the video redirects to the watch page, as
before; a play that only lacked its turn is still answered `503`. Set it
with `PATCH /api/v1/settings`.

The cards are rendered once and kept in
`media_archivist/server/api_v1/cards/`. Each is one ffmpeg call, changing
only the two lines of text and the output name:

```bash
ffmpeg -f lavfi -i "color=c=0x14161a:s=960x540:r=2:d=10" -f lavfi -i "anullsrc=r=22050:cl=mono" -t 10 -vf "drawtext=fontfile=DejaVuSans.ttf:text='<line one>':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=(h/2)-55,drawtext=fontfile=DejaVuSans.ttf:text='<line two>':fontcolor=0xb8bcc4:fontsize=26:x=(w-text_w)/2:y=(h/2)+15" -c:v libx264 -profile:v baseline -level 3.0 -pix_fmt yuv420p -preset veryslow -crf 36 -g 2 -c:a aac -b:a 16k -shortest -f mpegts <name>.ts
```

The result is H.264 baseline at 960x540 and 2 frames per second, with a
silent mono AAC track, in an MPEG transport stream. The first line is
DejaVu Sans at 42 px in white and the second at 26 px in grey, centred on a
dark background.

## Caveats

- **YouTube via the redirect endpoint** still requires a player that
  can resolve a YouTube watch URL. Jellyfin's official YouTube plugin
  or `yt-dlp`-based extensions handle this. The server does *not*
  transcode or extract. It returns the canonical watch URL.
- **URL stability**: direct Bandcamp / SoundCloud streams are stable.  YouTube CDN URLs are not, that is why the server-redirect mode is
  recommended for YouTube rows.
- **Authentication**: the server is single-tenant and unauthenticated
  by design. If you expose the port outside a trusted network, put it
  behind a reverse proxy that handles auth.

---
[← Running as a Service](deploy.md) · [Home](index.md) · [CLI Architecture →](cli.md)
