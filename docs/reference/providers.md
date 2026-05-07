# Metadata Providers Reference

Complete documentation of all built-in metadata providers and the provider system.

## Overview

Metadata providers are external databases that enrich entries with authoritative metadata and external IDs. They are used during canonicalization to:

1. Validate that two entries describe the same work (by comparing signals)
2. Extract authoritative external IDs (MusicBrainz, TMDB, IMDb, etc.)
3. Fill in missing metadata fields (year, runtime, country, language)

All providers are opt-in and disabled if their configuration is missing.

## Built-In Providers

All ~22 built-in providers live in `metadatarr` and self-register on import.
For the full table — including AniList, Jikan, LibriVox, Apple Podcasts,
Discogs, Blu-ray.com, DVDCompare, OpenLibrary, Anna's Archive, Bandcamp,
SoundCloud, YouTube/YT Music, Metal Archives, AudioDB, TVMaze, and the *arr
family — see [metadatarr resolver integration](../metadatarr.md).

The sections below document the most commonly used providers in detail.

| Provider | Media Types | Config | Rate Limit | Availability |
|----------|-------------|--------|-----------|--------------|
| MusicBrainz (`musicbrainz`) | Music | None (free) | 1 req/s | Always |
| Wikidata (`wikidata`) | All | None (free) | ~1000 req/hour | Always |
| Skyhook (`skyhook`) | Movie, TV, Music, Book | None (free Servarr proxies) | moderate | Always |
| Sonarr (`arr_sonarr`) | EPISODIC_SERIES | `MEDIA_ARCHIVIST_SONARR_URL` + `MEDIA_ARCHIVIST_SONARR_KEY` | Sonarr instance | If both set |
| Radarr (`arr_radarr`) | Movie | `MEDIA_ARCHIVIST_RADARR_URL` + `MEDIA_ARCHIVIST_RADARR_KEY` | Radarr instance | If both set |
| Readarr (`arr_readarr`) | Book | `MEDIA_ARCHIVIST_READARR_URL` + `MEDIA_ARCHIVIST_READARR_KEY` | Readarr instance | If both set |
| Lidarr (`arr_lidarr`) | Music | `MEDIA_ARCHIVIST_LIDARR_URL` + `MEDIA_ARCHIVIST_LIDARR_KEY` | Lidarr instance | If both set |

Env-var names for the *arr providers: `metadatarr/resolve/providers/arr.py:75`.

---

## MusicBrainz

**Name:** `musicbrainz`

**Media Types:** Music

**Configuration:** None (free, no API key)

**Rate Limit:** 1 request per second (built into library)

**Endpoint:** `https://musicbrainz.org/ws/2/recording`

### Signals Accepted

- `title` (required)
- `artist` (required)
- `medium` (optional; must be Music)

### External IDs Returned

- `musicbrainz_recording` — Recording MBID
- `musicbrainz_release` — Release MBID (if matched release found)
- `musicbrainz_artist` — Artist MBID

### Confidence Scoring

MusicBrainz returns a score (0–100) which is normalized to 0.0–1.0:

```
confidence = score / 100.0
```

### Lookup Logic

1. Search for recordings matching `recording:"<title>" AND artist:"<artist>"`
2. Returns top result sorted by MusicBrainz score
3. Extracts artist name, MBID, release info (country, date), runtime
4. Parses runtime from milliseconds to seconds

### Example

```python
from media_archivist.providers import all_providers

mb = all_providers()["musicbrainz"]
match = mb.lookup(Signals(title="Bohemian Rhapsody", artist="Queen", medium=MediaType.MUSIC))
# Returns: ProviderMatch(
#   provider="musicbrainz",
#   confidence=0.95,
#   signals=Signals(title="Bohemian Rhapsody", artist="Queen", runtime=354.0, medium=MediaType.MUSIC),
#   external_ids=ExternalIds(
#       musicbrainz_recording="...",
#       musicbrainz_release="...",
#       musicbrainz_artist="..."
#   )
# )
```

### Error Handling

Network errors are logged and return `None` (no match). The provider is always available.

---

## Wikidata

**Name:** `wikidata`

**Media Types:** Movie, TV, Music, Book, Podcast

**Configuration:** None (free, no API key)

**Rate Limit:** Wikidata API limits (~1000 req/hour)

**Endpoints:**
- `https://www.wikidata.org/w/api.php` (search and entity lookup)

### Signals Accepted

- `title` (required)
- `language` (optional; passed to search API)

### External IDs Returned

Wikidata returns cross-references to:

| Property | Field | Examples |
|----------|-------|----------|
| P345 | `imdb` | IMDb ID |
| P4947 | `tmdb_movie` | TMDB movie ID |
| P4983 | `tmdb_tv` | TMDB TV series ID |
| P4835 | `tvdb` | TVDB series ID |
| P436 | `musicbrainz_release_group` | MB release group ID |
| P434 | `musicbrainz_artist` | MB artist ID |
| P435 | `musicbrainz_work` | MB work ID |
| P648 | `olid` | Open Library ID |
| P212 | `isbn_13` | ISBN-13 |
| P957 | `isbn_10` | ISBN-10 |
| P2969 | `goodreads` | Goodreads book ID |

Plus:
- `wikidata` — Q-ID of the entity itself

### Confidence Scoring

Fixed confidence: `0.7` (search hit, no scoring API)

### Lookup Logic

1. Search for entities by title (language-aware)
2. Takes the first search result
3. Fetches entity claims (structured data)
4. Extracts cross-reference claims
5. Returns Wikidata Q-ID + all mapped external IDs

### Example

```python
from media_archivist.providers import all_providers

wd = all_providers()["wikidata"]
match = wd.lookup(Signals(title="Blade Runner", language="en"))
# Returns: ProviderMatch(
#   provider="wikidata",
#   confidence=0.7,
#   signals=Signals(title="Blade Runner"),
#   external_ids=ExternalIds(
#       wikidata="Q170589",
#       imdb="tt0083658",
#       tmdb_movie=78,
#       ...
#   )
# )
```

### Error Handling

Network errors are logged and return `None`. The provider is always available.

---

## Skyhook (Servarr proxy)

**Name:** `skyhook`

**Media Types:** Movie, TV, Music, Book

**Configuration:** None required. Uses the same public Servarr metadata proxies
that Sonarr / Radarr / Lidarr use internally — no API key needed.
— `metadatarr/resolve/providers/servarr_proxy.py:30`

**Backends dispatched internally by medium:**

| Medium | Proxy |
|---|---|
| MOVIE | `radarrapi.servarr.com/v1` (TMDB-shaped) |
| EPISODIC_SERIES | `skyhook.sonarr.tv/v1` (TVDB-shaped) |
| MUSIC | `api.lidarr.audio/v0.4` (MusicBrainz-shaped) |
| BOOK | `openlibrary.org` |

### External IDs Returned

- `tvdb` — TVDB series ID (TV)
- `tmdb_movie` / `tmdb_tv` — TMDB IDs (Movie / TV)
- `musicbrainz_artist` — MusicBrainz artist ID (Music)
- `olid` — OpenLibrary work ID (Book)

### Example

```python
from media_archivist.providers import all_providers
from mediavocab import MediaType
from mediavocab.models.signals import Signals

skyhook = all_providers()["skyhook"]
match = skyhook.lookup(Signals(title="The Matrix", year=1999, medium=MediaType.MOVIE))
```

Network errors are logged and return `None`. `is_available()` always returns `True`.

---

## Sonarr

**Name:** `arr_sonarr`

**Media Types:** TV

**Configuration:**
- `MEDIA_ARCHIVIST_SONARR_URL` (required; e.g., `http://localhost:8989`)
- `MEDIA_ARCHIVIST_SONARR_KEY` (required; API key)

**Rate Limit:** Depends on Sonarr instance

**Endpoint:** `{SONARR_URL}/api/v3/series/lookup?term=<query>`

### Configuration

Set both environment variables:

```bash
export MEDIA_ARCHIVIST_SONARR_URL=http://localhost:8989
export MEDIA_ARCHIVIST_SONARR_KEY=your_api_key_here
```

Provider becomes available once both are set.

### Signals Accepted

- `title` (required)
- `medium` (optional; must be TV)

### External IDs Returned

- `tvdb` — TVDB series ID
- `imdb` — IMDb ID
- `tmdb_tv` — TMDB TV series ID

### Confidence Scoring

Fixed: `0.9` (Sonarr lookup is fairly reliable)

### Lookup Logic

1. POST to `/api/v3/series/lookup?term=<title>`
2. Returns a list of series; takes the first
3. Extracts runtime (in minutes, converted to seconds), year, country, language

### Example

```python
from media_archivist.providers import all_providers

sonarr = all_providers()["arr_sonarr"]
if sonarr.is_available():
    match = sonarr.lookup(Signals(
        title="Breaking Bad",
        medium=MediaType.EPISODIC_SERIES
    ))
    # Returns: ProviderMatch(
    #   provider="arr_sonarr",
    #   confidence=0.9,
    #   signals=Signals(
    #       title="Breaking Bad",
    #       year=2008,
    #       runtime=2700.0,  # 45 minutes * 60
    #       country="US",
    #       language="en",
    #       medium=MediaType.EPISODIC_SERIES
    #   ),
    #   external_ids=ExternalIds(
    #       tvdb=81189,
    #       imdb="tt0903747",
    #       tmdb_tv=1396
    #   )
    # )
```

### Error Handling

Network errors are logged and return `None`. Provider is disabled if either env var is missing.

---

## Radarr

**Name:** `arr_radarr`

**Media Types:** Movie

**Configuration:**
- `MEDIA_ARCHIVIST_RADARR_URL` (required; e.g., `http://localhost:7878`)
- `MEDIA_ARCHIVIST_RADARR_KEY` (required; API key)

**Endpoint:** `{RADARR_URL}/api/v3/movie/lookup?term=<query>`

### Configuration

```bash
export MEDIA_ARCHIVIST_RADARR_URL=http://localhost:7878
export MEDIA_ARCHIVIST_RADARR_KEY=your_api_key_here
```

### Signals Accepted

- `title` (required)
- `medium` (optional; must be Movie)

### External IDs Returned

- `tmdb_movie` — TMDB movie ID
- `imdb` — IMDb ID

### Confidence Scoring

Fixed: `0.9`

### Lookup Logic

Similar to Sonarr: search, take first result, extract metadata.

---

## Readarr

**Name:** `arr_readarr`

**Media Types:** Book

**Configuration:**
- `MEDIA_ARCHIVIST_READARR_URL` (required; e.g., `http://localhost:8787`)
- `MEDIA_ARCHIVIST_READARR_KEY` (required; API key)

**Endpoint:** `{READARR_URL}/api/v1/book/lookup?term=<query>`

### Configuration

```bash
export MEDIA_ARCHIVIST_READARR_URL=http://localhost:8787
export MEDIA_ARCHIVIST_READARR_KEY=your_api_key_here
```

### Signals Accepted

- `title` (required)

### External IDs Returned

- `isbn_13` — ISBN-13
- `goodreads` — Goodreads book ID

### Confidence Scoring

Fixed: `0.85`

---

## Lidarr

**Name:** `arr_lidarr`

**Media Types:** Music

**Configuration:**
- `MEDIA_ARCHIVIST_LIDARR_URL` (required; e.g., `http://localhost:8686`)
- `MEDIA_ARCHIVIST_LIDARR_KEY` (required; API key)

**Endpoint:** `{LIDARR_URL}/api/v1/album/lookup?term=<query>`

### Configuration

```bash
export MEDIA_ARCHIVIST_LIDARR_URL=http://localhost:8686
export MEDIA_ARCHIVIST_LIDARR_KEY=your_api_key_here
```

### Signals Accepted

- `title` (album name, required)

### External IDs Returned

- `musicbrainz_release_group` — MB release group ID
- `musicbrainz_artist` — MB artist ID

### Confidence Scoring

Fixed: `0.85`

---

## Provider API

### Base Class

```python
from metadatarr.resolve.base import MetadataProvider, ProviderMatch

class MetadataProvider(ABC):
    """Abstract base for metadata providers."""

    name: ClassVar[str] = ""
    """Unique provider name (lowercase, underscore-separated)."""

    media: ClassVar[Set[MediaType]] = set()
    """Set of Medium types this provider handles."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider has all config it needs."""

    @abstractmethod
    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        """Lookup signals and return a match, or None if not found."""
```

### ProviderMatch

```python
class ProviderMatch(BaseModel):
    """One provider's response."""

    provider: str
    """Provider name."""

    confidence: float
    """Confidence score (0.0 to 1.0)."""

    signals: Signals = Field(default_factory=Signals)
    """Signals extracted from the provider."""

    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    """External IDs the provider returned."""
```

### Registry

```python
from media_archivist.providers import (
    all_providers,
    active_providers,
    register,
)

def all_providers() -> Dict[str, MetadataProvider]:
    """Return all registered providers (active and inactive)."""

def active_providers() -> List[MetadataProvider]:
    """Return only providers where is_available() == True."""

def register(provider: MetadataProvider) -> MetadataProvider:
    """Register a provider instance.
    
    Raises:
        ValueError: If provider.name is empty.
    """
```

---

## Writing a Custom Provider

To write a third-party provider:

### 1. Subclass MetadataProvider

```python
from metadatarr.resolve.base import MetadataProvider, ProviderMatch
from mediavocab import MediaType, Signals
from mediavocab import ExternalIds

class MyCustomProvider(MetadataProvider):
    name = "my_custom"
    media = {MediaType.MUSIC}

    def is_available(self) -> bool:
        import os
        return bool(os.environ.get("MY_CUSTOM_API_KEY"))

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not (signals.title and signals.artist):
            return None
        if signals.medium and signals.medium != MediaType.MUSIC:
            return None

        # Call your API, parse results
        api_key = os.environ.get("MY_CUSTOM_API_KEY")
        # ... implementation ...

        # Return a match
        return ProviderMatch(
            provider=self.name,
            confidence=0.85,
            signals=Signals(
                title=result.get("title"),
                artist=result.get("artist"),
                year=result.get("year"),
                runtime=result.get("runtime"),
                medium=MediaType.MUSIC,
            ),
            external_ids=ExternalIds(
                extra={
                    "my_custom_id": result.get("id"),
                }
            ),
        )
```

### 2. Register the Provider

```python
from media_archivist.providers import register

register(MyCustomProvider())
```

### 3. Ship as a Package

Create a package (e.g., `media-archivist-custom-provider`) that:

1. Defines your provider class
2. Registers it on import
3. Users install: `pip install media-archivist-custom-provider`

The provider will be auto-discovered when `media-archivist` loads.

### 4. Configuration Pattern

Follow the built-in pattern:

- Configuration via environment variables
- `is_available()` returns False if config is missing
- No startup errors; graceful degradation
- Document required env vars clearly

### Example: Custom Environment Variables

```python
class CustomProvider(MetadataProvider):
    name = "custom"
    media = {MediaType.MUSIC, MediaType.MOVIE}

    def is_available(self) -> bool:
        url = os.environ.get("MEDIA_ARCHIVIST_CUSTOM_URL")
        key = os.environ.get("MEDIA_ARCHIVIST_CUSTOM_KEY")
        return bool(url and key)

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        if not self.is_available():
            return None
        # ... rest of implementation
```

Users set:

```bash
export MEDIA_ARCHIVIST_CUSTOM_URL=https://api.example.com
export MEDIA_ARCHIVIST_CUSTOM_KEY=secret_key_here
```

---

## Provider Usage in Canonicalization

Providers are invoked during `canonicalize`:

```bash
media-archivist canonicalize --providers musicbrainz,wikidata
```

Or programmatically:

```python
from media_archivist.canonicalize import canonicalize

canonical, quarantine = canonicalize(
    "db.json",
    providers=["musicbrainz", "wikidata"],
    stamp_rows=True,
)
```

The orchestrator:

1. Loads each entry from the database
2. Extracts signals (title, artist, year, etc.)
3. Runs each provider's `lookup()`
4. Compares provider signals against local signals
5. If all agree, creates a `CanonicalRecord` with merged signals + external IDs
6. If signals conflict, entries go to quarantine for manual review

---

## Troubleshooting

### Provider not available

Check `media-archivist providers | jq` output. If `"active": false`, verify configuration:

```bash
# MusicBrainz (always available)
media-archivist providers | jq '.[] | select(.name=="musicbrainz")'

# skyhook (always available — no key needed)
media-archivist providers | jq '.[] | select(.name=="skyhook")'

# Sonarr (needs URL and key)
echo $MEDIA_ARCHIVIST_SONARR_URL
echo $MEDIA_ARCHIVIST_SONARR_KEY
# If either empty, set both
```

### Provider lookups failing

Enable debug logging:

```bash
media-archivist --verbose canonicalize --providers musicbrainz 2>&1 | grep -i musicbrainz
```

Check network connectivity to the provider's endpoint.

### High quarantine rate

Adjust signal comparison tolerances or configure providers with better coverage.

---

## Signal Comparison Details

When providers return matches, the canonicalize system compares their signals to the local entry:

```python
from mediavocab import Signals
from mediavocab.models.signals import compare_signals as compare

local = Signals(title="Bohemian Rhapsody", artist="Queen", year=1975)
provider = Signals(title="Bohemian Rhapsody", artist="Queen", year=1975)

conflicts = compare(local, provider)
if not conflicts:
    print("Match!")  # No conflicts
else:
    print(f"Conflicts: {conflicts}")  # Disagreement
```

Tolerances are built-in:

- **String fields** (title, artist): Fuzzy match ≥ 0.92 / 0.90 similarity
- **Year**: ±1 year
- **Runtime**: ±5 seconds
- **Medium, country, language**: Exact match

Customize tolerances at the call site if needed.
