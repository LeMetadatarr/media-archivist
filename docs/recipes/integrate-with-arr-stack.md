# Recipe: Use media-archivist from another app

An app such as Shortarr follows a YouTube channel, finds new uploads and asks
for a stream link or a download. It keeps its own policy (word filters, length
window, quality) and leaves the indexing, the playing and the downloading to
media-archivist. Every call below sends `X-Archivist-App: myapp`, so the app
has its own queue and history, and `X-Api-Key` when a key is set. The
[App API](../app-api.md) describes each route.

## Follow a channel

```bash
curl -X POST "$ARCHIVIST/api/v1/subscriptions" \
     -H "X-Archivist-App: myapp" -H "Content-Type: application/json" \
     -d '{"url": "https://www.youtube.com/@SomeChannel"}'
```

The answer carries the subscription `id`. The index lists the channel in the
background; `POST /api/v1/subscriptions/{id}/refresh` asks for a listing now.

## Ask what is new

```bash
curl -H "X-Archivist-App: myapp" "$ARCHIVIST/api/v1/entries?subscription=$SID&first_seen_after=2026-01-01T00:00:00Z&min_duration=600&exclude=trailer,reaction"
```

The app keeps `next_cursor` for the next page and decides, with its own rules,
which entries become candidates.

## Stream or download

For a library that plays from the network, write `.strm` files:

```bash
curl -X POST "$ARCHIVIST/api/v1/exports" -H "X-Archivist-App: myapp" \
     -H "Content-Type: application/json" \
     -d '{"scope": "entry", "id": "youtube:VIDEOID", "target_dir": "/library/shorts"}'
```

For a file on disk, queue a download:

```bash
curl -X POST "$ARCHIVIST/api/v1/downloads" -H "X-Archivist-App: myapp" \
     -H "Content-Type: application/json" \
     -d '{"entry": "youtube:VIDEOID", "target_dir": "/downloads/shorts"}'
curl "$ARCHIVIST/api/v1/downloads/$DOWNLOAD_ID" -H "X-Archivist-App: myapp"
```

Both folders must lie inside the roots the server allows; a request outside
them is refused with 400.
