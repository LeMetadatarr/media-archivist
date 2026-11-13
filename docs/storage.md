# Storage format

media-archivist keeps two files per database. The JSON database
(`talks.json`) is the archive: the raw rows as each source gave them, plain
JSON that diffs well and is what gets exported and published. The working
index (`talks.index.sqlite3`, beside it) is what a running server uses to
answer queries and to remember its own state: which subscription lists which
video and since when, durations and upload dates for filtering, back-off
pauses after YouTube's bot check, settings, download and export records.

The working index is SQLite in WAL mode, so readers never wait for the
writer, and every write takes the write lock before it reads, so two
threads or two processes changing the same record never lose either change.
Its schema is described in the docstring of `media_archivist/indexdb.py` and
grows by numbered migration steps in `media_archivist/index_migrations/`,
each applied once and recorded in the index's `migrations` table.
The index must sit on a local filesystem: WAL mode shares memory between
the processes using the file, which NFS and CIFS mounts do not provide, so
keep the database on local disk or point the server at a copy that is.

The first time a server opens a database, the index is created and filled
from what the JSON database already knows: each row's duration, upload date,
channel and metadata timestamps, and the subscriptions sidecar. The JSON
files are only read. That import runs once; later starts find it recorded
and skip it.

Nothing in the working index is the only copy of a video's metadata. If the
file is lost or damaged, stop the server, move the `.index.sqlite3` file
(and its `-wal` and `-shm` companions) aside and start again: the index is
rebuilt from the JSON database, and a re-index of the subscriptions
restores the memberships and first-seen times the JSON does not hold.
Pauses, settings and download history start empty in that case.

## The JSON database

The JSON database is a plain JSON file validated by the
`media_archivist.models.MediaArchive` envelope.

### Envelope format

```json
{
  "_meta": {
    "schema_version": 2,
    "archivist_version": "0.1.0",
    "created": "2026-04-29T15:32:34+00:00",
    "last_synced": "2026-04-29T15:33:01+00:00",
    "source_mix": { "youtube": 137, "bandcamp": 42 }
  },
  "entries": {
    "https://www.youtube.com/watch?v=abc": { "source": "youtube", "url": "...", "videoId": "abc", "title": "..." },
    "https://x.bandcamp.com/track/y":      { "source": "bandcamp", "url": "...", "title": "..." }
  }
}
```

`_meta.source_mix` is recomputed on every store. Useful for `stats` and
dataset metadata.

### Storage classes

```python
from media_archivist.storage import (
    EnvelopeJsonStorage,        # explicit path
    EnvelopeJsonStorageXDG,     # auto-placed under ~/.local/share/media_archivist/
)
```

Both subclass the corresponding `json_database.JsonStorage*` and behave
like a `dict[str, dict]` in memory (URL → entry). The envelope is only
materialised on load and on `store()`.

`JsonArchivist.__init__` selects between them based on the `db_path` /
`db_name` constructor argument:

```python
from media_archivist import YoutubeArchivist

YoutubeArchivist(db_path="./talks.json")              # EnvelopeJsonStorage
YoutubeArchivist(db_name="talks")                     # EnvelopeJsonStorageXDG
```

### Diff-friendliness

The on-disk format is sorted-key indented JSON. Source-faithful raw rows
mean a `git diff` shows exactly what changed in the upstream metadata, no schema gymnastics.

### Crash safety

Every write of the envelope or a sidecar is atomic. The content is serialised
into a temporary file in the same directory, flushed to disk, and then
`os.replace`d onto the destination. Because `os.replace` is atomic on a single
filesystem, a crash at any instant leaves each file either old-complete or
new-complete — never truncated or half-written. Serialisation happens before
the destination is touched, so a payload that fails to serialise raises without
disturbing the existing file, and the temporary file is always removed on
failure. The same-directory temp file is required: `os.replace` must not cross
filesystem boundaries.

Canonicalization writes several files per run — the entity, canonical, and
quarantine sidecars plus the envelope. It commits them in a fixed order:
sidecars first, envelope last.

1. `.entities.json`
2. `.canonical.json`
3. `.quarantine.json`
4. the envelope (`_meta.canonical_id` / `_meta.canonical_status` stamps)

The sidecars are derivable annotations keyed by entry id; the envelope stamps
are the commit point. A crash before the envelope is stored leaves
stamped-but-richer sidecars that the next run simply overwrites. Because the
stamps land last, a crash can never leave envelope stamps pointing at sidecar
records that were never persisted.

### Concurrency

`json_database` provides per-file locking (`ComboLock`) on the
`<filename>.lock` sentinel under `$TMPDIR`. The envelope wrapper inherits
this lock. Concurrent `media-archivist` invocations against the same DB
file serialise on the lock.

---
[← Schema & Validation](schema.md) · [Home](index.md) · [Canonical View & Dedupe →](canonical.md)
