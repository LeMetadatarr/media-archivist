# Storage format

DBs are plain JSON files validated by the
`media_archivist.models.MediaArchive` envelope.

## v0.2 envelope

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

`_meta.source_mix` is recomputed on every store; useful for `stats` and
for dataset cards (v0.6).

## Legacy bare mapping

Files written by `youtube_archivist` 0.0.x are flat `{url: entry, …}`
mappings without an `_meta` block. They are loaded transparently and
**rewritten as the v0.2 envelope on the next `store()`**. No manual
migration step.

```python
from media_archivist.storage import EnvelopeJsonStorage

db = EnvelopeJsonStorage("./old.json")   # legacy file
db.store()                               # written back as envelope
```

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
mean a `git diff` shows exactly what changed in the upstream metadata —
no schema gymnastics.

## Concurrency

`json_database` provides per-file locking (`ComboLock`) on the
`<filename>.lock` sentinel under `$TMPDIR`. The envelope wrapper inherits
this lock; concurrent `media-archivist` invocations against the same DB
file serialise on the lock.
