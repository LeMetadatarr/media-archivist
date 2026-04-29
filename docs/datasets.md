# Datasets, enrichment, snapshots & sharing (v0.6)

This page documents the dataset-publishing surface added in v0.6:

- **Enrichment**: `media-archivist enrich` augments rows with derived
  fields under `_meta.enriched.*` (lyrics, transcripts, content-type).
- **Splits**: `export --split` / `--split-by` emit deterministic
  train/val/test or per-source files.
- **Snapshots**: `media-archivist snapshot` makes a dated copy of the
  DB; `diff` compares two snapshots structurally.
- **HuggingFace publishing**: `media-archivist hub-publish` pushes a
  JSONL + auto-generated dataset card to the Hub.

For end-to-end recipes see [`recipes/dataset-for-ml.md`](./recipes/dataset-for-ml.md).

## Enrichment

```bash
media-archivist enrich --db-file talks.json --transcripts --content-type
media-archivist enrich --db-file songs.json --bandcamp --lyrics
```

Each enricher only fires when the row's `source` is compatible:

| Enricher       | Applies to                | Backend dependency       |
| -------------- | ------------------------- | ------------------------ |
| `--lyrics`     | `bandcamp`                | `py_bandcamp`            |
| `--transcripts`| `youtube`, `youtube_music`| `yt-dlp` on PATH         |
| `--content-type`| `youtube`, `youtube_music`| `tutubo.content_type`   |

Results land under `_meta.enriched.{lyrics,transcript,content_type}`,
validated by the `EnrichedBlock` pydantic model
(`media_archivist/models/enriched.py`). The raw row is otherwise
untouched.

Re-run with `--overwrite` to refresh existing blocks; without it,
enrichers skip rows whose block is already populated. `--limit N`
caps the number of rows processed (handy when iterating).

## Deterministic splits

```bash
# 80/10/10 train/val/test by canonical_id (or row id, falling back to URL)
media-archivist export --db-file songs.json --canonical --format jsonl \
    --split 'train:0.8,val:0.1,test:0.1' -o songs.jsonl
# → emits songs.train.jsonl, songs.val.jsonl, songs.test.jsonl

# One file per distinct value of a field
media-archivist export --db-file songs.json --canonical --format jsonl \
    --split-by source -o per_source.jsonl
# → emits per_source.bandcamp.jsonl, per_source.youtube_music.jsonl, …
```

`--split` keys on `canonical_id` when present (so all variants of a
work end up in the same split — no leakage across sources), then on
the row id, then on the URL. Re-running on the same DB always yields
identical bucketing, which keeps train/eval splits stable across
re-fetches.

## Snapshots and diffs

```bash
# Stamp a snapshot under <db_dir>/.snapshots/<utc-timestamp>[-label].json
media-archivist snapshot --db-file talks.json --label pre-prune

# Compare two DB files; ignores volatile _meta fields
media-archivist diff a.json b.json
```

`diff` returns `{added, removed, changed}` URL lists. Volatile
metadata (`last_synced`, etc.) is ignored — only structural changes
to the entry payload count as "changed".

## HuggingFace Hub publishing

```bash
pip install media_archivist[hub]   # installs huggingface_hub
huggingface-cli login

media-archivist hub-publish --db-file talks.json \
    --jsonl talks.jsonl \
    --repo your-handle/talks-dataset \
    --description "Curated talks scraped from public channels."
```

The publisher:

1. Reads `<db>.canonical.json` (if present) for the `canonical_records`
   / `quarantined` counts.
2. Uses the envelope's `source_mix` for the per-source breakdown.
3. Looks up per-source default-license blurbs from
   `media_archivist.models.dataset_card.DEFAULT_LICENSES` and embeds
   them in the card.
4. Writes a `README.md` next to the JSONL and uploads both files to
   the Hub via `huggingface_hub.HfApi.upload_file`.

The card body is itself a pydantic `DatasetCard` (`name`,
`description`, `license`, `languages`, `tags`, plus the auto-derived
fields). To preview before pushing:

```python
from media_archivist.hub import build_card
print(build_card("talks.json", name="talks-dataset",
                 description="...").to_markdown())
```

## Pairing with v0.3.5

The `--canonical` flag on `export` switches the row shape from raw to
the canonical `MediaEntry` view, so the JSONL carries `canonical_id`
and `external_ids` (if you ran `media-archivist canonicalize` first).
Splits then key on `canonical_id` automatically — different sources
of the same work always land in the same split.

## Verification

- `pytest test/test_v06.py` covers `EnrichedBlock` round-trip, dataset
  card rendering, deterministic split bucketing, snapshot, and diff
  classification (8 tests, fully offline).
- `media-archivist export ... --split 'train:0.8,val:0.1,test:0.1'`
  on the same DB yields identical files between runs.
- `media-archivist snapshot && media-archivist diff <a> <b>` returns
  empty lists when nothing changed and the expected sets after
  `prune` / `add`.
