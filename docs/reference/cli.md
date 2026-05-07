# CLI Reference

`media-archivist` is a command-line tool for indexing and managing media metadata across five sources: YouTube, YouTube Music, Internet Archive, Bandcamp, and SoundCloud.

## Global Options

All subcommands accept these options:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--version` | N/A | N/A | Show version and exit |
| `-v`, `--verbose` | flag | off | Enable debug logging |

## Database Target (Required for all data subcommands)

Exactly one of these must be provided:

| Flag | Format | Use Case |
|------|--------|----------|
| `--db NAME` | string | Auto-place under XDG at `~/.local/share/media_archivist/<NAME>.json` (recommended for shared databases) |
| `--db-file PATH` | path | Explicit file location (recommended for datasets you commit alongside scripts) |

## Backend Selection (Mutually Exclusive, Default: YouTube)

| Flag | Backend | Archivist | Notes |
|------|---------|-----------|-------|
| (none) | YouTube | `YoutubeArchivist` | Default; channels, playlists, search results |
| `--music` | YouTube Music | `YoutubeMusicArchivist` | Rich track metadata: artist, album, year, explicit |
| `--ia` | Internet Archive | `IAArchivist` | Streaming collections, video files |
| `--bandcamp` | Bandcamp | `BandcampArchivist` | Requires `pip install py_bandcamp` |
| `--soundcloud` | SoundCloud | `SoundCloudArchivist` | Requires `pip install nuvem_de_som` |

## Common Filters

These apply to `add`, `urls`, `list`, `export`, `monitor`:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--require KW` | string | (none) | Only index entries whose title contains all these keywords (repeatable) |
| `--blacklist KW` | string | (none) | Skip entries whose title contains any of these keywords (repeatable) |
| `--min-duration SECONDS` | int | -1 | Minimum duration in seconds (only for backends that expose length) |
| `--skip-explicit` | flag | off | (YouTube Music only) skip tracks flagged explicit |
| `--only-audio` | flag | off | (YouTube Music only) keep only audio-only tracks (no music videos) |

## View Flags

These flags apply to `urls`, `list`, `export` — they activate the read-side `Index` and canonical view:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--canonical` | flag | off | Use the canonical `MediaEntry` view (including external IDs, canonical status) |
| `--where EXPR` | string | (none) | Filter expression (e.g., `duration>180 and source=="youtube_music"`) — see WHERE Language reference |
| `--source NAME` | string | (none) | Keep only entries from this source (youtube, youtube_music, bandcamp, soundcloud, internet_archive) |
| `--has-stream` | flag | None | Keep only entries with a resolved stream URL |
| `--no-stream` | flag | None | Keep only entries without a stream URL |
| `--explicit` | flag | None | (Canonical view only) keep only explicit-flagged tracks |
| `--no-explicit` | flag | None | (Canonical view only) drop explicit-flagged tracks |

---

## Subcommands

### add

Add one or more URLs to the database.

```
media-archivist add URL [URL ...]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--db`, `--db-file` | Database target (required) |
| `--<backend>` | Backend selector (optional; default: youtube) |
| `--require`, `--blacklist`, `--min-duration` | Filters |

**Exit Codes:**

- `0` — Success
- `1` — Validation error (no DB target)

**Examples:**

```bash
# Add a YouTube channel
media-archivist --db-file talks.json add https://www.youtube.com/@SomeChannel

# Add multiple URLs with a keyword filter
media-archivist --db-file music.json --music add \
  https://music.youtube.com/browse/MPREb_xxx \
  https://music.youtube.com/playlist?list=PLyyy

# Add only tracks longer than 3 minutes
media-archivist --db-file tracks.json --music --min-duration 180 add \
  "relaxing ambient music"
```

---

### urls

Print stored URLs (one per line). Output is suitable for piping to `yt-dlp -a -`.

```
media-archivist urls [--grep PATTERN] [--limit N]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--grep PATTERN` | string | (none) | Filter by substring in title (case-insensitive) |
| `--limit N` | int | 0 | Emit at most N URLs (0 = unlimited) |
| View flags | — | — | `--canonical`, `--where`, `--source`, `--has-stream`, `--no-stream` |

**Exit Codes:**

- `0` — Success
- `1` — --where expression error

**Examples:**

```bash
# Dump all URLs to yt-dlp
media-archivist --db-file talks.json urls | yt-dlp -a -

# Download only "podcast" entries
media-archivist --db-file talks.json urls --grep podcast | yt-dlp -a -

# List only YouTube Music URLs with valid streams
media-archivist --db-file db.json urls --canonical --source youtube_music --has-stream

# Complex filter: music entries longer than 5 minutes from Bandcamp
media-archivist --db-file db.json urls --canonical --where "source=='bandcamp' and duration>300"
```

---

### list

List entries in human-readable format (title, tab, URL) or JSON.

```
media-archivist list [--grep PATTERN] [--limit N] [--json]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--grep PATTERN` | string | (none) | Filter by substring in title |
| `--limit N` | int | 0 | Emit at most N entries |
| `--json` | flag | off | Emit JSON array instead of tab-separated |
| View flags | — | — | `--canonical`, `--where`, `--source`, `--has-stream`, `--no-stream`, `--explicit`, `--no-explicit` |

**Exit Codes:**

- `0` — Success
- `1` — --where expression error

**Examples:**

```bash
# List all entries
media-archivist --db-file db.json list

# List with JSON output
media-archivist --db-file db.json list --json

# Entries from specific sources
media-archivist --db-file db.json list --canonical --source bandcamp --limit 10

# Non-explicit music tracks
media-archivist --db-file db.json list --canonical --no-explicit --source youtube_music
```

---

### dump

Dump the entire raw database as JSON (pretty-printed).

```
media-archivist dump
```

**Options:**

| Flag | Description |
|------|-------------|
| `--db`, `--db-file` | Database target (required) |

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
# Backup the database
media-archivist --db-file db.json dump > backup.json

# Inspect raw structure
media-archivist --db-file db.json dump | jq '._meta'
```

---

### export

Export entries as JSON, JSONL, CSV, or plain text with optional field projection.

```
media-archivist export [--format FORMAT] [--fields A,B,C] [--output PATH]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--format FORMAT` | enum | jsonl | One of: `json`, `jsonl`, `csv`, `txt` |
| `--fields A,B,C` | string | (all) | Comma-separated field names to project (default: all) |
| `--output PATH` | path | stdout | Write to PATH instead of stdout |
| `--grep PATTERN` | string | (none) | Filter by title substring |
| `--limit N` | int | 0 | Emit at most N rows |
| View flags | — | — | `--canonical`, `--where`, `--source`, `--has-stream` |

**Default Fields (for CSV when `--fields` not specified):**

`videoId`, `title`, `url`, `thumbnail`, `published`, `views`, `is_live`, `tags`, `description`, `playlist`

**Exit Codes:**

- `0` — Success
- `1` — Invalid format
- `2` — File write error

**Examples:**

```bash
# Export as JSONL (one entry per line)
media-archivist --db-file db.json export --format jsonl > export.jsonl

# Export as CSV with selected columns
media-archivist --db-file db.json export --format csv \
  --fields videoId,title,url,published > dataset.csv

# Export canonical records
media-archivist --db-file db.json export --format json --canonical -o canonical.json

# Export only long-form content
media-archivist --db-file db.json export --canonical --where "duration>1800" -o long_form.jsonl
```

---

### import

Load entries from an external JSON or JSONL file into the database.

```
media-archivist import PATH [--overwrite]
```

**Options:**

| Flag | Type | Description |
|------|------|-------------|
| PATH | path | `.json` (dict or list) or `.jsonl` input file (required) |
| `--db`, `--db-file` | string | Database target (required) |
| `--overwrite` | flag | Replace existing entries with the same URL |

**File Format:**

- `.json`: Object mapping URLs to entries, or list of entries (each with a `url` field)
- `.jsonl`: One JSON object per line, each with a `url` field

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
# Import from a JSON object
media-archivist --db-file db.json import backup.json

# Import from JSONL
media-archivist --db-file db.json import export.jsonl

# Import and overwrite duplicates
media-archivist --db-file db.json import new_data.json --overwrite
```

---

### merge

Merge multiple source database files into the destination database.

```
media-archivist merge SOURCE [SOURCE ...] [--overwrite]
```

**Options:**

| Flag | Type | Description |
|------|------|-------------|
| SOURCE | path | Source `.json` DB paths (required, at least one) |
| `--db`, `--db-file` | string | Destination database target (required) |
| `--overwrite` | flag | Replace existing entries with the same URL |

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
# Merge two databases
media-archivist --db-file combined.json merge db1.json db2.json

# Merge with overwrite
media-archivist --db-file prod.json merge staging1.json staging2.json --overwrite
```

---

### stats

Print dataset statistics (total entries, live status, per-playlist breakdown, field coverage).

```
media-archivist stats
```

**Options:**

| Flag | Description |
|------|-------------|
| `--db`, `--db-file` | Database target (required) |

**Output:**

JSON object with keys:
- `total` — Total number of entries
- `live` — Number of entries flagged as live
- `playlists` — Object mapping playlist name to count
- `field_coverage` — Object mapping field name to count of non-null entries

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
media-archivist --db-file db.json stats

# Output:
# {
#   "total": 250,
#   "live": 5,
#   "playlists": {
#     "My Playlist": 50,
#     "Another": 30
#   },
#   "field_coverage": {
#     "videoId": 250,
#     "title": 248,
#     "url": 250,
#     "duration": 200,
#     ...
#   }
# }
```

---

### prune

Remove entries by various criteria.

```
media-archivist prune [--unavailable] [--below MINUTES] [--missing FIELD ...]
```

**Options:**

| Flag | Type | Description |
|------|------|-------------|
| `--unavailable` | flag | Drop entries that no longer resolve (oEmbed probe for YouTube) |
| `--below MINUTES` | int | Drop entries shorter than MINUTES |
| `--missing FIELD` | string | Drop entries missing FIELD (repeatable) |
| `--blacklist KW` | string | (inherited) skip entries with this keyword in title |

**Validation:**

At least one of `--unavailable`, `--below`, `--missing`, or `--blacklist` must be specified.

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
# Remove videos that no longer exist
media-archivist --db-file db.json prune --unavailable

# Remove entries shorter than 10 minutes
media-archivist --db-file db.json prune --below 10

# Remove entries missing a title
media-archivist --db-file db.json prune --missing title

# Remove short clips and missing metadata
media-archivist --db-file db.json prune --below 5 --missing duration
```

---

### bootstrap

Seed an empty database from a remote JSON dump URL.

```
media-archivist bootstrap URL
```

**Options:**

| Flag | Type | Description |
|------|------|-------------|
| URL | string | Remote URL to a JSON file (required) |
| `--db`, `--db-file` | string | Database target (required) |

**Notes:**

Only supported by `YoutubeArchivist` and `YoutubeMonitor`; other backends return an error.

**Exit Codes:**

- `0` — Success
- `1` — Backend doesn't support bootstrap

**Examples:**

```bash
media-archivist --db-file db.json bootstrap https://example.com/archive.json
```

---

### link

Compute fingerprint groups and write the `<db>.links.json` sidecar.

```
media-archivist link [--duration-tolerance SECONDS]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--duration-tolerance SECONDS` | float | 2.0 | Seconds of duration mismatch tolerated within a fingerprint group |
| `--db`, `--db-file` | string | — | Database target (required) |

**Output:**

Writes `<db>.links.json` with structure:

```json
{
  "<fingerprint>": ["id1", "id2", ...],
  "<fingerprint>:1": ["id3", "id4", ...],
  ...
}
```

Entries in the same group (by fingerprint) are likely duplicates across sources.

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
media-archivist --db-file db.json link --duration-tolerance 2.0
```

---

### dedupe

Read view + links and emit a deduped canonical JSONL.

```
media-archivist dedupe --output PATH [--prefer A,B,C]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--output PATH` | path | — | Output JSONL path (required) |
| `--prefer A,B,C` | string | `bandcamp,internet_archive,youtube_music,soundcloud,youtube` | Comma-separated source preference order (winners first) |
| `--duration-tolerance SECONDS` | float | 2.0 | Duration tolerance for link validation |
| `--db`, `--db-file` | string | — | Database target (required) |

**Output:**

One canonical JSONL row per deduplicated group, with fields from the preferred source.

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
media-archivist --db-file db.json dedupe --output canonical.jsonl

# Custom preference order
media-archivist --db-file db.json dedupe --output canonical.jsonl \
  --prefer internet_archive,youtube_music,youtube
```

---

### providers

List built-in metadata providers and their active status.

```
media-archivist providers
```

**Options:**

None. This subcommand doesn't require a DB target.

**Output:**

JSON array with entries:

```json
[
  {
    "name": "musicbrainz",
    "active": true,
    "media": ["music"]
  },
  {
    "name": "tmdb",
    "active": false,
    "media": ["movie", "tv"]
  },
  ...
]
```

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
media-archivist providers
media-archivist providers | jq '.[] | select(.active)'
```

---

### canonicalize

Run metadata providers against the database and update canonical/quarantine sidecars.

```
media-archivist canonicalize [--providers NAME ...] [--no-stamp]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--providers NAME` | string | (all active) | Restrict to this provider (repeatable); if not specified, runs all active providers |
| `--no-stamp` | flag | off | Don't write `_meta.canonical_id` back to rows in the database |
| `--db`, `--db-file` | string | — | Database target (required) |

**Side Effects:**

- Writes/updates `<db>.canonical.json` with deduplicated records and external IDs
- Writes/updates `<db>.quarantine.json` with conflicting entries
- If `--no-stamp` is not set, stamps each row's `_meta.canonical_id` and `_meta.canonical_status`

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
# Run all active providers
media-archivist --db-file db.json canonicalize

# Run only MusicBrainz and Wikidata
media-archivist --db-file db.json canonicalize --providers musicbrainz --providers wikidata

# Run without modifying the source DB
media-archivist --db-file db.json canonicalize --no-stamp
```

---

### quarantine-list

Dump the quarantine sidecar as JSON.

```
media-archivist quarantine-list
```

**Options:**

| Flag | Description |
|------|-------------|
| `--db`, `--db-file` | Database target (required) |

**Output:**

JSON with structure:

```json
{
  "version": 1,
  "entries": {
    "<row_id>": {
      "row_id": "...",
      "candidate_canonical_id": "...",
      "conflicts": [
        {"signal": "title", "ours": "...", "theirs": "..."}
      ],
      "proposed_signals": {...},
      "first_seen": "2025-01-15T...",
      "last_seen": "2025-01-15T..."
    }
  }
}
```

**Exit Codes:**

- `0` — Success

**Examples:**

```bash
media-archivist --db-file db.json quarantine-list | jq '.entries | keys'
```

---

### quarantine-resolve

Accept a quarantined row and link it to a canonical record.

```
media-archivist quarantine-resolve --row-id ID [--canonical-id CANONICAL_ID]
```

**Options:**

| Flag | Type | Description |
|------|------|-------------|
| `--row-id ID` | string | Row ID to resolve (required) |
| `--canonical-id CANONICAL_ID` | string | Link to this existing canonical_id; if omitted, allocate new from proposed signals |
| `--db`, `--db-file` | string | Database target (required) |

**Side Effects:**

- Removes the entry from the quarantine sidecar
- Stamps the row with the canonical_id and status "matched"

**Exit Codes:**

- `0` — Success
- `1` — row_id not found in quarantine

**Examples:**

```bash
# Resolve to an existing canonical record
media-archivist --db-file db.json quarantine-resolve \
  --row-id abc123def456 \
  --canonical-id canonical_xyz

# Allocate a new canonical ID
media-archivist --db-file db.json quarantine-resolve --row-id abc123def456
```

---

### quarantine-reject

Reject a proposal and force a fresh canonical_id.

```
media-archivist quarantine-reject --row-id ID
```

**Options:**

| Flag | Type | Description |
|------|------|-------------|
| `--row-id ID` | string | Row ID to reject (required) |
| `--db`, `--db-file` | string | Database target (required) |

**Side Effects:**

- Removes the entry from the quarantine sidecar
- Allocates a fresh canonical_id and stamps the row with status "unmatched"

**Exit Codes:**

- `0` — Success
- `1` — row_id not found in quarantine

**Examples:**

```bash
media-archivist --db-file db.json quarantine-reject --row-id abc123def456
```

---

### monitor

Background-poll URLs and keep the database in sync.

```
media-archivist monitor URL [URL ...] [--interval SECONDS]
```

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| URL | string | — | Channel, playlist, or artist URLs to monitor (required, at least one) |
| `--interval SECONDS` | int | 120 | Seconds between syncs |
| `--db`, `--db-file` | string | — | Database target (required) |
| `--require`, `--blacklist`, `--min-duration` | — | — | Filters applied to each sync |

**Notes:**

- Not supported with `--ia` backend
- Runs indefinitely; press Ctrl-C to stop
- Logs sync events to stderr

**Exit Codes:**

- `0` — Success or Ctrl-C
- `1` — Backend error or validation error

**Examples:**

```bash
# Monitor a YouTube channel every 2 minutes
media-archivist --db-file db.json monitor https://www.youtube.com/@SomeChannel

# Monitor multiple playlists every 5 minutes
media-archivist --db-file music.json --music monitor \
  https://music.youtube.com/playlist?list=PLyyy \
  https://music.youtube.com/playlist?list=PLzzz \
  --interval 300

# Monitor with filters
media-archivist --db-file db.json monitor https://www.youtube.com/@Channel \
  --min-duration 600 --require "important keyword"
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Validation error, missing argument, or command not supported |
| 2 | File I/O error (e.g., bad output format) |

---

## Error Messages

Common errors and resolutions:

| Error | Cause | Resolution |
|-------|-------|-----------|
| `error: pass --db NAME or --db-file PATH` | No database target | Specify exactly one of `--db` or `--db-file` |
| `error: --where: <message>` | Invalid WHERE expression | Check syntax; see WHERE Language reference |
| `error: <backend> backend requires 'pip install <package>'` | Backend not installed | Install optional dependency |
| `error: row_id <id> not in quarantine` | Quarantine operation on non-existent row | Check the row_id against `quarantine-list` output |
