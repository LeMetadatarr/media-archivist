# App API

media-archivist is the streaming library and the YouTube index that other
applications build on. Shortarr, and any app built the same way, asks
media-archivist to follow channels and playlists, queries what those
sources contain, and then chooses one of two actions for a video: stream it
through Jellyfin without downloading it, or download it into a folder the app
owns. This page is the HTTP contract those apps use.

Everything here lives under `/api/v1`. The unversioned routes the Web UI and
the CLI use (`/entries`, `/subscriptions`, `/strm/{id}` and the rest) keep
working beside it; they are not a stable contract for other programs, and
`/api/v1` is.

## Principles

**Indexing is not exporting.** Following a channel keeps its listing current
in media-archivist's index. It writes nothing into a media library. A `.strm`
file appears only when somebody asks for it: a user in media-archivist's Web UI,
or an app through `POST /api/v1/exports`, for one video, one playlist or one
channel. A subscription can be switched to export automatically, and that
switch is off unless it is turned on.

**A `.strm` file never holds a media address.** Its body points at
media-archivist, which resolves a fresh stream with yt-dlp each time the item
is played. YouTube's stream addresses expire within hours, so an address
written at export time would stop playing the same day.

**One YouTube client.** All YouTube traffic for every app goes through one
media-archivist process: listings and pictures through
[tutubo](https://github.com/LeMetadatarr/tutubo), per-video details, stream
resolution and downloads through yt-dlp. Requests are made one at a time and
spaced, and when YouTube answers with its bot check, only the kind of request
that was challenged pauses (see [Back-off](#back-off)). Two apps that follow
the same channel cost YouTube one listing, not two.

## Authentication and app identity

The API uses the server's API key. When `MEDIA_ARCHIVIST_API_KEY` is set,
every request carries it in the `X-Api-Key` header or the `apikey` query
parameter, unless it comes directly from an address in the exempt list
(loopback, the private IPv4 ranges and Tailscale by default; set
`MEDIA_ARCHIVIST_AUTH_EXEMPT` to change it). A request that arrived through a
reverse proxy is never exempt. `GET /healthz` needs no key. When the variable
is unset, the API is open, which is only appropriate on a trusted network.

An app names itself in the `X-Archivist-App` header, for example
`X-Archivist-App: shortarr`. The name is a label, not a credential: it decides
whose queue a download joins, whose history it appears in and which app holds
a subscription. A request without the header is attributed to `user`, the
name the Web UI uses.

Errors are JSON objects with a `detail` field and the usual status codes:
`400` for a request that cannot be read, `401` for a missing or wrong key,
`404` for an unknown id, `409` when the request conflicts with the current
state, `429` when a queue is full, and `503` when a needed tool (yt-dlp) is
not installed.

## Identifiers

Every indexed video is an *entry*. An entry has an opaque `id` (a hex string,
stable for the life of the index) and a readable `key` made of the source and
the source's own id, such as `youtube:dQw4w9WgXcQ`. Every path that takes an
entry id accepts either form, so an app that only knows a YouTube video id
never needs to look up the opaque one first.

A subscription has an `id` of the same readable kind: `youtube:channel:UC…`
for a channel (its permanent channel id, not its handle), `youtube:playlist:PL…`
for a playlist. A playlist found on a channel's Playlists tab is
`youtube:playlist:PL…` too, whether or not anybody subscribed to it.

Times are ISO 8601 in UTC (`2024-05-01T12:00:00Z`). Durations are seconds.

## Subscriptions

A subscription is a channel or playlist that media-archivist keeps indexed.

### Create

```
POST /api/v1/subscriptions
{
  "url": "https://www.youtube.com/@SomeChannel",
  "label": "Some Channel",
  "index_playlists": true,
  "export": {"automatic": false, "target_dir": null, "layout": "movies"}
}
```

Only `url` is required. A channel URL may be a handle, a `/channel/UC…`
address or any of its tabs; a playlist URL is anything with `list=`. The
server resolves the URL to its permanent id, so two apps that add the same
channel by different addresses share one subscription. Adding a URL that is
already subscribed returns the existing subscription with status `200` and
records the calling app as one of its holders; a new one returns `201`. The
first full listing is queued at once, and the response does not wait for it.

`index_playlists` (default `true` for channels, ignored for playlists) keeps
the channel's Playlists tab and each of those playlists' video ids indexed.

`export.automatic` is `false` by default. When it is `true`, every video the
subscription indexes from then on is exported to `export.target_dir` in the
given `layout` (see [Exports](#exports)), and `target_dir` is required.
Videos already indexed when the switch is turned on are not exported by it; an
explicit export does that.

### List and read

```
GET /api/v1/subscriptions
GET /api/v1/subscriptions/{id}
```

A subscription looks like this:

```json
{
  "id": "youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx",
  "url": "https://www.youtube.com/@SomeChannel",
  "kind": "channel",
  "title": "Some Channel",
  "label": "Some Channel",
  "thumbnail": "/api/v1/subscriptions/youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx/thumbnail",
  "holders": ["shortarr", "user"],
  "index_playlists": true,
  "export": {"automatic": false, "target_dir": null, "layout": "movies"},
  "entry_count": 412,
  "first_indexed_at": "2024-05-01T12:00:00Z",
  "last_listing_at": "2024-05-06T12:00:00Z",
  "last_feed_at": "2024-05-07T08:00:00Z",
  "next_check_at": "2024-05-07T10:00:00Z",
  "failures": 0,
  "last_error": null,
  "flagged": false
}
```

`first_indexed_at` is when the first complete listing finished; until then it
is `null`. A subscription that fails three times in a row for a reason other
than the bot check is `flagged` and is not tried again until it is changed or
refreshed by hand.

### Change

```
PATCH /api/v1/subscriptions/{id}
{"export": {"automatic": true, "target_dir": "/media/streams/youtube"}}
```

Changes `label`, `index_playlists` and `export`. Changing a subscription
clears its `flagged` state.

### Refresh now

```
POST /api/v1/subscriptions/{id}/refresh
```

Queues a complete listing now instead of at its scheduled time. It returns at
once with the task that does the work; while the `listing` kind is paused for
the bot check, the task waits.

### Delete

```
DELETE /api/v1/subscriptions/{id}
```

Removes the calling app from the subscription's holders. When no holder is
left, the subscription stops being indexed. Its entries stay in the index,
together with any `.strm` file or download already made from them; nothing on
disk is deleted. `DELETE /api/v1/subscriptions/{id}?all_holders=true` stops
the subscription whoever holds it.

### How a subscription is kept current

A channel's feed (the latest fifteen uploads, one cheap request) is read every
two hours, and the channel's Videos tab is listed in full once a week; the
full listing is also what gives the channel id the feed needs. A playlist is
listed again every 24 hours. When `index_playlists` is on, a channel's
Playlists tab is read once a day and each of its playlists' video ids once a
day, oldest first, a few playlists per pass.

A listing does not always say how long a video is, and a feed never does. The
missing lengths come first from tutubo's listing of the same source, and
otherwise from one yt-dlp request per video, a few per pass. A channel's and a
playlist's own picture is fetched once and retried after a day if it failed.
A value once known is never replaced by a listing that lacks it.

## Entries

### Query

```
GET /api/v1/entries
```

| Parameter | Meaning |
| --- | --- |
| `subscription` | One or more subscription ids, comma-separated. An entry matches when any of them lists it. |
| `playlist` | One or more playlist ids, comma-separated, including playlists found on a channel's Playlists tab. |
| `since` | Only entries that are new on or after this time (see below). |
| `first_seen_after` | Only entries the index first saw after this time, for polling. |
| `min_duration`, `max_duration` | Length window in seconds, both ends included. |
| `unknown_duration` | `exclude` (default when a window is given) or `include`. |
| `exclude` | Title words or phrases, comma-separated; an entry whose title contains one is left out. |
| `fields` | Comma-separated list of the fields to return; all fields by default. |
| `limit` | Page size, 1 to 1000, default 100. |
| `cursor` | The `next_cursor` of the previous page. |

`since` means what an app asking for "new videos" needs: an entry counts as
new on or after `T` when its upload date is on or after `T`. When its upload
date is not known yet, it counts when the subscription first saw it on or
after `T` and after its first complete listing, so a channel's back
catalogue never shows up as new on the day it is subscribed.

`exclude` matches whole words: case, hyphens, underscores and runs of spaces
are folded, a word never matches inside a longer word, and its plural with
`-s` or `-es` matches too. `exclude=trailer,behind the scenes` leaves out
"Official Trailers" and "Behind-the-Scenes" but keeps "Trailblazer".

Results are ordered by the time the index first saw each entry, newest first,
and the cursor is stable while new entries arrive: a new upload appears at the
top of a fresh query and never shifts a page an app is in the middle of
reading. An app that polls keeps the `first_seen` of the newest entry it has
handled and passes it as `first_seen_after` on its next poll, which returns
only what arrived since.

```json
{
  "entries": [
    {
      "id": "3f9a…",
      "key": "youtube:dQw4w9WgXcQ",
      "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "title": "A Short Film",
      "duration": 754,
      "published": "2024-05-03",
      "first_seen": "2024-05-03T14:02:11Z",
      "channel": {"id": "UCxxxxxxxxxxxxxxxxxxxxxx", "title": "Some Channel"},
      "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
      "vertical": false,
      "subscriptions": ["youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx"],
      "playlists": [{"id": "youtube:playlist:PLyyyy", "title": "Animation"}]
    }
  ],
  "next_cursor": "eyJ0IjoxNzE0NzQ…",
  "complete": true
}
```

`published` is a date, or `null` until it is known. `vertical` is `true` for a
video YouTube files as a Short. `next_cursor` is `null` on the last page.
`complete` is `false` when a subscription in the query has not finished its
first listing, so the answer may grow without anything being uploaded.

### One entry

```
GET /api/v1/entries/{id}
```

Returns one entry with every field above. When a YouTube entry still lacks
its length, upload date or channel, the server asks yt-dlp for them before
answering if the `details` kind is not paused and no other details request is
running; otherwise it answers with what it has, and the background fill
supplies the rest later.

## Channel playlists

```
GET /api/v1/subscriptions/{id}/playlists
GET /api/v1/playlists/{playlist_id}
```

The first lists the playlists on a subscribed channel's Playlists tab. The
second returns one playlist with its video entry keys in playlist order.

```json
{
  "playlists": [
    {
      "id": "youtube:playlist:PLyyyy",
      "title": "Animation",
      "url": "https://www.youtube.com/playlist?list=PLyyyy",
      "thumbnail": "https://i.ytimg.com/vi/…/hqdefault.jpg",
      "video_count": 23,
      "videos": ["youtube:dQw4w9WgXcQ", "youtube:…"],
      "videos_at": "2024-05-07T03:10:00Z",
      "error": null
    }
  ],
  "listed_at": "2024-05-07T03:00:00Z"
}
```

`videos` is `null` for a playlist whose video ids have not been read yet. A
playlist id passed to `GET /api/v1/entries?playlist=` returns full entries for
its videos.

## Streams

### Play-time resolution

```
GET /api/v1/stream/{id}
HEAD /api/v1/stream/{id}
```

Resolves a fresh stream with yt-dlp and answers with a `302` redirect to it.
With `mode=proxy`, media-archivist streams the bytes itself and passes the
player's `Range` header through, for players that do not follow redirects or
cannot reach YouTube's servers. A failed resolution still redirects, to the
watch page, because a `.strm` that answers with an error breaks the item in
Jellyfin. This is the address `.strm` files point at.

### Signed links

```
POST /api/v1/entries/{id}/link
{"expires_in": 86400}
```

```json
{"url": "https://archivist.example.org/api/v1/stream/3f9a…?exp=1714831200&sig=…", "expires_at": "2024-05-04T14:00:00Z"}
```

A signed link plays one entry and does nothing else. It carries its own
signature, so a player outside the local network needs no API key and the key
is never written into a file or a player's history. `expires_in` is optional;
without it the link does not expire, which is what an exported `.strm` file
uses. Changing `MEDIA_ARCHIVIST_LINK_SECRET` revokes every signed link at
once; the `.strm` files are then exported again.

Inside the exempt networks no signature is needed, and exports for a local
Jellyfin write plain links.

## Exports

An export writes `.strm` files, with their `.nfo` and poster, into a library
folder Jellyfin watches.

```
POST /api/v1/exports
{
  "scope": "playlist",
  "id": "youtube:playlist:PLyyyy",
  "target_dir": "/media/streams/youtube",
  "layout": "movies",
  "links": "signed",
  "refresh_jellyfin": true,
  "collection": true
}
```

`scope` is `entry`, `playlist` or `subscription`, and `id` names it.
`target_dir` must be inside one of the folders listed in
`MEDIA_ARCHIVIST_LIBRARY_ROOTS`; any other path is refused with `400`, so an
API key cannot be used to write anywhere on the server.

`layout` is one of:

- `movies` (default): one folder per video, `Title (Year) [videoid]/Title (Year) [videoid].strm`
  with `movie.nfo` and `poster.jpg` beside it. This is the layout Jellyfin's
  Movies library type reads, and the same one media-archivist uses for
  downloaded files.
- `episodes`: the channel as a show and the upload year as a season,
  `Channel/Season 2024/Channel - S2024E0503 - Title [videoid].strm`, with
  `tvshow.nfo`, `season.nfo` and episode `.nfo` files, for a Shows library.

The `.nfo` files carry the title, the description, the upload date, the
length, the channel as studio and the YouTube id as a unique id, and the
poster is the video's own thumbnail; both come from media-archivist's index and
from [metadatarr](https://github.com/LeMetadatarr/metadatarr) where it knows the
item. `links` is `plain` (default) or `signed`, as described under
[Signed links](#signed-links).

The response lists every file written. Exporting the same entry again into
the same folder rewrites its files in place, so an export is safe to repeat.

```json
{
  "id": "exp_01HX…",
  "written": [
    "/media/streams/youtube/A Short Film (2024) [dQw4w9WgXcQ]/A Short Film (2024) [dQw4w9WgXcQ].strm",
    "/media/streams/youtube/A Short Film (2024) [dQw4w9WgXcQ]/movie.nfo"
  ],
  "skipped": [{"key": "youtube:…", "reason": "unavailable"}],
  "posters_pending": 1,
  "jellyfin": {"refresh": "requested", "collection": "pending"}
}
```

Posters are fetched after the response, so a large export answers quickly;
`GET /api/v1/exports/{id}` shows when they are all on disk.

`refresh_jellyfin` asks Jellyfin to scan the written folders. It needs
`MEDIA_ARCHIVIST_JELLYFIN_URL` and `MEDIA_ARCHIVIST_JELLYFIN_API_KEY`; without
them the export still succeeds and reports `"refresh": "not configured"`.

`collection` applies to a playlist export: once Jellyfin has scanned the new
items, media-archivist makes a Jellyfin collection named like the playlist
holding exactly those items, or updates the one it made before. A collection
media-archivist did not make is never changed. `GET /api/v1/exports/{id}`
reports `"collection": "created"`, `"updated"` or the reason it was not made.

```
DELETE /api/v1/exports?scope=entry&id=youtube:dQw4w9WgXcQ&target_dir=/media/streams/youtube
```

removes the files an earlier export wrote for that scope in that folder and
nothing else.

## Downloads

media-archivist is the one YouTube downloader for every app. Each app has its
own queue and history, decides where the file goes and decides what it is
called.

### Queue a download

```
POST /api/v1/downloads
{
  "entry": "youtube:dQw4w9WgXcQ",
  "target_dir": "/media/shorts/incoming",
  "filename": "{title} ({year}) [{video_id}]",
  "format": "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=1080]",
  "subtitles": ["en", "pt"]
}
```

`entry` is an entry id or key; a YouTube video that is not indexed yet is
accepted as a key and indexed first. `target_dir` must be inside one of the
folders listed in `MEDIA_ARCHIVIST_DOWNLOAD_ROOTS`. `filename` is the name
without its extension, built from `{title}`, `{year}`, `{date}`,
`{video_id}`, `{channel}` and `{id}`; characters that are not allowed in a
file name are replaced. Without `filename` the file is named
`{title} [{video_id}]`. `format` is a yt-dlp format selector and defaults to
the server's. `subtitles` lists languages to fetch beside the video.

The response is the download record, with status `201`. Queuing the same
entry into the same folder while an earlier request for it is still queued or
running returns that earlier record with status `200`.

### Status, queue and history

```
GET /api/v1/downloads/{id}
GET /api/v1/downloads?status=queued,running
GET /api/v1/downloads?status=completed,failed,cancelled&limit=50&cursor=…
```

A list shows the calling app's downloads; `app=all` shows everyone's.

```json
{
  "id": "dl_01HX…",
  "app": "shortarr",
  "entry": "youtube:dQw4w9WgXcQ",
  "title": "A Short Film",
  "status": "running",
  "progress": 42,
  "bytes": 51200000,
  "target_dir": "/media/shorts/incoming",
  "files": [],
  "created": "2024-05-03T14:10:00Z",
  "started": "2024-05-03T14:12:30Z",
  "finished": null,
  "error": null,
  "waiting_for": null
}
```

`status` is `queued`, `running`, `completed`, `failed` or `cancelled`. `files`
lists the video and subtitle files once the download is complete. A queued
download that cannot start because YouTube's bot check paused the `details`
kind says so in `waiting_for`, for example
`{"reason": "bot check", "until": "2024-05-03T15:00:00Z"}`, and starts by
itself when the pause ends. A download that met the bot check part way through
goes back to the queue instead of failing.

### Cancel and forget

```
DELETE /api/v1/downloads/{id}
```

A queued download is removed from the queue. A running one is stopped and
its partial files are removed. A finished one is removed from the history;
`?delete_files=true` deletes its files as well, and only those files.

Downloads run one at a time, in the order they were queued, alternating
between apps so that a long queue from one app does not hold up another.
They do not wait behind index refreshes.

## Back-off

YouTube challenges some kinds of request and not others: from a home IPv6
address, for instance, per-video details can be blocked while listings work.
media-archivist therefore tracks the bot check separately for five kinds:

| Kind | Requests |
| --- | --- |
| `details` | per-video metadata, stream resolution, downloads |
| `listing` | channel and playlist listings, a channel's Playlists tab |
| `feed` | channel feeds |
| `thumb` | channel, playlist and video pictures |
| `search` | YouTube search |

When a request of a kind meets the bot check, that kind pauses for 30 minutes,
doubling with every check in a row up to 24 hours, and a success ends the
pause. The other kinds carry on.

```
GET /api/v1/youtube/backoff
```

```json
{
  "kinds": {
    "details": {"paused": true, "until": "2024-05-03T15:00:00Z", "wait": 3600, "since": "2024-05-03T14:00:00Z"},
    "listing": {"paused": false},
    "feed": {"paused": false},
    "thumb": {"paused": false},
    "search": {"paused": false}
  },
  "ip_family": "ipv4"
}
```

`DELETE /api/v1/youtube/backoff/{kind}` ends a pause by hand, for after the
cause has been fixed, for example a change of IP family.

## Settings

```
GET /api/v1/settings
PATCH /api/v1/settings
{"youtube_ip_family": "ipv4"}
```

| Setting | Values | Default |
| --- | --- | --- |
| `youtube_ip_family` | `ipv4`, `ipv6`, `auto` | `ipv4` |
| `youtube_request_spacing` | seconds between two YouTube requests | `10` |

`youtube_ip_family` decides which address family every YouTube request uses:
tutubo's listings and pictures as well as yt-dlp. IPv4 is the default because
YouTube treats a household's IPv6 range far more harshly than its IPv4
address; on one home connection it challenged six of six per-video lookups
made over IPv6 and none of six made over IPv4. `auto` leaves the choice to
the operating system.

The library and download roots, the Jellyfin connection and the link secret
are environment settings of the server and are shown here read-only, so an
app can tell its user which folders it may use.

## Tasks

Refreshes, exports and fills run as tasks. `GET /api/v1/tasks` and
`GET /api/v1/tasks/{id}` show them with the same fields as the Web UI's task
list. Downloads have their own records under `/api/v1/downloads` and are not
duplicated here.
