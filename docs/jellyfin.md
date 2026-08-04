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

## Keeping expired streams playable with yt-dlp

Source URLs (YouTube especially) expire; the raw watch/listing URL still
resolves as an *entry*, but stops pointing at a directly playable file.
When `yt-dlp` is installed (either the `yt_dlp` Python package or the
`yt-dlp` binary on `PATH`), `/strm/{id}` can resolve a **fresh** direct
media URL on demand instead of returning the stored `stream`/`url`
verbatim:

```bash
curl "http://nas.local:8000/strm/<entry_id>?resolve=1"
```

To make this the default for every `.strm` request — useful when
Jellyfin has no yt-dlp plugin of its own and you want the server to do
the resolving — set `MEDIA_ARCHIVIST_STRM_RESOLVE=1` in the server's
environment; `?resolve=0` still overrides it off per-request.

Resolution never breaks the `.strm` contract: if yt-dlp is unavailable
or fails to resolve (private/deleted video, network hiccup, ...), the
endpoint falls back to the stored `stream`/`url` and logs a warning —
it never returns an error status, since Jellyfin/Kodi need a body back.

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
