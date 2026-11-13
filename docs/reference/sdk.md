# Python SDK Reference

Complete API reference for the `media_archivist` Python library.

## Top-Level Exports

Import from `media_archivist`:

```python
from media_archivist import (
    Index,
    YoutubeArchivist,
    YoutubeMonitor,
    YoutubeMusicArchivist,
    IAArchivist,
    BandcampArchivist,          # Optional; may be None
    SoundCloudArchivist,        # Optional; may be None
    MediaArchivistError,
    VideoUnavailable,
    __version__,
)
```

---

## Index

Read-side SDK over a media_archivist database file.

```python
class Index:
    def __init__(self, path: str | Path) -> None:
        """Open a media_archivist database file (read-only).
        
        Args:
            path: Absolute or relative path to the JSON database file.
                  Auto-loads sidecars (<db>.canonical.json, <db>.links.json).
        """

    @property
    def path(self) -> str:
        """File path of the database."""

    @property
    def meta(self) -> ArchiveMeta:
        """Archive-level metadata (version, created, source mix, etc.)."""

    def __len__(self) -> int:
        """Total number of entries in the database."""

    def raw_entries(self) -> Iterator[dict]:
        """Iterate over raw (unvalidated) entry dicts.
        
        Yields:
            Raw entry dictionaries as stored on disk.
        """

    def view(
        self,
        *,
        where: Optional[str] = None,
        source: Optional[str] = None,
        has_stream: Optional[bool] = None,
        explicit: Optional[bool] = None,
        grep: Optional[str] = None,
        limit: int = 0,
    ) -> Iterator[MediaEntry]:
        """Yield MediaEntry rows matching the given filters.
        
        All filters are optional and combine with AND logic.
        
        Args:
            where: Sandboxed WHERE expression (e.g., 'duration>180 and source=="youtube"').
                   See WHERE Language reference.
            source: Keep only entries from this source 
                    (youtube, youtube_music, bandcamp, soundcloud, internet_archive).
            has_stream: If True, only entries with stream URL.
                        If False, only entries without stream.
                        If None, no filter.
            explicit: If True, only explicit-flagged entries.
                      If False, only non-explicit entries.
                      If None, no filter.
            grep: Filter by substring in title (case-insensitive).
            limit: Emit at most N rows (0 = unlimited).
        
        Yields:
            MediaEntry objects.
        
        Raises:
            WhereError: If the WHERE expression is invalid or uses denied syntax.
        """

    def to_list(self, **filters) -> List[MediaEntry]:
        """Convenience method to collect all view() results into a list.
        
        Args:
            **filters: Same as view().
        
        Returns:
            List of MediaEntry objects.
        """
```

### Example

```python
from media_archivist import Index

idx = Index("./talks.json")
print(f"Database has {len(idx)} entries")
print(f"Created: {idx.meta.created}")

# List all YouTube Music entries longer than 3 minutes
for entry in idx.view(source="youtube_music", where="duration > 180"):
    print(f"{entry.title} ({entry.duration}s) by {entry.artist}")

# Count entries by source
from collections import Counter
sources = Counter(e.source.value for e in idx.view())
print(sources)
```

---

## YoutubeArchivist

Index YouTube channels, playlists, and search results.

```python
class YoutubeArchivist(JsonArchivist):
    def __init__(
        self,
        db_name: Optional[str] = None,
        db_path: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
    ) -> None:
        """Initialize a YouTube archivist.
        
        Args:
            db_name: XDG database name (auto-placed at ~/.local/share/media_archivist/<name>.json).
                     Mutually exclusive with db_path.
            db_path: Explicit database file path.
                     Mutually exclusive with db_name.
            required_kwords: Only archive entries whose title contains all of these keywords.
            blacklisted_kwords: Skip entries whose title contains any of these keywords.
            min_duration: Minimum duration in seconds (only for search results with duration).
            logger: Custom logger instance.
        
        Raises:
            ValueError: If both db_name and db_path are provided.
        """

    @property
    def db(self) -> EnvelopeJsonStorage:
        """Access the raw database dict (URL → entry)."""

    @property
    def video_urls(self) -> List[str]:
        """List of all video URLs in the database."""

    def archive(self, url: str) -> None:
        """Archive a YouTube URL (channel, playlist, or video).
        
        Args:
            url: YouTube channel URL, playlist URL, video URL, or video ID.
        
        Raises:
            VideoUnavailable: If the video is removed or private.
            ValueError: If URL is malformed.
        """

    def sorted_entries(self) -> list:
        """Return all entries sorted by upload timestamp (newest first).
        
        Returns:
            List of raw entry dicts.
        """

    def remove_unavailable(self) -> None:
        """Remove entries that no longer resolve (probed via oEmbed)."""

    def remove_keyword(self, kwords: Optional[Iterable[str]] = None) -> None:
        """Remove entries whose title contains any of the given keywords.
        
        Args:
            kwords: Keywords to match. Defaults to self.blacklisted_kwords.
        """

    def remove_missing(self, kwords: Iterable[str]) -> None:
        """Remove entries missing any of the specified fields.
        
        Args:
            kwords: Field names (e.g., ['duration', 'author']).
        """

    def remove_below_duration(self, minutes: int = 30) -> None:
        """Remove entries shorter than the specified duration.
        
        Args:
            minutes: Duration threshold in minutes.
        """

    def bootstrap_from_url(self, url: str) -> None:
        """Seed an empty database from a remote JSON dump.
        
        Args:
            url: HTTP(S) URL to a JSON file mapping URLs to entries.
        """
```

### Example

```python
from media_archivist import YoutubeArchivist

archivist = YoutubeArchivist(db_path="./my_talks.json")

# Archive a channel
archivist.archive("https://www.youtube.com/@SomeChannel")

# Archive a playlist
archivist.archive("https://www.youtube.com/playlist?list=PLxxx")

# Archive a single video
archivist.archive("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

print(f"Database now has {len(archivist.video_urls)} URLs")

# Remove videos longer than 30 minutes
archivist.remove_below_duration(minutes=-1)  # inverse logic
```

---

## YoutubeMusicArchivist

Index YouTube Music tracks, albums, artists, and playlists.

```python
class YoutubeMusicArchivist(JsonArchivist):
    def __init__(
        self,
        db_name: Optional[str] = None,
        db_path: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
        skip_explicit: bool = False,
        only_audio: bool = False,
    ) -> None:
        """Initialize a YouTube Music archivist.
        
        Args:
            db_name: XDG database name.
            db_path: Explicit database file path.
            required_kwords: Filter by keyword (title must contain all).
            blacklisted_kwords: Filter by keyword (skip if title contains any).
            min_duration: Minimum duration in seconds.
            logger: Custom logger instance.
            skip_explicit: Skip tracks flagged explicit.
            only_audio: Keep only audio-only tracks (no music videos).
        """

    def archive(self, url_or_query: str) -> None:
        """Archive a YouTube Music URL or search query.
        
        Dispatches based on URL:
        - Playlist URL → archive_playlist()
        - Album URL (/browse/) → archive_album()
        - Artist URL (/channel/) → archive_artist()
        - Video URL (watch?v=) → archive_video_id()
        - Text query → archive_search()
        
        Args:
            url_or_query: YouTube Music URL or free-text search query.
        """

    def archive_search(self, query: str, max_results: int = -1) -> None:
        """Run a YT Music search and archive track/video results.
        
        Args:
            query: Search query string.
            max_results: Maximum number of results to archive (-1 = unlimited).
        """

    def archive_playlist(self, url: str) -> None:
        """Archive all tracks in a YouTube Music playlist.
        
        Args:
            url: Playlist URL (music.youtube.com/playlist?list=...).
        """

    def archive_album(self, browse_id: str) -> None:
        """Archive all tracks in a YouTube Music album.
        
        Args:
            browse_id: Album browse ID (e.g., MPREb_xxx).
        """

    def archive_artist(self, browse_id: str) -> None:
        """Archive all albums and tracks by a YouTube Music artist.
        
        Args:
            browse_id: Artist channel ID (e.g., UCxxx).
        """
```

### Example

```python
from media_archivist import YoutubeMusicArchivist

archivist = YoutubeMusicArchivist(
    db_path="./music.json",
    skip_explicit=True,
    only_audio=True,
)

# Search for music
archivist.archive("ambient electronic music")

# Archive a playlist
archivist.archive("https://music.youtube.com/playlist?list=PLxxx")

# Archive an album
archivist.archive("https://music.youtube.com/browse/MPREb_xxx")

# Archive an artist's work
archivist.archive("https://music.youtube.com/channel/UCxxx")
```

---

## YoutubeMonitor

Background thread that periodically re-syncs a set of YouTube URLs.

```python
class YoutubeMonitor(Thread):
    def __init__(
        self,
        db_name: Optional[str] = None,
        db_path: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
        sync_interval: int = 120,
        repeat_min_gap: int = 30,
    ) -> None:
        """Initialize a YouTube monitor thread.
        
        Args:
            db_name: XDG database name.
            db_path: Explicit database file path.
            required_kwords: Filter by keyword.
            blacklisted_kwords: Filter by keyword.
            min_duration: Minimum duration in seconds.
            logger: Custom logger instance.
            sync_interval: Seconds between syncs (default: 120).
            repeat_min_gap: Minimum seconds between re-syncing the same URL (default: 30).
        """

    @property
    def db(self) -> EnvelopeJsonStorage:
        """Access the database dict."""

    def sorted_entries(self) -> list:
        """Return all entries sorted by upload timestamp (newest first)."""

    def bootstrap_from_url(self, url: str) -> None:
        """Seed the database from a remote JSON dump (if empty)."""

    def monitor(self, url: str) -> None:
        """Add a URL to the monitor queue."""

    def start(self) -> None:
        """Start the background monitoring thread."""

    def stop(self) -> None:
        """Stop the monitoring thread (safe to call multiple times)."""

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the thread to finish.
        
        Raises:
            KeyboardInterrupt: If Ctrl-C is pressed while waiting.
        """
```

### Example

```python
from media_archivist import YoutubeMonitor

monitor = YoutubeMonitor(db_path="./db.json", sync_interval=300)
monitor.start()

# Add URLs to monitor
monitor.monitor("https://www.youtube.com/@Channel1")
monitor.monitor("https://www.youtube.com/@Channel2")

print("Monitoring... Press Ctrl-C to stop")
try:
    monitor.join()
except KeyboardInterrupt:
    monitor.stop()
    print("Stopped.")
```

---

## IAArchivist

Index Internet Archive streams.

```python
class IAArchivist(JsonArchivist):
    def __init__(
        self,
        db_name: Optional[str] = None,
        db_path: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
    ) -> None:
        """Initialize an Internet Archive archivist.
        
        Args:
            db_name: XDG database name.
            db_path: Explicit database file path.
            required_kwords: Filter by keyword.
            blacklisted_kwords: Filter by keyword.
            min_duration: Minimum duration in seconds.
            logger: Custom logger instance.
        """

    def archive(self, url: str) -> None:
        """Archive an Internet Archive item URL.
        
        Args:
            url: Internet Archive item URL (archive.org/details/...).
        """
```

---

## BandcampArchivist

Index Bandcamp tracks, albums, and artists.

```python
class BandcampArchivist(JsonArchivist):
    def __init__(
        self,
        db_name: Optional[str] = None,
        db_path: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
    ) -> None:
        """Initialize a Bandcamp archivist.
        
        Requires: pip install py_bandcamp
        
        Args:
            db_name: XDG database name.
            db_path: Explicit database file path.
            required_kwords: Filter by keyword.
            blacklisted_kwords: Filter by keyword.
            min_duration: Minimum duration in seconds.
            logger: Custom logger instance.
        """

    def archive(self, url_or_query: str) -> None:
        """Archive a Bandcamp URL or search query.
        
        Dispatches based on URL:
        - /track/ → archive individual track
        - /album/ → archive_album()
        - Other → archive_artist()
        - Text query → archive_search()
        
        Args:
            url_or_query: Bandcamp URL or free-text search query.
        """

    def archive_search(self, query: str, max_results: int = -1) -> None:
        """Search Bandcamp and archive track results.
        
        Args:
            query: Search query string.
            max_results: Maximum results (-1 = unlimited).
        """

    def archive_album(self, url: str) -> None:
        """Archive all tracks in a Bandcamp album.
        
        Args:
            url: Album URL.
        """

    def archive_artist(self, url: str) -> None:
        """Archive all albums and tracks by a Bandcamp artist.
        
        Args:
            url: Artist profile URL.
        """
```

---

## SoundCloudArchivist

Index SoundCloud tracks and playlists.

```python
class SoundCloudArchivist(JsonArchivist):
    def __init__(
        self,
        db_name: Optional[str] = None,
        db_path: Optional[str] = None,
        required_kwords: Optional[Iterable[str]] = None,
        blacklisted_kwords: Optional[Iterable[str]] = None,
        min_duration: int = -1,
        logger: Logger = LOG,
    ) -> None:
        """Initialize a SoundCloud archivist.
        
        Requires: pip install nuvem_de_som
        
        Args:
            db_name: XDG database name.
            db_path: Explicit database file path.
            required_kwords: Filter by keyword.
            blacklisted_kwords: Filter by keyword.
            min_duration: Minimum duration in seconds.
            logger: Custom logger instance.
        """

    def archive(self, url_or_query: str) -> None:
        """Archive a SoundCloud URL or search query.
        
        Args:
            url_or_query: SoundCloud URL or free-text search query.
        """
```

---

## Canonicalization Functions

Cross-source deduplication and metadata enrichment.

```python
from media_archivist.dedupe import fingerprint, build_links, dedupe, write_dedupe_jsonl

def fingerprint(entry: MediaEntry) -> str:
    """Return a deterministic fingerprint for cross-source matching.
    
    Computed from normalized (artist, title) only — duration is used
    as a soft guard at dedupe time.
    
    Args:
        entry: A MediaEntry.
    
    Returns:
        SHA1 hex string (40 characters).
    """

def build_links(
    entries: Iterable[MediaEntry],
    duration_tolerance: float = 2.0,
) -> Dict[str, List[str]]:
    """Group entry ids by fingerprint.
    
    Only fingerprints with >= 2 entries are kept. Within a group,
    candidates whose duration disagrees by more than duration_tolerance
    are split into a separate group keyed <fp>:<n>.
    
    Args:
        entries: Iterable of MediaEntry objects.
        duration_tolerance: Seconds of duration mismatch tolerated.
    
    Returns:
        Dict mapping fingerprint → list of entry ids.
    """

def dedupe(
    db_path: str,
    preference: Optional[List[str]] = None,
    duration_tolerance: float = 2.0,
) -> List[dict]:
    """Read view + links sidecar and emit deduped canonical dicts.
    
    Args:
        db_path: Path to the database file.
        preference: Source preference order (winners first).
                   Default: bandcamp, internet_archive, youtube_music, soundcloud, youtube.
        duration_tolerance: Tolerance for duration matching.
    
    Returns:
        List of canonical entry dicts (one per deduplicated group).
    """

def write_dedupe_jsonl(canonical_rows: List[dict], output_path: str) -> int:
    """Write deduplicated rows to a JSONL file.
    
    Args:
        canonical_rows: List of canonical dicts (from dedupe()).
        output_path: Path to output JSONL file.
    
    Returns:
        Number of rows written.
    """
```

### Example

```python
from media_archivist import Index
from media_archivist.dedupe import fingerprint, build_links

idx = Index("./db.json")
entries = list(idx.view())

# Compute fingerprints
links = build_links(entries, duration_tolerance=2.0)
print(f"Found {len(links)} fingerprint groups")

for fp, ids in links.items():
    if len(ids) > 1:
        print(f"Fingerprint {fp[:8]}... has {len(ids)} entries")
```

---

## Canonicalize Orchestrator

```python
from media_archivist.canonicalize import (
    canonicalize,
    load_canonical,
    save_canonical,
    load_quarantine,
    save_quarantine,
)

def canonicalize(
    db_path: str,
    providers: Optional[Sequence[str]] = None,
    stamp_rows: bool = True,
) -> Tuple[CanonicalSidecar, QuarantineSidecar]:
    """Run providers and update canonical/quarantine sidecars.
    
    Args:
        db_path: Path to the database file.
        providers: List of provider names to run (default: all active).
        stamp_rows: If True, write _meta.canonical_id back to rows.
    
    Returns:
        Tuple of (canonical_sidecar, quarantine_sidecar).
    
    Raises:
        ValueError: If an unknown provider name is specified.
    """

def load_canonical(db_path: str) -> CanonicalSidecar:
    """Load <db>.canonical.json or return empty sidecar."""

def save_canonical(db_path: str, sidecar: CanonicalSidecar) -> Path:
    """Save canonical sidecar to disk."""

def load_quarantine(db_path: str) -> QuarantineSidecar:
    """Load <db>.quarantine.json or return empty sidecar."""

def save_quarantine(db_path: str, sidecar: QuarantineSidecar) -> Path:
    """Save quarantine sidecar to disk."""
```

---

## WHERE Expression Evaluation

```python
from media_archivist.index import evaluate_where, WhereError

def evaluate_where(expr: str, entry: MediaEntry) -> bool:
    """Evaluate a sandboxed WHERE expression against an entry.
    
    Args:
        expr: WHERE expression string (e.g., 'duration > 180 and source == "youtube"').
        entry: MediaEntry to evaluate against.
    
    Returns:
        True if the expression matches, False otherwise.
    
    Raises:
        WhereError: If the expression is invalid or uses denied syntax.
    """

class WhereError(ValueError):
    """Raised when a WHERE expression is invalid or uses denied syntax."""
```

### Example

```python
from media_archivist import Index
from media_archivist.index import evaluate_where

idx = Index("./db.json")
for entry in idx.view():
    if evaluate_where('duration > 300 and source == "youtube_music"', entry):
        print(entry.title)
```

---

## Exceptions

```python
class MediaArchivistError(Exception):
    """Base class for all media_archivist errors."""

class VideoUnavailable(MediaArchivistError):
    """Raised when a video has been removed, made private, or otherwise can't be fetched."""
```

---

## Storage Classes

```python
from media_archivist.storage import EnvelopeJsonStorage, EnvelopeJsonStorageXDG

class EnvelopeJsonStorage:
    """Explicit-path JSON storage with envelope semantics.
    
    The on-disk file is the MediaArchive envelope
    ({"_meta": {...}, "entries": {...}}), while in-memory the dict
    interface maps URL → entry.
    """

    def __init__(self, path: str, disable_lock: bool = False) -> None:
        """Initialize storage.
        
        Args:
            path: Absolute path to JSON file.
            disable_lock: Disable file locking (not recommended).
        """

    @property
    def meta(self) -> ArchiveMeta:
        """Archive-level metadata."""

    def store(self, path: Optional[str] = None) -> None:
        """Write the envelope to disk.
        
        Args:
            path: Optional override path.
        """

class EnvelopeJsonStorageXDG:
    """XDG-managed JSON storage with envelope semantics.
    
    Database is auto-placed under ~/.local/share/<subfolder>/<name>.json.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        subfolder: str = "media_archivist",
        xdg_folder: Optional[str] = None,
    ) -> None:
        """Initialize XDG storage.
        
        Args:
            name: Database name (auto-placed at ~/.local/share/<subfolder>/<name>.json).
            subfolder: XDG subdirectory.
            xdg_folder: Override the default XDG base directory.
        """
```

---

## Version

```python
from media_archivist import __version__

print(__version__)  # "X.Y.Z"
```
