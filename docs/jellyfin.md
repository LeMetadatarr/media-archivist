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

## Recommended: play-time yt-dlp resolution with `?resolve=1`

Source URLs (YouTube especially) expire; the raw watch/listing URL still
resolves as an *entry*, but stops pointing at a directly playable file.
When `yt-dlp` is installed (either the `yt_dlp` Python package or the
`yt-dlp` binary on `PATH`), `/strm/{id}?resolve=1` turns the endpoint
into a play-time yt-dlp hook instead of a static URL lookup.

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
