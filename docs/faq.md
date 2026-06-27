# FAQ: media_archivist

Frequently asked questions and troubleshooting for the metadata-only media indexer.

## Installation & Setup

### Q: I get "ImportError: No module named media_archivist" after install.

**A:** Ensure you installed the right variant:

```bash
# Core (YouTube, YT Music, IA)
pip install media_archivist

# Or with optional backends
pip install media_archivist[bandcamp]
pip install media_archivist[soundcloud]
pip install media_archivist[all]
```

If you installed via `all`, check your `$PYTHONPATH`:

```bash
python -c "import media_archivist; print(media_archivist.__file__)"
```

### Q: "error: {backend} backend requires `pip install ...`"

**A:** You used a backend flag (e.g., `--bandcamp`) without installing the optional dependency.

```bash
# If you get "bandcamp backend requires py_bandcamp":
pip install media_archivist[bandcamp]

# If you get "soundcloud backend requires nuvem_de_som":
pip install media_archivist[soundcloud]
```

After install, retry the command.

### Q: Should I use `--db NAME` or `--db-file PATH`?

**A:** It depends on your use case.

**Use `--db-file PATH`** if:
- You want to commit the JSON alongside your code (recommended for datasets).
- You're running scripts from a specific directory.
- You want explicit control over file location.

Example:

```bash
media-archivist add --db-file ./my_dataset.json https://www.youtube.com/@Channel
```

**Use `--db NAME`** if:
- You want a system-wide, auto-managed database under XDG.
- You're using `media_archivist` interactively across multiple projects.
- You don't want to pass the full path every time.

Example:

```bash
media-archivist add --db my_music_collection https://music.youtube.com/...
```

The file lives at `~/.local/share/media_archivist/my_music_collection.json` and is shared across all commands using `--db my_music_collection`.

## Backends & Data Sources

### Q: Which backend should I use for music?

**A:** Use **YouTube Music** (`--music`) for the best metadata, **Bandcamp** (`--bandcamp`) if you need direct stream URLs, **SoundCloud** (`--soundcloud`) for user-uploaded content.

| Backend | Best for | Metadata richness | Direct streams |
|---------|----------|-------------------|---|
| YouTube Music | Official releases, albums, playlists | High (artist, album, year, duration, explicit) | No (YouTube-only) |
| Bandcamp | Independent artists, album releases | Medium (artist, album, duration) | Yes |
| SoundCloud | User uploads, DJ sets, remixes | Low (track, artist, duration) | Yes (if resolved) |
| YouTube | Everything else (talks, tutorials, vlogs) | Low (title, views, description) | No (YouTube-only) |

For **music deduplication**, dedupe with source preference: `--prefer bandcamp,youtube_music,soundcloud`. Bandcamp entries include stream URLs, making them ideal for offline playback.

### Q: Can I index multiple sources into the same DB?

**A:** Yes. Just run `add` multiple times with different `--music`, `--bandcamp`, etc. flags:

```bash
media-archivist add --db-file music.json --music "Artist Name"
media-archivist add --db-file music.json --bandcamp "https://artist.bandcamp.com"
media-archivist add --db-file music.json --soundcloud "https://soundcloud.com/artist"
```

The DB will have 117 entries from all three sources. Use `stats` to see the breakdown:

```bash
media-archivist stats --db-file music.json
```

To deduplicate across sources, use `link` and `dedupe` (see [Tutorial](tutorial.md)).

### Q: Is indexing from YouTube considered scraping? What about ToS?

**A:** `media_archivist` uses public APIs or metadata that YouTube / Bandcamp / etc. already expose publicly. Here's the breakdown:

| Backend | Method | ToS | Scraping? |
|---------|--------|-----|-----------|
| YouTube | `tutubo` (web scrape) | Technically gray; no API key needed | Yes, but metadata-only |
| YouTube Music | `ytmusicapi` (web scrape) | Technically gray | Yes, but metadata-only |
| Bandcamp | Public artist pages | Public data, no login | No, public access |
| SoundCloud | `nuvem_de_som` (API-like) | Unclear | Possibly |
| Internet Archive | Metadata API | Encouraged | No, uses official API |

**Key point:** `media_archivist` is **metadata-only**. It reads title, URL, artist, duration, and other publicly-visible metadata. It does **not download content**; that's a separate, independent step via `yt-dlp` or your own tool.

**Recommendation:** For production use cases, especially at scale:
- Use **Internet Archive** (openly encouraged).
- Consider **YouTube Data API** if you have sufficient quota.
- Check the **Bandcamp** and **SoundCloud** ToS if using at scale.

The library is designed for **research, curation, and dataset building** — typical uses have never triggered enforcement action.

### Q: Can I index a private YouTube playlist?

**A:** No. `tutubo` (the YouTube backend) relies on public metadata that appears in the HTML. Private playlists, private channels, and age-gated content are not publicly accessible, so `media_archivist` cannot see them.

**Workaround:** Export the playlist from YouTube's UI (download as CSV or use `yt-dlp`), then import the URLs:

```bash
media-archivist import --db-file my_private.json my_exported_urls.txt
```

### Q: Does media_archivist work offline?

**A:** No. Indexing requires network access to fetch metadata from the source (YouTube, Bandcamp, etc.). However, once indexed, you can **export and work offline**:

```bash
# Online: index
media-archivist add --db-file my_data.json https://www.youtube.com/@Channel

# Offline: work with the JSON
media-archivist list --db-file my_data.json
media-archivist export --db-file my_data.json --format jsonl -o data.jsonl
```

The JSON DB is just a file; you can copy it to an offline machine and use `list`, `export`, `urls` without network access.

For download, you need `yt-dlp` (or another tool) and internet access to the video platform.

## Performance & Scale

### Q: How fast is indexing large channels?

**A:** Depends on the backend:

- **YouTube channels** (1000+ videos): ~5–10 minutes for 1000 videos (network-bound).
- **YouTube Music playlists** (500 tracks): ~30 seconds per 100 tracks.
- **Bandcamp artists** (100+ tracks): ~1 minute.
- **Internet Archive collections** (10000+ items): ~2–3 minutes for 10000.

Speed is limited by:
1. Network latency to the source.
2. Pagination (most backends paginate in 50–100 item chunks).
3. Per-entry metadata extraction.

**Tip:** Use `--limit N` during development to test with small result sets, then re-run without `--limit` for the full index.

### Q: I have 100K videos. Will media_archivist handle it?

**A:** Yes, but with caveats:

- **File size:** 100K entries @ ~1–2KB per entry = 100–200MB JSON (still manageable).
- **Memory:** Loaded into memory at once; Python can handle this on modern machines.
- **Speed:** Index operation may take 1–2 hours if network-bound.
- **Diff:** Git diffs on 200MB files are slow; consider using `--db NAME` (XDG) and not version-controlling the raw DB.

**Recommendations for large DBs:**
1. Use `--db NAME` (XDG-managed) instead of `--db-file` for the raw index.
2. Export to JSONL (`--format jsonl`) for downstream processing.
3. Use `link` and `dedupe` to reduce to a deduplicated JSONL (typically 30–50% smaller).
4. If you need versioning, version-control the deduplicated JSONL, not the raw JSON.

## Filtering & Querying

### Q: Why doesn't `--min-duration` work on plain YouTube channels?

**A:** YouTube bare-channel scraping (via `tutubo`) doesn't expose duration metadata. The `--min-duration` flag is a no-op in that case.

**Why?** YouTube's channel pages don't list video duration in the public HTML; you'd need to fetch each video's metadata individually (slow and ToS-risky).

**When does `--min-duration` work?**
- YouTube Music (`--music`) — durations are exposed.
- Bandcamp (`--bandcamp`) — durations are exposed.
- SoundCloud (`--soundcloud`) — durations are exposed.
- Internet Archive (`--ia`) — durations are exposed.
- YouTube search results — durations are in the preview metadata.

**Workaround for plain YouTube:** Use `--blacklist` to exclude common short-form patterns:

```bash
media-archivist add --db-file yt.json \
    --blacklist "#shorts" --blacklist "short" \
    https://www.youtube.com/@Channel
```

### Q: What's the difference between `--blacklist` and `--require`?

**A:** 

- **`--blacklist KW`** — skip entries whose title contains this keyword (case-insensitive substring match).
- **`--require KW`** — index *only* entries whose title contains this keyword.

Examples:

```bash
# Skip shorts
media-archivist add --db-file yt.json --blacklist "#shorts" \
    https://www.youtube.com/@Channel

# Index only CPU reviews
media-archivist add --db-file yt.json --require "cpu" --require "review" \
    https://www.youtube.com/@Channel
```

Multiple `--blacklist` flags are OR'd (skip if *any* match). Multiple `--require` flags are AND'd (include only if *all* match).

### Q: How do I filter the DB after indexing?

**A:** Use `list`, `urls`, `export` with `--grep` (substring) or `--where` (complex expressions).

```bash
# Substring match
media-archivist list --db-file talks.json --grep "machine learning"

# Complex expression (canonical view)
media-archivist list --db-file talks.json --canonical \
    --where 'duration > 600 and source != "youtube"'

# URLs matching a filter
media-archivist urls --db-file talks.json --where 'views > 1000000'

# Export filtered subset
media-archivist export --db-file talks.json --format jsonl \
    --grep "tutorial" -o tutorials.jsonl
```

See [Canonical](canonical.md) for full `--where` syntax.

### Q: Can I use regex in `--grep` or `--where`?

**A:** `--grep` is substring-only (not regex). `--where` supports `lower()` and `upper()` string functions but no regex.

For regex, export to JSONL and post-process with `jq` or Python:

```bash
media-archivist export --db-file talks.json --format jsonl \
    | jq 'select(.title | test("machine.*learning"; "i"))'
```

## Canonicalization & Deduplication

### Q: When should I use `link` and `dedupe`?

**A:** Use `link` and `dedupe` when:
- You've indexed the same media from multiple sources (e.g., YouTube Music + Bandcamp + SoundCloud).
- You want to eliminate duplicates and keep the "best" version (preferred source).
- You're building a deduplicated dataset for ML training.

**Workflow:**

```bash
# 1. Index from multiple sources
media-archivist add --db-file music.json --music "Artist"
media-archivist add --db-file music.json --bandcamp "Artist"

# 2. Find duplicates by fingerprint
media-archivist link --db-file music.json
# → creates music.links.json sidecar

# 3. Collapse to canonical JSONL
media-archivist dedupe --db-file music.json \
    --output music.canonical.jsonl
```

The sidecar and source DB are **not modified** by these operations — only the output JSONL is new.

### Q: What's the difference between `dedupe` and `canonicalize`?

**A:**

- **`dedupe`** — Fingerprint-based deduplication. Matches entries by normalized title + artist and duration tolerance. Outputs a JSONL with one row per unique work. Simple, local, no external API calls.

- **`canonicalize`** — Mints canonical IDs and looks up external IDs (MusicBrainz, TMDB, IMDb, etc.) via registered providers. Matches entries based on a signal set (title, artist, year, runtime, language, etc.) with conservative disagreement handling (quarantine rows that don't match). Outputs sidecar JSONs and stamps `_meta.canonical_id` on rows.

**Choose:**
- Use **`dedupe`** for quick, local deduplication without external lookups.
- Use **`canonicalize`** when you need external metadata (IMDb, TMDB, MusicBrainz IDs) or when you want to link across very different media types (audio + video + book titles for the same work).

See [Canonical](canonical.md) and [Disambiguation](disambiguation.md) for details.

### Q: What rows get quarantined?

**A:** During `canonicalize`, a row is quarantined if a provider's response **disagrees** with your local data on at least one signal.

For example:
- Your row says "duration=300", provider says "duration=310" (disagree by >5s) → quarantined.
- Your row says "year=2020", provider says "year=2019" (disagree by >1) → quarantined.
- Your row says "artist=John", provider says "artist=Jon" (fuzzy ratio <0.90) → quarantined.

When a row is quarantined:
- No `canonical_id` is stamped on the raw row.
- The row is moved to `<db>.quarantine.json` with conflict details.
- You can manually review and resolve with `quarantine-resolve` or `quarantine-reject`.

See [Disambiguation](disambiguation.md) for quarantine workflow.

## Advanced Usage

### Q: How do I build a training dataset from the index?

**A:** Typical workflow:

```bash
# 1. Index
media-archivist add --db-file dataset.json \
    https://www.youtube.com/@Channel1 \
    https://www.youtube.com/@Channel2

# 2. Deduplicate (if multi-source)
media-archivist link --db-file dataset.json
media-archivist dedupe --db-file dataset.json --output canonical.jsonl

# 3. Canonicalize (if you need external IDs)
media-archivist canonicalize --db-file dataset.json \
    --providers wikidata --providers tmdb

# 4. Export to training format
media-archivist export --db-file dataset.json --canonical --format jsonl \
    --where 'duration > 300' \
    -o training_data.jsonl

# 5. Load into HuggingFace Datasets
from datasets import load_dataset
ds = load_dataset("json", data_files="training_data.jsonl")
```

### Q: Can I import metadata from yt-dlp's own metadata?

**A:** Yes. Use `media-archivist import`:

```bash
# yt-dlp outputs metadata as JSON lines
yt-dlp --dump-json https://www.youtube.com/@Channel | \
    media-archivist import --db-file my.json --format jsonl -

# Or from a file
media-archivist import --db-file my.json yt-dlp-export.jsonl
```

However, the schema differs from `media_archivist`'s raw models, so some fields may be lost. For best results, use `media-archivist add` directly.

### Q: I have a flat JSON mapping — can media_archivist read it?

**A:** Yes. A flat JSON mapping (`{url: {title, ...}, ...}`) loads transparently and is rewritten as the envelope format on the next `store()`:

```python
from media_archivist.storage import EnvelopeJsonStorage

db = EnvelopeJsonStorage("old_flat_format.json")
db.store()  # Rewritten as envelope format
```

### Q: How do I prune dead links?

**A:** Use `prune`:

```bash
# Remove entries that return 404 / are unavailable
media-archivist prune --db-file talks.json --unavailable

# Remove entries with blacklisted keywords (after-the-fact cleanup)
media-archivist prune --db-file talks.json --blacklist "sponsorblock"

# Remove entries missing critical fields
media-archivist prune --db-file talks.json --missing "duration"

# Remove entries shorter than N seconds
media-archivist prune --db-file talks.json --below 60
```

All `prune` operations are **in-place** (modify the source JSON). Always back up first.

## Environment Variables & Configuration

### Q: How do I set up TMDB / TVDB / IMDb lookups?

**A:** The keyless `skyhook` provider (Servarr's public proxy) covers
TMDB / TVDB / IMDb / MusicBrainz / OpenLibrary cross-references for
movies, TV, music, and books out of the box — no env vars required.

Then run `canonicalize`:

```bash
media-archivist canonicalize --db-file my.json \
    --providers tmdb --providers wikidata
```

Check active providers:

```bash
media-archivist providers
```

### Q: Can I extend media_archivist with my own providers?

**A:** Yes. Subclass `MetadataProvider` and register:

```python
from metadatarr.resolve.base import MetadataProvider, register, ProviderMatch

class MyProvider(MetadataProvider):
    name = "my_provider"
    media = {MediaType.MUSIC}  # or MOVIE, TV, BOOK

    def is_available(self) -> bool:
        # Check env vars or config
        return os.getenv("MY_PROVIDER_KEY") is not None

    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        # Query your API
        # Return ProviderMatch(provider="my_provider", confidence=0.9, signals=..., external_ids=...)
        ...

register(MyProvider())
```

Call `register(MyProvider())` at module import time in your own package.
`media_archivist.providers` is a thin re-export of metadatarr's registry —
do not add files into it. Any import that calls `register()` before
`canonicalize()` runs will wire the provider in correctly.

## Data Integrity & Troubleshooting

### Q: What happens if I edit the JSON directly?

**A:** You can edit the JSON manually (it's just a plain file), but:

1. **Envelope structure** — The outer `_meta` block and `entries` key must be preserved.
2. **Validation** — When you load the file with `media-archivist`, it validates against the pydantic models. Invalid entries are skipped with a warning.
3. **Rewrite** — The next `store()` (any command that modifies the DB) rewrites the file with validated entries.

Example: If you manually add an entry without a `source` field, the next save will fail or skip it.

**Recommendation:** Use the CLI to add/modify entries. Direct editing is risky.

### Q: Can I run multiple media-archivist commands concurrently?

**A:** Yes, with caveats. The JSON file is locked during read/write (via `ComboLock`), so concurrent commands serialize. Performance is fine for reasonable concurrency; for highly parallel workloads, batch operations (e.g., merge multiple DBs) instead of parallel CLI calls.

### Q: What does "Value error, prune requires at least one of: ..." mean?

**A:** You ran `prune` without specifying what to prune. Provide at least one of:

```bash
media-archivist prune --db-file talks.json --unavailable
media-archivist prune --db-file talks.json --below 60
media-archivist prune --db-file talks.json --blacklist "shorts"
media-archivist prune --db-file talks.json --missing "duration"
```

### Q: Why are some entries missing expected fields?

**A:** Different backends expose different metadata:

| Backend | title | artist | album | duration | stream | views | is_live |
|---------|-------|--------|-------|----------|--------|-------|---------|
| YouTube | ✓ | | | | | ✓ | ✓ |
| YouTube Music | ✓ | ✓ | ✓ | ✓ | | ✓ | |
| Bandcamp | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| SoundCloud | ✓ | ✓ | | ✓ | ✓ | | |
| Internet Archive | ✓ | | | ✓ | ✓ | | |

Use `--where` or `--missing` to filter:

```bash
# Keep only entries with duration
media-archivist export --db-file my.json --canonical \
    --where 'duration != None' -o with_duration.jsonl

# Drop entries missing duration
media-archivist prune --db-file my.json --missing "duration"
```

## Contributing & Support

### Q: I found a bug. How do I report it?

**A:** File an issue on GitHub: https://github.com/TigreGotico/media-archivist/issues

Include:
- Python version (`python --version`)
- `media_archivist` version (`media-archivist --version`)
- The exact command that failed
- Full error message
- Input data (sanitize sensitive URLs if needed)

### Q: Where's the development roadmap?

**A:** See [Roadmap](roadmap.md) for planned features.

### Q: Can I use this library in my project?

**A:** Yes. `media_archivist` is Apache-2.0 licensed. Import and use:

```python
from media_archivist import YoutubeArchivist, Index

archivist = YoutubeArchivist(db_path="./my_data.json")
archivist.archive("https://www.youtube.com/@Channel")

idx = Index("./my_data.json")
for entry in idx.view(grep="tutorial", limit=10):
    print(entry.title, entry.url)
```

See [CLI Architecture](cli.md) for the public API surface.
