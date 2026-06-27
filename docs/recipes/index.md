# Recipes — practical end-to-end workflows

Each recipe is a self-contained tutorial using real `media-archivist` subcommands and library calls. All recipes assume `media_archivist[all]` is installed.

## Available recipes

1. **[music-library-from-bandcamp-soundcloud-and-ytmusic.md](./music-library-from-bandcamp-soundcloud-and-ytmusic.md)** — Index the same artist on three sources, fingerprint-link, dedupe, export JSONL.

2. **[documentary-archive.md](./documentary-archive.md)** — Index a curated set of documentary YouTube channels into one DB, prune unwanted titles, export URL list, pipe to yt-dlp for offline viewing.

3. **[podcast-mirror.md](./podcast-mirror.md)** — Turn an Internet Archive collection into an M3U-friendly index.

4. **[dataset-for-ml.md](./dataset-for-ml.md)** — Produce a HuggingFace `datasets`-loadable JSONL with deterministic train/val/test splits via fingerprint-stable hashing.

5. **[cross-source-dedup-with-quarantine.md](./cross-source-dedup-with-quarantine.md)** — Full walk through the quarantine workflow: run canonicalize, inspect quarantine-list, resolve and reject rows, re-run canonicalize.

6. **[automate-with-cron.md](./automate-with-cron.md)** — Daily refresh via cron; how to make it re-entrant without re-scraping (link sidecar persistence).

## Quick start

All recipes assume you have installed the library:

```bash
pip install media_archivist[all]
```

Each recipe creates its own isolated database file(s). Example output files are prefixed with the recipe name (e.g., `recipe_music_library.json`).

## Conventions

- **Shell commands** are prefixed with `$` and assume a Bash shell.
- **Python code** blocks show library calls for when you need programmatic control.
- **Expected output** snippets show realistic data shapes to compare against your runs.
- **"What to do next"** sections suggest follow-up steps or further reading.

## Common patterns

### Using `--db-file` vs `--db`

```bash
# Explicit path (recommended for version-controlled datasets)
media-archivist add --db-file ./my_index.json https://...

# Auto-placed under ~/.local/share/media_archivist/ (good for temporary work)
media-archivist add --db my_index https://...
```

### Piping to yt-dlp

```bash
# Export URLs and pipe to yt-dlp for batch download
media-archivist urls --db-file my_index.json | yt-dlp -a - -o "%(title)s.%(ext)s"
```

### Exporting for downstream use

```bash
# JSONL (line-oriented, great for ML pipelines)
media-archivist export --db-file my_index.json --format jsonl -o export.jsonl

# CSV (spreadsheets, pandas)
media-archivist export --db-file my_index.json --format csv \
    --fields videoId,title,url,duration -o export.csv

# Plain text (just URLs)
media-archivist export --db-file my_index.json --format txt -o urls.txt
```

### Filtering with `--grep` and `--where`

```bash
# Substring match on title
media-archivist list --db-file my_index.json --grep "documentary"

# Sandboxed expression
media-archivist list --db-file my_index.json --canonical \
    --where 'duration>600 and source=="youtube_music"'
```

### Canonical view and deduplication

```bash
# Link cross-source fingerprints
media-archivist link --db-file my_index.json
# → writes my_index.links.json (sidecar)

# Dedupe to a canonical JSONL
media-archivist dedupe --db-file my_index.json \
    --output canonical.jsonl \
    --prefer bandcamp,internet_archive,youtube_music
```

## Working with providers

```bash
# Check which providers are active in your environment
media-archivist providers

# Run disambiguation against all active providers
media-archivist canonicalize --db-file my_index.json

# Or specify a subset
media-archivist canonicalize --db-file my_index.json \
    --providers wikidata --providers musicbrainz

# Inspect quarantine
media-archivist quarantine-list --db-file my_index.json

# Resolve a quarantined row (accept the provider's suggestion)
media-archivist quarantine-resolve --db-file my_index.json --row-id <id>

# Reject (force a new canonical_id)
media-archivist quarantine-reject --db-file my_index.json --row-id <id>
```

## Further reading

- [README.md](../../README.md) — user-facing installation and basic usage.
- [CLI architecture](../cli.md) — all subcommands and validation rules.
- [Canonical view & dedupe](../canonical.md) — fingerprinting, `--where` syntax.
- [Disambiguation & external IDs](../disambiguation.md) — providers, quarantine workflow.
- [`examples/`](../../examples/) — Python library examples (cross_source_dataset.py, canonicalize_movies.py, hf_dataset.py).
