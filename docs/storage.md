# Storage format

DBs are plain JSON files validated by the
`media_archivist.models.MediaArchive` envelope.

## Envelope format

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

## Storage classes

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

## Diff-friendliness

The on-disk format is sorted-key indented JSON. Source-faithful raw rows
mean a `git diff` shows exactly what changed in the upstream metadata, no schema gymnastics.

## Crash safety

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

## Concurrency

`json_database` provides per-file locking (`ComboLock`) on the
`<filename>.lock` sentinel under `$TMPDIR`. The envelope wrapper inherits
this lock. Concurrent `media-archivist` invocations against the same DB
file serialise on the lock.

---
[← Schema & Validation](schema.md) · [Home](index.md) · [Canonical View & Dedupe →](canonical.md)
