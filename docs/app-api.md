# App API

Other applications use media-archivist through the versioned JSON API under
`/api/v1`. It follows subscriptions, answers queries over the index, plays
entries, writes `.strm` files on request and downloads files on request.
Interactive documentation is served at `/docs`.

## Identity and access

When `MEDIA_ARCHIVIST_API_KEY` is set, every route except `GET /healthz` and
`GET /api/v1/healthz` needs the key as the `X-Api-Key` header or the `apikey`
query parameter, unless the caller sits on a loopback, private or Tailscale
address. [Deploy](deploy.md) lists the exemption rules.

`X-Archivist-App` names the calling application, for example `shortarr`. It is
a label, not a credential: it decides whose subscriptions, download queue and
history a request joins, never what the request may do. Without it the label
is `user`.

`GET /api/v1/meta` returns the server version, the schema version and the
list of features this build offers, so a client can check before it relies on
one.

## Subscriptions

A subscription is a YouTube channel or playlist the index keeps current:
uploads, lengths, thumbnails and, for a channel, its playlists. Indexing does
not write any file.

| Route | Purpose |
| --- | --- |
| `GET /subscriptions` | Every subscription with its holders. |
| `POST /subscriptions` | Follow `url`. Optional `label`, `index_playlists`, `export`. A second request for the same channel adds the caller as a holder and changes nothing else; the answer is 201 for a new subscription and 200 otherwise. |
| `GET /subscriptions/{id}` | One subscription. |
| `PATCH /subscriptions/{id}` | Change `label`, `index_playlists` or `export`. |
| `DELETE /subscriptions/{id}` | Remove the caller as holder; `all_holders=true` removes everyone. A subscription with no holder stops being indexed, and its entries stay. |
| `POST /subscriptions/{id}/refresh` | Queue a listing now. Answers 202 with the task. |
| `GET /subscriptions/{id}/playlists` | The playlists of a channel and their sizes. |
| `GET /subscriptions/{id}/thumbnail` | Redirect to the channel picture. |

The `export` object has `automatic` (default false), `target_dir` and
`layout` (`movies` or `episodes`). An automatic export needs a `target_dir`
and is the only way a subscription writes `.strm` files by itself.

## Queries

`GET /entries` answers "what is new", "what is in this length range" and "what
is not about this topic" over the index.

| Parameter | Meaning |
| --- | --- |
| `subscription`, `playlist` | Comma-separated ids to read from. |
| `since` | Entries published on or after the date of this ISO 8601 time (the time of day is ignored), and undated entries first seen after it. |
| `first_seen_after` | Entries the index first saw after this time. |
| `min_duration`, `max_duration` | Length range in seconds. |
| `unknown_duration` | `include` or `exclude` entries whose length is not known yet. |
| `exclude` | Comma-separated title words; an entry whose title holds one is left out. |
| `vertical` | Only vertical videos (`true`), or every video that is not known to be vertical (`false`), which includes those whose orientation is unknown. |
| `fields` | Comma-separated subset of `id`, `key`, `url`, `title`, `duration`, `published`, `first_seen`, `channel`, `thumbnail`, `vertical`, `subscriptions`, `playlists`. |
| `limit`, `cursor` | Page size (1 to 1000, default 100) and the `next_cursor` of the previous page. |

The answer holds `entries`, `next_cursor` (null on the last page) and
`complete`, which says whether every source of the query has been listed yet.
`GET /entries/{id-or-key}` returns one entry; a YouTube key looks like
`youtube:<videoId>`. `GET /playlists/{id}` returns a playlist with its videos
in playlist order.

## Playing

`GET /stream/{id}` resolves a fresh address and redirects to it; `mode=proxy`
passes the bytes through instead. When a video cannot be played it answers a
short status card in place of an error, so a library item is not broken by a
transient failure; a play that only could not get its turn answers 503 with
`Retry-After`. The `status_cards` setting turns the cards off.

`POST /entries/{id}/link` returns a signed link. The body may carry
`expires_in`, a whole number of seconds; without it the link does not expire.
A signed link plays that one entry from any address without the API key, and
opens no other route. The signature is an HMAC-SHA256 keyed with
`MEDIA_ARCHIVIST_LINK_SECRET`, or with a random secret kept in the index when
the variable is unset; changing the secret revokes every signed link.
`MEDIA_ARCHIVIST_PUBLIC_URL` is the address links carry.

## Exports

`POST /exports` writes `.strm` files, and the `.nfo` and poster files beside
them, for one `entry`, a `playlist` or a `subscription` (`scope` and `id`).
`target_dir` must lie inside one of the folders in `MEDIA_ARCHIVIST_LIBRARY_ROOTS`,
symbolic links resolved. `links` is `plain` or `signed`; `jellyfin_refresh`
asks Jellyfin to rescan; `collection` builds a collection from a playlist;
`layout` is `movies` (default) or `episodes`; `base_url` replaces the address
the links carry for this export.
A provider marked adult is refused unless it is allowed in the
`adult_export_providers` setting, see [plugins](plugins.md). `GET
/exports/{id}` shows progress, `DELETE /exports/{id}` removes the files the
export wrote and keeps any that changed since. `DELETE /exports?scope=&id=&target_dir=`
undoes every export of that scope into that folder and answers 404 when there
is none. See [Jellyfin](jellyfin.md).

## Downloads

`POST /downloads` queues a file download for the calling app. `entry` is an
entry id or `youtube:<videoId>`. `tool` chooses the program: `yt-dlp` (the
default) downloads a video, or any format, and `tutubo` fetches what a dataset
needs, described in the next section.

The folder is either `target_dir`, an absolute path, or `root` and `dest`: the
position of a folder in `MEDIA_ARCHIVIST_DOWNLOAD_ROOTS` (0 by default) and a
relative path below it. Without either, the first root. The folder must stay
inside a root once symbolic links are resolved; `dest` may not hold `..`, `.`,
empty parts, backslashes or control characters, and may not name a staging
folder. `GET /downloads/roots` lists the roots with their position, path and
free space. See [deploy](deploy.md).

A `yt-dlp` download also takes `filename` (a template over the fields yt-dlp
offers), `format`, `subtitles` (language codes), `subtitles_kinds` and
`keep_info_json`. An option of the other tool answers 400.

An identical request (same entry, folder and tool) still queued or running
returns the existing record with 200 instead of 201. `POST /downloads/batch`
takes `entries` (1 to 1000) and the same options as one download, and queues
one download per entry: all of them, or none when any entry is refused or the
batch would pass the app's limit of 1000 queued downloads. Its answer lists
each record with `created`, and the number created.

`GET /downloads` lists the caller's downloads, newest first (`app=all` for
everyone's; `status` takes comma-separated `queued`, `running`, `completed`,
`failed` or `cancelled`; `limit` is 1 to 1000, default 100; `cursor` is the
previous `next_cursor`). `GET /downloads/{id}` shows one, and `DELETE
/downloads/{id}` cancels a queued or running download or removes a finished
one from the history; `delete_files=true` also deletes the files it placed
that are still unchanged. The server answers 503 when the tool is not
installed, 429 when the app would have too many downloads queued, 422 when the
body does not match the schema and 400 for a request it refuses.

Every record carries `tool`, `status`, `target_dir`, `root` (the position of
its root, null when that root is no longer configured), `root_dir`, `dest` (the
folder relative to the root), `files` (absolute paths) and `paths` (the same
files relative to the root), `error` and `error_kind`. A queued record that
waits for a pause has `waiting_for` with `reason` and `until`.

## Downloads for datasets

A download with `tool` set to `tutubo` fetches one YouTube video for a speech
or text dataset: an audio track in the codec YouTube stores it, with no
re-encode, its caption files exactly as YouTube serves them, and the video's
details. Its options are `audio` and `captions` (both
true by default, not both false), `caption_kinds` (`manual`, `asr` or both,
the default) and `caption_formats` (`json3`, `vtt`, `srv1`, `srv2`, `srv3`,
`ttml`; default `json3` and `vtt`). Machine translations of captions are never
fetched.

The audio is the track YouTube marks as the original language when it marks
one. A video with dubbed tracks that marks none gets the best audio track,
which can be a dub. The program records which it took: `original` on the
audio file is true for the original track, false for a dub and null when
YouTube does not say. A dub makes the fetch incomplete; an unknown track does
not.

The folder receives:

| File | What it is |
| --- | --- |
| `audio/<id>.<ext>` | The audio track, usually `m4a` or `webm`. |
| `captions/<id>.<lang>.<kind>.<format>` | One file per caption track and format. |
| `meta/<id>.json` | The video's details: title, channel, duration, upload date, the audio and caption tracks offered, what was fetched and the tool versions. |
| `meta/<id>.files.json` | The completeness record: the last request, `complete`, `incomplete`, `files` (each file this server placed for the video in the folder, with its path relative to the folder, `bytes` and `sha256`) and `latest` (the paths the last fetch placed). |

A finished record adds `fetch` (the options), `complete`, `incomplete` (one
line per missing or doubtful piece: a caption format YouTube does not offer, a
live stream that has not started, audio that was not written, a dubbed audio
track, a file whose size differs from the size the program reported) and
`file_details`: each file this download placed, with `path` relative to the
root, `bytes` and `sha256` as measured on the placed file, `role` (`audio`,
`caption` or `meta`), for a caption `language`, `kind` and `format`, and for
the audio `codec`, `ext` and `original`. A fetch is complete only when the
program exited cleanly, reported the fetch complete and nothing is listed in
`incomplete`. `status` is `completed` whenever the files were placed, also
when `complete` is false; check `complete` before using a video. A video the
program could not fetch at all is `failed`, with `error_kind` `permanent`
(private, removed), `network` or `tool`.

Several requests with different options may share a folder. The
completeness record keeps listing the files an earlier fetch placed and a
later one did not ask for, as long as each still holds its recorded size and
checksum; one that changed drops out of the record.

A file already in the folder is kept when it holds the same bytes, and
replaced only when this server placed it (its `meta/<id>.files.json` lists it)
and it is unchanged since. Any other file at one of the names fails the
download with `error_kind` `conflict` and nothing is written; that includes a
`meta/<id>.files.json` this server did not write. A file being replaced is
first moved to a new name beside it (`.<name>.archivist-old-<random>`) that
no other file holds, and dropped once the download is recorded as completed.
A cancel or an error while the files are being placed puts back the files
and removes the folders the download created; a target folder that did not
exist before stays, empty. Each step of a placement is listed first in
`meta/.<id>.archivist-journal.json`; when the server stops during a placement
(a crash or a kill), the next fetch or cancel of that video in that folder
reads the journal and finishes the placement when its completeness record
was written, or puts the folder back when it was not. It moves or removes
only the files the journal names, with the bytes it recorded, and never puts
an older file over one that changed after the crash. The journal carries an
HMAC made with a random key that the server keeps in its index for that one
placement and drops once the placement is kept or undone. Any other journal,
including a well-formed one written by someone else or a copy of an earlier
one, fails the download with `conflict` and nothing is touched. The index is
trusted: whoever can write the server's index can make recovery act on a
journal of their own, while write access to the download folder alone cannot
(though it can change the files there directly). One server process works on
a download folder at a time; two processes recovering one folder at once are
not supported. A folder whose
completeness record matches the request, with every file still at its size
and checksum, completes again at once without asking YouTube, so queueing a
whole channel a second time costs nothing for the videos already there. A
stop or a restart keeps a partial audio file for the next attempt; a cancel
removes it. No download may be placed in a staging folder (`.archivist-dl-…`
or `.archivist-fetch-…`), whether its name is given or a symbolic link leads
there: a staging folder is removed with everything in it.

### Waiting for a pause

Downloads never work around YouTube's bot check. When YouTube asks for one,
the download goes back to the queue, the kind of request that met it pauses
(30 minutes, doubling to a day), and the download fails once it has met the
check on five runs. `tutubo` downloads wait for the server's `download` and
`details` pauses and for the pause tutubo's own programs share on this
machine; `yt-dlp` downloads wait for `download` only. `GET /downloads` and
`POST /downloads/batch` answer `pauses`, which gives for each tool `paused`,
`retry_at` and `reason`; a queued record gives the same in `waiting_for`. A
client polls the record at `retry_at`, not before: asking again does not make
anything start sooner.

### A client's round trip

1. `POST /subscriptions` with the channel's or playlist's `url`, then read
   `GET /entries?subscription=<id>&fields=key,title,duration,published` page
   by page until `next_cursor` is null and `complete` is true. `key` is
   `youtube:<videoId>`; `published` (the upload date) and `duration` can be
   null until the index has read the video, and `GET /entries/{key}` reads it
   on request.
2. `POST /downloads/batch` with the chosen keys, `tool` `tutubo`, `root` and a
   `dest` for the dataset, and the caption kinds and formats.
3. Poll `GET /downloads?status=queued,running` and `GET /downloads/{id}`;
   while `pauses.tutubo.paused` is true, wait until `retry_at`.
4. For each completed record, map `file_details[].path` onto the client's own
   mount of root `root`, and verify `bytes` and `sha256` after any copy.

## YouTube pacing

Every kind of YouTube request (`details`, `listing`, `feed`, `thumb`,
`search`, `download`) has its own back-off. After YouTube shows its bot check
the kind pauses, starting at 30 minutes and doubling to at most a day.
`GET /youtube/backoff` shows the paused kinds and the address family, and as
`shared` the pause tutubo's programs share on this machine;
`POST /youtube/backoff/{kind}/clear` and `DELETE /youtube/backoff/{kind}` end
one of the server's own pauses by hand. The shared pause is not ended through
the API.

## Settings

`GET /settings` and `PATCH /settings` read and change the run-time settings
`youtube_ip_family` (`ipv4` by default), `youtube_request_spacing` in seconds,
`download_concurrency`, `status_cards`, `enabled_providers` and
`adult_export_providers`. A value outside its range answers 400 and changes
nothing. `GET /providers` lists the installed providers and why any failed to
load.
