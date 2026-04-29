# Reference Documentation

Complete technical reference for `media_archivist` — a Python library and CLI for indexing media metadata across YouTube, YouTube Music, Internet Archive, Bandcamp, and SoundCloud, with disambiguation against external databases.

## Quick Links

1. **[CLI Reference](cli.md)** — All subcommands, flags, exit codes, and examples
2. **[Python SDK Reference](sdk.md)** — Public classes, functions, and methods
3. **[Data Models](models.md)** — Pydantic model documentation with all fields
4. **[WHERE Query Language](where-language.md)** — Sandboxed expression syntax and examples
5. **[Metadata Providers](providers.md)** — Built-in provider configuration and APIs
6. **[File Formats](file-formats.md)** — On-disk JSON schemas with examples

## Overview

The `media_archivist` system has three main layers:

- **Archivists** — Backend-specific indexers (YouTube, YouTube Music, Internet Archive, Bandcamp, SoundCloud) that write raw entries to disk
- **Index** — Read-side SDK that loads the database and exposes filtering via sandboxed WHERE expressions
- **Canonicalization** — Cross-source deduplication, fingerprinting, and metadata enrichment via external providers (MusicBrainz, TMDB, Wikidata, Arr stack)

## Key Concepts

### Database Format

Databases are JSON files structured as an envelope:

```json
{
  "_meta": {
    "schema_version": 2,
    "archivist_version": "X.Y.Z",
    "created": "2025-01-15T10:30:00+00:00",
    "last_synced": "2025-01-15T10:30:00+00:00",
    "source_mix": {"youtube": 150, "youtube_music": 75}
  },
  "entries": {
    "URL": {
      "source": "youtube",
      "title": "...",
      ...
    }
  }
}
```

### Sidecars

Three optional JSON sidecars store metadata alongside the main database:

- **`<db>.links.json`** — Fingerprint groups for deduplication
- **`<db>.canonical.json`** — Canonical records (deduplicated, enriched with external IDs)
- **`<db>.quarantine.json`** — Entries with conflicting signals pending manual review

### Sources

Five sources are supported:

| Source | Via | Archivist | Rich Metadata |
|--------|-----|-----------|---------------|
| YouTube | tutubo | `YoutubeArchivist` | Title, duration, published date |
| YouTube Music | ytmusicapi | `YoutubeMusicArchivist` | Artist, album, year, explicit flag |
| Internet Archive | JSON API | `IAArchivist` | Title, streams, runtime |
| Bandcamp | py_bandcamp | `BandcampArchivist` | Artist, album, stream URL |
| SoundCloud | nuvem_de_som | `SoundCloudArchivist` | Artist, stream URL |

## Example Workflows

### Build a dataset from a YouTube channel

```bash
media-archivist --db-file my_talks.json add https://www.youtube.com/@SomeChannel
media-archivist --db-file my_talks.json export --format csv --fields videoId,title,url,published > talks.csv
```

### Find entries by duration and source

```bash
media-archivist --db-file db.json list --canonical --where "duration > 600 and source == 'youtube_music'"
```

### Deduplicate across sources

```bash
media-archivist --db-file db.json link --duration-tolerance 2.0
media-archivist --db-file db.json dedupe --output canonical.jsonl --prefer youtube_music,youtube
```

### Enrich with metadata providers

```bash
media-archivist --db-file db.json canonicalize --providers musicbrainz,tmdb
media-archivist --db-file db.json quarantine-list | head
```

## API Stability

All modules under `media_archivist/` are considered public API:

- `__init__.py` — Top-level imports (Index, Archivists, exceptions)
- `index.py` — Read-side Index class
- `canon.py` — Fingerprinting and linking functions
- `canonicalize.py` — Canonicalization orchestrator
- `models/` — All pydantic models
- `providers/` — Provider base class and registry

Internal modules (prefixed `_` or in `media_archivist/providers/`) may change between minor versions.
