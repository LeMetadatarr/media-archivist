# File Formats Reference

Complete documentation of on-disk JSON schemas used by `media_archivist`.

## Overview

Four JSON files work together:

1. **`<db>.json`** — Main database (envelope format)
2. **`<db>.links.json`** — Fingerprint groups for deduplication
3. **`<db>.canonical.json`** — Deduplicated records with external IDs
4. **`<db>.quarantine.json`** — Entries with conflicting signals

All files are UTF-8 encoded, pretty-printed with 2-space indentation (for manual inspection).

---

## 1. Main Database Envelope

**File:** `<db>.json` (e.g., `talks.json`, `music.json`)

**Top-level structure:**

```json
{
  "_meta": { ... },
  "entries": { ... }
}
```

### _meta Object (ArchiveMeta)

Archive-level metadata:

```json
{
  "_meta": {
    "schema_version": 2,
    "archivist_version": "0.1.0",
    "created": "2025-01-15T10:30:00+00:00",
    "last_synced": "2025-01-15T11:45:00+00:00",
    "source_mix": {
      "youtube": 150,
      "youtube_music": 75,
      "bandcamp": 25,
      "internet_archive": 10,
      "soundcloud": 5
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Current: 2 |
| `archivist_version` | string | Version of media_archivist that wrote this |
| `created` | string | ISO 8601 UTC timestamp when DB was created |
| `last_synced` | string or null | ISO 8601 UTC timestamp of last archive operation |
| `source_mix` | object | Counts by source (for quick stats) |

### entries Object

URL → raw entry mapping. Keys are URLs; values are backend-specific raw dicts.

```json
{
  "entries": {
    "https://www.youtube.com/watch?v=abc123": {
      "source": "youtube",
      "url": "https://www.youtube.com/watch?v=abc123",
      "videoId": "abc123",
      "title": "My Talk",
      "author": "Jane Doe",
      "published": "2025-01-15",
      "duration": 1800.0,
      "is_live": false,
      "views": "5000",
      "description": "A great talk",
      "thumbnail": "https://i.ytimg.com/...",
      "tags": ["conference", "tech"],
      "extra": {}
    },
    "https://music.youtube.com/watch?v=xyz789": {
      "source": "youtube_music",
      "url": "https://music.youtube.com/watch?v=xyz789",
      "videoId": "xyz789",
      "title": "Ambient Dream",
      "artist": "Atmospheric Sounds",
      "album": "Zenscape",
      "year": 2024,
      "duration": 310.5,
      "explicit": false,
      "video_type": "MUSIC_VIDEO",
      "audio_only": false,
      "music_video": true,
      "views": "12000",
      "playlist": null,
      "thumbnail": "https://lh3.googleusercontent.com/...",
      "tags": ["ambient", "electronic"],
      "extra": {}
    }
  }
}
```

### Full Example

```json
{
  "_meta": {
    "schema_version": 2,
    "archivist_version": "0.1.0",
    "created": "2025-01-01T00:00:00+00:00",
    "last_synced": "2025-01-15T10:30:00+00:00",
    "source_mix": {
      "youtube": 3,
      "youtube_music": 2
    }
  },
  "entries": {
    "https://www.youtube.com/watch?v=abc": {
      "source": "youtube",
      "url": "https://www.youtube.com/watch?v=abc",
      "videoId": "abc",
      "title": "Video 1",
      "author": "Author A",
      "published": "2025-01-01",
      "duration": 600.0,
      "is_live": false,
      "views": "100",
      "description": "",
      "thumbnail": null,
      "tags": [],
      "extra": {}
    },
    "https://music.youtube.com/watch?v=def": {
      "source": "youtube_music",
      "url": "https://music.youtube.com/watch?v=def",
      "videoId": "def",
      "title": "Song 1",
      "artist": "Artist B",
      "album": "Album C",
      "year": 2024,
      "duration": 180.0,
      "explicit": false,
      "video_type": "MUSIC_VIDEO",
      "audio_only": false,
      "music_video": true,
      "views": "1000",
      "playlist": null,
      "thumbnail": null,
      "tags": ["music"],
      "extra": {}
    }
  }
}
```

---

## 2. Links Sidecar

**File:** `<db>.links.json` (e.g., `talks.links.json`)

**Purpose:** Fingerprint groups for cross-source deduplication. Written by `link` subcommand.

**Top-level structure:**

```json
{
  "<fingerprint>": ["<id1>", "<id2>", ...],
  "<fingerprint>:1": ["<id3>", "<id4>", ...],
  ...
}
```

### Key Structure

- **Key:** SHA1 fingerprint (40-char hex string) or `<fingerprint>:<cluster>` for duration-split groups
- **Value:** List of entry IDs (stable_id from source:url)

### Example

```json
{
  "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b": [
    "abc123xyz",
    "def456uvw",
    "ghi789rst"
  ],
  "f9e8d7c6b5a4f9e8d7c6b5a4f9e8d7c6b5a4f9e8": [
    "jkl012opq",
    "mno345lmn"
  ],
  "e0d1c2b3a4f5e0d1c2b3a4f5e0d1c2b3a4f5e0d1:1": [
    "pqr678efg"
  ]
}
```

### Cluster Keys

When entries in the same fingerprint group have durations that disagree by more than `--duration-tolerance`:

1. The first cluster keeps the base fingerprint as key
2. Subsequent clusters are keyed `<fingerprint>:<n>` (n = 1, 2, 3, ...)

This prevents false positives when:
- A 3-minute version and a 5-minute version of the same song exist
- The fingerprint is identical (same artist+title normalization)
- But duration differs beyond tolerance

---

## 3. Canonical Sidecar

**File:** `<db>.canonical.json` (e.g., `talks.canonical.json`)

**Purpose:** Deduplicated records with consolidated signals and external IDs. Written by `canonicalize` subcommand.

**Top-level structure:**

```json
{
  "version": 1,
  "records": {
    "<canonical_id>": { ... },
    "<canonical_id>": { ... }
  }
}
```

### Record Object (CanonicalRecord)

```json
{
  "canonical_id": "canonical_abc123xyz",
  "signals": {
    "title": "Bohemian Rhapsody",
    "artist": "Queen",
    "year": 1975,
    "country": "GB",
    "runtime": 354.0,
    "medium": "music",
    "language": "en"
  },
  "members": [
    "youtube_id_1",
    "youtube_music_id_1",
    "bandcamp_id_1"
  ],
  "external_ids": {
    "musicbrainz_recording": "123e4567-e89b-12d3-a456-426614174000",
    "musicbrainz_release": "223e4567-e89b-12d3-a456-426614174001",
    "musicbrainz_artist": "323e4567-e89b-12d3-a456-426614174002",
    "wikidata": "Q14825049",
    "imdb": null,
    "tmdb_movie": null,
    "extra": {}
  },
  "provider_log": [
    {
      "provider": "musicbrainz",
      "matched_at": "2025-01-15T10:30:00+00:00",
      "confidence": 0.98
    },
    {
      "provider": "wikidata",
      "matched_at": "2025-01-15T10:30:05+00:00",
      "confidence": 0.85
    }
  ],
  "created": "2025-01-15T10:30:00+00:00",
  "last_updated": "2025-01-15T10:30:05+00:00"
}
```

### Full Example

```json
{
  "version": 1,
  "records": {
    "canonical_001": {
      "canonical_id": "canonical_001",
      "signals": {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "year": 1975,
        "country": "GB",
        "runtime": 354.0,
        "medium": "music",
        "language": "en"
      },
      "members": [
        "id_youtube_1",
        "id_youtube_music_1"
      ],
      "external_ids": {
        "musicbrainz_recording": "abc-123",
        "musicbrainz_release": "def-456",
        "musicbrainz_artist": "ghi-789",
        "wikidata": "Q14825049",
        "imdb": null,
        "tmdb_movie": null,
        "isbn_10": null,
        "isbn_13": null,
        "olid": null,
        "goodreads": null,
        "tvdb": null,
        "tmdb_tv": null,
        "extra": {}
      },
      "provider_log": [
        {
          "provider": "musicbrainz",
          "matched_at": "2025-01-15T10:30:00+00:00",
          "confidence": 0.98
        }
      ],
      "created": "2025-01-15T10:30:00+00:00",
      "last_updated": "2025-01-15T10:30:00+00:00"
    },
    "canonical_002": {
      "canonical_id": "canonical_002",
      "signals": {
        "title": "Stairway to Heaven",
        "artist": "Led Zeppelin",
        "year": 1971,
        "country": "GB",
        "runtime": 482.0,
        "medium": "music",
        "language": "en"
      },
      "members": [
        "id_youtube_2"
      ],
      "external_ids": {
        "musicbrainz_recording": "jkl-012",
        "musicbrainz_release": null,
        "musicbrainz_artist": "mno-345",
        "wikidata": null,
        "imdb": null,
        "extra": {}
      },
      "provider_log": [
        {
          "provider": "musicbrainz",
          "matched_at": "2025-01-15T10:31:00+00:00",
          "confidence": 0.95
        }
      ],
      "created": "2025-01-15T10:31:00+00:00",
      "last_updated": "2025-01-15T10:31:00+00:00"
    }
  }
}
```

---

## 4. Quarantine Sidecar

**File:** `<db>.quarantine.json` (e.g., `talks.quarantine.json`)

**Purpose:** Entries with conflicting signals pending manual review. Written by `canonicalize` subcommand.

**Top-level structure:**

```json
{
  "version": 1,
  "entries": {
    "<row_id>": { ... },
    "<row_id>": { ... }
  }
}
```

### Entry Object (QuarantineEntry)

```json
{
  "row_id": "abc123def456xyz789",
  "candidate_canonical_id": "canonical_xyz",
  "conflicts": [
    {
      "signal": "title",
      "ours": "Song Title (Original Mix)",
      "theirs": "Song Title"
    },
    {
      "signal": "runtime",
      "ours": 310.5,
      "theirs": 315.0
    }
  ],
  "proposed_signals": {
    "title": "Song Title",
    "artist": "Artist Name",
    "year": 2020,
    "country": null,
    "runtime": 312.5,
    "medium": "music",
    "language": null
  },
  "first_seen": "2025-01-15T10:30:00+00:00",
  "last_seen": "2025-01-15T10:30:00+00:00"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `row_id` | string | Stable entry ID (sha1 of source:url) |
| `candidate_canonical_id` | string or null | Proposed canonical_id if no conflicts (if conflicts exist, null) |
| `conflicts` | array | List of SignalConflict objects (disagreements between local and provider) |
| `proposed_signals` | object or null | Merged signals from providers (before conflict detection) |
| `first_seen` | string | ISO 8601 timestamp when entry entered quarantine |
| `last_seen` | string | ISO 8601 timestamp of last canonicalization attempt |

### Full Example

```json
{
  "version": 1,
  "entries": {
    "row_001": {
      "row_id": "row_001",
      "candidate_canonical_id": "canonical_abc",
      "conflicts": [],
      "proposed_signals": {
        "title": "Perfect Song",
        "artist": "Artist X",
        "year": 2022,
        "country": "US",
        "runtime": 240.0,
        "medium": "music",
        "language": "en"
      },
      "first_seen": "2025-01-15T10:30:00+00:00",
      "last_seen": "2025-01-15T10:30:00+00:00"
    },
    "row_002": {
      "row_id": "row_002",
      "candidate_canonical_id": null,
      "conflicts": [
        {
          "signal": "title",
          "ours": "Track Name (Extended Mix)",
          "theirs": "Track Name"
        },
        {
          "signal": "runtime",
          "ours": 450.0,
          "theirs": 420.0
        }
      ],
      "proposed_signals": {
        "title": "Track Name",
        "artist": "Artist Y",
        "year": 2023,
        "country": null,
        "runtime": 435.0,
        "medium": "music",
        "language": null
      },
      "first_seen": "2025-01-15T10:31:00+00:00",
      "last_seen": "2025-01-15T10:31:00+00:00"
    }
  }
}
```

### Resolving Quarantine Entries

To accept a quarantined entry:

```bash
# Link to an existing canonical record
media-archivist quarantine-resolve --row-id row_002 --canonical-id canonical_abc

# Or allocate a new canonical ID from proposed signals
media-archivist quarantine-resolve --row-id row_002
```

To reject and start fresh:

```bash
media-archivist quarantine-reject --row-id row_002
```

---

## Integration with Index

The `Index` class loads and uses these files:

```python
from media_archivist import Index

idx = Index("./talks.json")

# Loads talks.json
# Auto-loads talks.canonical.json if present
# Auto-loads talks.links.json if present

# View with canonical records
for entry in idx.view(canonical=True):
    print(entry.canonical_id, entry.external_ids)
```

---

## Compatibility

### Format upgrade

The envelope format (with `_meta` and `entries`) is the current standard. Earlier flat object mappings are auto-detected and upgraded by `MediaArchive.load_dict()`:

```python
# Flat format (auto-detected and upgraded)
{
  "https://example.com/video1": { ... },
  "https://example.com/video2": { ... }
}

# Envelope format (current)
{
  "_meta": { ... },
  "entries": {
    "https://example.com/video1": { ... },
    "https://example.com/video2": { ... }
  }
}
```

When saving, always uses the envelope format.

---

## Examples: Workflow

### 1. Archive a channel

```bash
media-archivist --db-file talks.json add https://www.youtube.com/@SomeChannel
```

Creates/updates `talks.json`:

```json
{
  "_meta": {
    "schema_version": 2,
    "archivist_version": "0.1.0",
    "created": "2025-01-15T...",
    "last_synced": "2025-01-15T...",
    "source_mix": { "youtube": 42 }
  },
  "entries": {
    "https://www.youtube.com/watch?v=abc": { ... },
    ...
  }
}
```

### 2. Link entries for deduplication

```bash
media-archivist --db-file talks.json link --duration-tolerance 2.0
```

Creates `talks.links.json`:

```json
{
  "a1b2c3...": ["id1", "id2"],
  "f9e8d7...": ["id3"]
}
```

### 3. Run providers and canonicalize

```bash
media-archivist --db-file talks.json canonicalize --providers musicbrainz,wikidata
```

Creates/updates `talks.canonical.json` and `talks.quarantine.json`.

Stamps each row's `_meta` field in the main database (if not --no-stamp).

### 4. Query the canonical view

```python
from media_archivist import Index

idx = Index("./talks.json")
for entry in idx.view(canonical=True):
    print(f"{entry.canonical_id}: {entry.title} by {entry.artist}")
    print(f"  External IDs: {entry.external_ids.model_dump()}")
```

---

## JSON Schema (Formal)

Raw entry discriminator:

```json
{
  "oneOf": [
    { "$ref": "#/definitions/RawYoutubeEntry" },
    { "$ref": "#/definitions/RawYoutubeMusicEntry" },
    { "$ref": "#/definitions/RawBandcampEntry" },
    { "$ref": "#/definitions/RawSoundcloudEntry" },
    { "$ref": "#/definitions/RawIAEntry" }
  ],
  "discriminator": {
    "propertyName": "source"
  }
}
```

For Pydantic models, use `.model_json_schema()`:

```python
from media_archivist.models.archive import MediaArchive
from media_archivist.models.canonical_record import CanonicalSidecar
from media_archivist.models.canonical_record import QuarantineSidecar

print(MediaArchive.model_json_schema())
print(CanonicalSidecar.model_json_schema())
print(QuarantineSidecar.model_json_schema())
```
