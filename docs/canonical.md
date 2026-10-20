# Canonical view, fingerprint & dedupe

`media_archivist` keeps **per-source raw rows** on disk (diff-stable,
source-faithful) and computes a **canonical `MediaEntry` view** on read.
This enables source-agnostic queries, cross-source dedup, and dataset
exports without rewriting stored data.

## Two-tier model

```
Disk (raw)                              In-memory (canonical view)
───────────────                         ─────────────────────────
Raw{Youtube,YoutubeMusic,...}Entry  ──▶  MediaEntry
                                         id, source, url, title, tags,
                                         artist?, album?, duration?,
                                         published?, thumbnail?,
                                         is_live, explicit, stream?,
                                         raw: {...source-specific...}
```

Adapters live at `media_archivist/views.py`. The dispatcher
`to_media_entry(raw)` picks the right one by `raw["source"]`.

## The `Index` SDK

```python
from media_archivist import Index

idx = Index("./talks.json")
print(len(idx), "entries")

for e in idx.view(where='artist=="Foo" and duration>180',
                  source="bandcamp", has_stream=True, limit=20):
    print(e.url, e.duration, e.stream)
```

`Index.view(...)` filters by:

| Filter         | Type                  | Notes |
| -------------- | --------------------- | ----- |
| `where`        | `str` (sandboxed expr) | See *--where* below. |
| `source`       | source string         | One of `youtube`, `youtube_music`, `bandcamp`, `soundcloud`, `internet_archive`. |
| `has_stream`   | `bool`                | Restrict to entries with / without a resolved stream URL. |
| `explicit`     | `bool`                | (YT-Music) Restrict to explicit-flagged tracks. |
| `grep`         | `str`                 | Substring match on the title. |
| `limit`        | `int`                 | Yield at most N entries. |

## `--where` expression language

Sandboxed Python-like syntax evaluated against the canonical entry.
Identifiers refer to entry fields. Ordering comparisons against `None`
fail closed (no `TypeError`).

| Allowed                                                                 | Denied |
| ----------------------------------------------------------------------- | --- |
| `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`                        | attribute access (`title.upper()`) |
| `and`, `or`, `not`                                                       | function calls except `len`, `lower`, `upper` |
| `+ - * / % //`                                                           | imports, list/dict comprehensions, lambdas |
| `len(...)`, `lower(...)`, `upper(...)`                                  | name lookups outside the entry's fields |

Examples:

```bash
media-archivist list  --db-file talks.json --canonical \
    --where 'duration>180 and source=="bandcamp"'

media-archivist urls  --db-file talks.json --canonical --has-stream

media-archivist export --db-file talks.json --canonical --format jsonl \
    --where 'len(tags)>0 and not explicit' -o filtered.jsonl
```

## Fingerprint & link

`media_archivist.canon` computes cross-source fingerprints from
`(normalized_artist, normalized_title)` (parens / `feat.` / punctuation
are stripped). Within a fingerprint group, candidates are clustered by
duration tolerance (default ±2 s) so live versions, remixes and edits
don't collapse together.

```bash
media-archivist link --db-file talks.json
# → writes talks.links.json next to the source file
```

The sidecar is a plain JSON map:

```json
{
  "<sha1>":   ["<id-bandcamp>", "<id-youtube_music>"],
  "<sha1>:1": ["<id-bandcamp-live>", "<id-soundcloud-live>"]
}
```

The source DB is **not modified**, `link` only writes the sidecar.

## Dedupe

```bash
media-archivist dedupe --db-file talks.json \
    --output canonical.jsonl \
    --prefer bandcamp,internet_archive,youtube_music,soundcloud,youtube
```

The output is a JSONL of `MediaEntry` rows. Within each fingerprint
group, the entry from the highest-ranked source wins. The discarded
entries are attached as `raw.alternates`. Singletons pass through
untouched.

## Default source preference

Controlled by `media_archivist.canon.DEFAULT_PREFERENCE`:

```python
("bandcamp", "internet_archive", "youtube_music", "soundcloud", "youtube")
```

The default favours sources that ship a direct stream URL (Bandcamp,
IA), then audio-quality-focused metadata (YT-Music), with general
YouTube as a last resort. Override with `--prefer` for any reason
(e.g. when training a model where you actually want the YouTube
audio track).

## When fingerprinting fails

- Rows missing either `artist` or `title` are skipped (no reliable key).
- Wildly different durations stay separate via the cluster step.
- Cover versions, remasters, etc. share a fingerprint *and* a duration, they will collapse. If that is undesirable for your dataset, run
  `dedupe` per-source (`--source youtube_music` etc.) instead of
  globally.

## Sidecar consistency

`canonicalize` writes the entity, canonical, and quarantine sidecars and the
envelope through atomic replaces, in a fixed sidecars-first / envelope-last
order (see [Crash safety](storage.md#crash-safety)). The envelope is stored
exactly once per run, after all three sidecars are on disk, so the
`_meta.canonical_id` stamps — the commit point — can never reference sidecar
records that failed to persist. A crash mid-run leaves at worst richer sidecars
than the envelope stamps, which the next run overwrites.

---
[← Storage Format](storage.md) · [Home](index.md) · [Disambiguation & External IDs →](disambiguation.md)
