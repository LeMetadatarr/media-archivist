# Data Models Reference

Complete documentation of all Pydantic models in `media_archivist`.

## Source Enum

```python
from media_archivist.models.raw import Source

class Source(str, Enum):
    """Enumeration of supported media sources."""
    YOUTUBE = "youtube"
    YOUTUBE_MUSIC = "youtube_music"
    BANDCAMP = "bandcamp"
    SOUNDCLOUD = "soundcloud"
    INTERNET_ARCHIVE = "internet_archive"
```

---

## Raw Entry Models

Raw models mirror what each archivist writes. They are **strict on read** (reject unknown fields in tests) but **lenient on construction** (defaults for optional fields).

### RawYoutubeEntry

YouTube channel/playlist/video entries.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | Source | YOUTUBE | Discriminator |
| `url` | str | — | Video URL (watch?v= or youtu.be form) |
| `videoId` | str | — | YouTube video ID |
| `title` | Optional[str] | None | Video title |
| `author` | Optional[str] | None | Channel author name |
| `published` | str | "" | Publish date (ISO 8601 if available) |
| `duration` | Optional[float] | None | Duration in seconds (from search previews) |
| `is_live` | bool | False | True if video is a live stream |
| `views` | str | "" | View count (string, raw format) |
| `description` | str | "" | Video description |
| `playlist` | Optional[str] | None | Playlist name if archived as part of a playlist |
| `thumbnail` | Optional[str] | None | Thumbnail URL |
| `tags` | List[str] | [] | Video tags/keywords |
| `extra` | dict | {} | Free-form metadata |

### RawYoutubeMusicEntry

YouTube Music track/album/artist entries.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | Source | YOUTUBE_MUSIC | Discriminator |
| `url` | str | — | Track/video URL |
| `videoId` | str | — | YouTube video ID |
| `title` | Optional[str] | None | Track title |
| `artist` | Optional[str] | None | Artist name |
| `album` | str | "" | Album name |
| `year` | Optional[int] | None | Release year |
| `duration` | Optional[float] | None | Duration in seconds |
| `explicit` | bool | False | True if track is flagged explicit |
| `video_type` | str | "" | Type: MUSIC_VIDEO, OFFICIAL_ARTIST_CHANNEL, etc. |
| `audio_only` | bool | False | True if audio-only (no video) |
| `music_video` | bool | False | True if a music video |
| `views` | str | "" | View count (string) |
| `playlist` | Optional[str] | None | Playlist name |
| `playlist_id` | Optional[str] | None | YouTube Music playlist ID |
| `album_browse_id` | Optional[str] | None | Album browse ID (MPRE...) |
| `artist_browse_id` | Optional[str] | None | Artist browse ID (UC...) |
| `label` | Optional[str] | None | Record label |
| `thumbnail` | Optional[str] | None | Thumbnail URL |
| `tags` | List[str] | [] | Tags |
| `extra` | dict | {} | Free-form metadata |

### RawBandcampEntry

Bandcamp track/album entries.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | Source | BANDCAMP | Discriminator |
| `url` | str | — | Track or album URL |
| `title` | Optional[str] | None | Track/album title |
| `artist` | Optional[str] | None | Artist name |
| `album` | str | "" | Album name |
| `album_url` | Optional[str] | None | Album page URL |
| `track_number` | Optional[int] | None | Track number within album |
| `duration` | Optional[float] | None | Duration in seconds (float) |
| `stream` | Optional[str] | None | Direct stream URL (when available) |
| `artwork` | Optional[str] | None | Artwork image URL |
| `thumbnail` | Optional[str] | None | Thumbnail URL |
| `tags` | List[str] | [] | Tags |
| `extra` | dict | {} | Free-form metadata |

### RawSoundcloudEntry

SoundCloud track entries.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | Source | SOUNDCLOUD | Discriminator |
| `url` | str | — | Track URL |
| `title` | Optional[str] | None | Track title |
| `artist` | Optional[str] | None | Artist name |
| `artist_url` | Optional[str] | None | Artist profile URL |
| `duration` | Optional[float] | None | Duration in seconds (converted from ms) |
| `stream` | Optional[str] | None | Direct stream URL |
| `source_query` | Optional[str] | None | Original search query (if from search) |
| `source_url` | Optional[str] | None | Source URL for the track |
| `thumbnail` | Optional[str] | None | Thumbnail URL |
| `tags` | List[str] | [] | Tags |
| `extra` | dict | {} | Free-form metadata |

### RawIAEntry

Internet Archive item entries.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | Source | INTERNET_ARCHIVE | Discriminator |
| `url` | str | — | Internet Archive item URL (archive.org/details/...) |
| `title` | Optional[str] | None | Item title |
| `collection` | Union[str, List[str]] | "" | Collection(s) the item belongs to |
| `duration` | Optional[Union[str, float]] | None | Runtime (IA returns "HH:MM:SS" string; can be float) |
| `streams` | List[str] | [] | List of stream URLs |
| `images` | List[str] | [] | List of image URLs |
| `thumbnail` | Optional[str] | None | Thumbnail URL |
| `tags` | List[str] | [] | Tags |
| `extra` | dict | {} | Free-form metadata |

### RawEntry (Discriminated Union)

```python
RawEntry = Annotated[
    Union[
        RawYoutubeEntry,
        RawYoutubeMusicEntry,
        RawBandcampEntry,
        RawSoundcloudEntry,
        RawIAEntry,
    ],
    Field(discriminator="source"),
]

def parse_raw(data: dict) -> _RawEntryBase:
    """Validate a raw dict into the right Raw*Entry subclass.
    
    Args:
        data: Dict with required 'source' field.
    
    Returns:
        Validated Raw*Entry instance.
    
    Raises:
        ValidationError: If validation fails.
    """
```

---

## Canonical View Model

### MediaEntry

Unified cross-source view of a single media work.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | str | — | Stable entry ID (sha1 of "source:url") |
| `source` | Source | — | Which source (youtube, youtube_music, bandcamp, soundcloud, internet_archive) |
| `url` | str | — | The media URL |
| `title` | str | — | Media title (required, non-empty) |
| `artist` | Optional[str] | None | Artist/creator name |
| `album` | Optional[str] | None | Album/collection name |
| `duration` | Optional[float] | None | Duration in seconds |
| `published` | Optional[str] | None | Publish/release date (ISO 8601 if derivable) |
| `thumbnail` | Optional[str] | None | Thumbnail image URL |
| `tags` | List[str] | [] | Keywords/tags |
| `is_live` | bool | False | True if live stream |
| `explicit` | bool | False | True if flagged explicit |
| `stream` | Optional[str] | None | Direct playback stream URL |
| `canonical_id` | Optional[str] | None | Deduplication ID (from canonical sidecar) |
| `canonical_status` | Optional[CanonicalStatus] | None | "matched", "quarantined", or "unmatched" |
| `external_ids` | ExternalIds | {} | Cross-reference IDs (MusicBrainz, TMDB, etc.) |
| `raw` | Dict[str, Any] | {} | The original raw entry dict |

**Methods:**

```python
@classmethod
def build(
    cls,
    *,
    source: Source,
    url: str,
    title: Optional[str],
    raw: Dict[str, Any],
    **fields,
) -> "MediaEntry":
    """Construct a MediaEntry with id and tags pre-filled.
    
    Args:
        source: Source enum value.
        url: Media URL.
        title: Media title (can be None; coerced to empty string).
        raw: Original raw entry dict.
        **fields: Additional field values (artist, album, duration, etc.).
    
    Returns:
        MediaEntry instance with id computed from source:url.
    """
```

**Type Aliases:**

```python
CanonicalStatus = Literal["matched", "quarantined", "unmatched"]
```

---

## Signals and Comparison Models

### Signals

Bag of normalized facts for cross-source comparison.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | Optional[str] | None | Normalized media title |
| `artist` | Optional[str] | None | Artist name (for music/podcast) or director (for video) |
| `year` | Optional[int] | None | Release/publication year |
| `country` | Optional[str] | None | Country code (ISO 3166-1 alpha-2) |
| `runtime` | Optional[float] | None | Duration in seconds |
| `medium` | Optional[Medium] | None | Media type (movie, tv, music, book, podcast, other) |
| `language` | Optional[str] | None | Language code (ISO 639-1) |

**Comparison Rules:**

- A signal absent on either side is **not** a disagreement
- All overlapping signals must agree for a match
- Any single overlapping signal disagrees → conflict (quarantine)

**Comparison Tolerances (configurable):**

```python
TITLE_FUZZY_MIN = 0.92        # fuzzy string match threshold
ARTIST_FUZZY_MIN = 0.90       # fuzzy string match threshold
YEAR_TOLERANCE = 1            # years
RUNTIME_TOLERANCE_S = 5.0     # seconds
```

### Medium (Enum)

```python
class Medium(str, Enum):
    MOVIE = "movie"
    TV = "tv"
    MUSIC = "music"
    BOOK = "book"
    PODCAST = "podcast"
    OTHER = "other"
```

### SignalConflict

Single-field disagreement between two Signals bags.

| Field | Type | Description |
|-------|------|-------------|
| `signal` | str | Field name (title, artist, year, country, runtime, medium, language) |
| `ours` | Any | Local value |
| `theirs` | Any | Provider/comparison value |

**Functions:**

```python
def compare(ours: Signals, theirs: Signals) -> List[SignalConflict]:
    """Return list of overlapping signals that disagree.
    
    Empty list ⇒ matched (no overlap counts as agreement).
    
    Args:
        ours: Local signals.
        theirs: Provider/comparison signals.
    
    Returns:
        List of SignalConflict objects (empty if all agree).
    """

def merged(*bags: Signals) -> Signals:
    """Merge multiple Signals bags (first non-None value wins per field).
    
    Args:
        *bags: Variable number of Signals objects.
    
    Returns:
        Merged Signals (first non-None value per field).
    """

def fuzzy_ratio(a: str, b: str) -> float:
    """Compute fuzzy string match ratio (0.0 to 1.0).
    
    Uses SequenceMatcher on normalized text.
    """
```

---

## External IDs Model

### ExternalIds

Cross-reference IDs from authoritative external databases.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `musicbrainz_recording` | Optional[str] | None | MusicBrainz recording MBID |
| `musicbrainz_release` | Optional[str] | None | MusicBrainz release MBID |
| `musicbrainz_release_group` | Optional[str] | None | MusicBrainz release group MBID |
| `musicbrainz_work` | Optional[str] | None | MusicBrainz work MBID |
| `musicbrainz_artist` | Optional[str] | None | MusicBrainz artist MBID |
| `imdb` | Optional[str] | None | IMDb ID (tt-format) |
| `tmdb_movie` | Optional[int] | None | TMDB movie ID |
| `tmdb_tv` | Optional[int] | None | TMDB TV series ID |
| `tvdb` | Optional[int] | None | TVDB series ID |
| `isbn_10` | Optional[str] | None | ISBN-10 |
| `isbn_13` | Optional[str] | None | ISBN-13 |
| `olid` | Optional[str] | None | Open Library ID |
| `goodreads` | Optional[str] | None | Goodreads book ID |
| `wikidata` | Optional[str] | None | Wikidata Q-ID |
| `extra` | Dict[str, str] | {} | Arbitrary provider-specific IDs |

**Methods:**

```python
def merge(self, other: "ExternalIds") -> "ExternalIds":
    """Merge another ExternalIds (non-None values from other override)."""

def is_empty(self) -> bool:
    """Return True if all fields are None/empty."""
```

---

## Canonical Records (Sidecar Models)

### CanonicalRecord

One entry per *work* in the `<db>.canonical.json` sidecar.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `canonical_id` | str | — | Unique ID for this deduplicated work |
| `signals` | Signals | — | Consolidated signals from all members |
| `members` | List[str] | [] | Entry IDs belonging to this work |
| `external_ids` | ExternalIds | {} | Cross-reference IDs from providers |
| `provider_log` | List[ProviderHit] | [] | Provider matches that informed this record |
| `created` | str | (UTC now) | ISO 8601 timestamp |
| `last_updated` | str | (UTC now) | ISO 8601 timestamp |

**Methods:**

```python
def touch(self) -> None:
    """Update last_updated to current UTC time."""
```

### ProviderHit

Record of a provider match.

| Field | Type | Description |
|-------|------|-------------|
| `provider` | str | Provider name (musicbrainz, tmdb, etc.) |
| `matched_at` | str | ISO 8601 timestamp |
| `confidence` | float | Confidence score (0.0 to 1.0) |

### CanonicalSidecar

Top-level shape of `<db>.canonical.json`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | 1 | Schema version |
| `records` | Dict[str, CanonicalRecord] | {} | Map of canonical_id → CanonicalRecord |

---

## Quarantine Models (Sidecar)

### QuarantineEntry

One entry per quarantined row in `<db>.quarantine.json`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `row_id` | str | — | Entry ID (stable_id of source:url) |
| `candidate_canonical_id` | Optional[str] | None | Proposed canonical_id if no conflicts |
| `conflicts` | List[SignalConflict] | [] | List of signal disagreements |
| `proposed_signals` | Optional[Signals] | None | Proposed merged signals |
| `first_seen` | str | (UTC now) | ISO 8601 timestamp |
| `last_seen` | str | (UTC now) | ISO 8601 timestamp |

### QuarantineSidecar

Top-level shape of `<db>.quarantine.json`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | 1 | Schema version |
| `entries` | Dict[str, QuarantineEntry] | {} | Map of row_id → QuarantineEntry |

---

## Archive Envelope Models

### ArchiveMeta

Archive-level metadata.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `schema_version` | int | 2 | Envelope schema version |
| `archivist_version` | str | (package __version__) | Version of media_archivist that created/last wrote this |
| `created` | str | (UTC now) | ISO 8601 creation timestamp |
| `last_synced` | Optional[str] | None | ISO 8601 timestamp of last sync |
| `source_mix` | Dict[str, int] | {} | Counts by source (youtube: 150, bandcamp: 75, ...) |

**Extra fields allowed** (for custom metadata).

**Methods:**

```python
def touch(self) -> None:
    """Update last_synced to current UTC time."""
```

### MediaArchive

Validated wrapper around on-disk JSON envelope.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `meta` | ArchiveMeta | (new) | Archive metadata (aliased as `_meta` on disk) |
| `entries` | Dict[str, Dict[str, Any]] | {} | URL → raw entry dict mapping |

**On-disk format:**

```json
{
  "_meta": {...},
  "entries": {...}
}
```

**Backwards compatibility:**

Accepts legacy format (bare `{url: entry, ...}`) and auto-upgrades.

**Methods:**

```python
@classmethod
def load_dict(cls, data: Dict[str, Any]) -> "MediaArchive":
    """Load and validate an envelope dict."""

def dump_dict(self) -> Dict[str, Any]:
    """Serialize to dict (with _meta alias)."""

def touch(self) -> None:
    """Update last_synced."""

def recompute_source_mix(self) -> None:
    """Recalculate source counts from entries."""
```

---

## Provider Match Model

### ProviderMatch

One provider's response: what they say the work is.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | str | — | Provider name |
| `confidence` | float | — | Confidence score (0.0 to 1.0, validated) |
| `signals` | Signals | {} | Signals from the provider |
| `external_ids` | ExternalIds | {} | External IDs the provider returned |

---

## Helper Functions

### stable_id

```python
def stable_id(source: Source, url: str) -> str:
    """Compute deterministic entry ID from source:url.
    
    Returns:
        SHA1 hex string (40 characters).
    """
```

### JSON Examples

#### Raw Entry (YouTube)

```json
{
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
  "thumbnail": "https://...",
  "tags": ["conference", "tech"],
  "extra": {}
}
```

#### MediaEntry (Canonical View)

```json
{
  "id": "abc123def456...",
  "source": "youtube",
  "url": "https://www.youtube.com/watch?v=abc123",
  "title": "My Talk",
  "artist": "Jane Doe",
  "album": null,
  "duration": 1800.0,
  "published": "2025-01-15",
  "thumbnail": "https://...",
  "tags": ["conference", "tech"],
  "is_live": false,
  "explicit": false,
  "stream": null,
  "canonical_id": "canonical_xyz",
  "canonical_status": "matched",
  "external_ids": {
    "wikidata": "Q123456",
    "imdb": null,
    "extra": {}
  },
  "raw": {...}
}
```

#### CanonicalRecord

```json
{
  "canonical_id": "canonical_xyz",
  "signals": {
    "title": "My Talk",
    "artist": "Jane Doe",
    "year": 2025,
    "country": "US",
    "runtime": 1800.0,
    "medium": "other",
    "language": "en"
  },
  "members": ["id1", "id2"],
  "external_ids": {
    "wikidata": "Q123456",
    "imdb": null,
    "extra": {}
  },
  "provider_log": [
    {
      "provider": "wikidata",
      "matched_at": "2025-01-15T10:30:00+00:00",
      "confidence": 0.95
    }
  ],
  "created": "2025-01-15T10:30:00+00:00",
  "last_updated": "2025-01-15T10:30:00+00:00"
}
```
