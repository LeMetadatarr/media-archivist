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

`POST /downloads` queues a file download for the calling app: `entry` (an id
or `youtube:<videoId>`), and optionally `target_dir`, `filename` (a template
over the fields yt-dlp offers), `format`, `subtitles` (language codes),
`subtitles_kinds` and `keep_info_json`. The folder must be inside
`MEDIA_ARCHIVIST_DOWNLOAD_ROOTS`; see [deploy](deploy.md). An identical request
still queued or running returns the existing record. `GET /downloads` lists the
caller's downloads, newest first (`app=all` for everyone's; `status` takes
comma-separated `queued`, `running`, `completed`, `failed` or `cancelled`;
`limit` is 1 to 1000, default 100; `cursor` is the previous `next_cursor`), `GET /downloads/{id}` shows one,
and `DELETE /downloads/{id}` cancels a queued or running download or removes a
finished one from the history; `delete_files=true` also deletes the files it
placed that are still unchanged. The server returns 503 when yt-dlp is not
installed and 429 when the app already has too many downloads queued.

## YouTube pacing

Every kind of YouTube request (`details`, `listing`, `feed`, `thumb`,
`search`, `download`) has its own back-off. After YouTube shows its bot check
the kind pauses, starting at 30 minutes and doubling to at most a day.
`GET /youtube/backoff` shows the paused kinds and the address family;
`POST /youtube/backoff/{kind}/clear` and `DELETE /youtube/backoff/{kind}` end
a pause by hand.

## Settings

`GET /settings` and `PATCH /settings` read and change the run-time settings
`youtube_ip_family` (`ipv4` by default), `youtube_request_spacing` in seconds,
`download_concurrency`, `status_cards`, `enabled_providers` and
`adult_export_providers`. A value outside its range answers 400 and changes
nothing. `GET /providers` lists the installed providers and why any failed to
load.
