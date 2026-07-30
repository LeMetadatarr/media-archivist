# Tutorial: Build a Cross-Source Music Dataset

This 2000+ word tutorial walks you through a realistic end-to-end workflow: index the same artist across three music sources, find and deduplicate cross-source matches, and export a canonical JSONL for use in a training pipeline or HuggingFace dataset.

By the end you will:
- Index from YouTube Music, Bandcamp, and SoundCloud
- Use fingerprinting to discover duplicates
- Export a deduplicated canonical JSONL with your preferred source hierarchy
- Load it into HuggingFace Datasets

## Part 1: Set Up and Index

This example uses Aphex Twin, a prolific artist with releases across all three platforms.

### Install the full suite

```bash
pip install media_archivist[all]
```

This installs `media_archivist` with support for YouTube Music, Bandcamp, and SoundCloud.

### Create a working directory

```bash
mkdir -p aphex_dataset
cd aphex_dataset
```

### Index YouTube Music

YouTube Music has the richest metadata: duration (seconds), album, year, artist, explicit flag, and music-video / audio-only classification.

```bash
media-archivist add --db-file aphex.json --music "Aphex Twin"
```

Expected output:

```
Searching for 'Aphex Twin'... |████████████| 85 entries
db now contains 85 entries
```

Check what we got:

```bash
media-archivist list --db-file aphex.json --limit 5 --json
```

You'll see entries like:

```json
{
  "source": "youtube_music",
  "videoId": "...",
  "url": "https://music.youtube.com/watch?v=...",
  "title": "Windowlicker",
  "artist": "Aphex Twin",
  "album": "Windowlicker",
  "year": 1999,
  "duration": 286,
  "explicit": false,
  "thumbnail": "..."
}
```

### Index Bandcamp

Bandcamp is useful here because it ships direct audio stream URLs (when available) and artist / album metadata.

```bash
media-archivist add --db-file aphex.json --bandcamp \
    "https://aphextwin.bandcamp.com"
```

This indexes the artist's official Bandcamp profile. Bandcamp entries look like:

```json
{
  "source": "bandcamp",
  "url": "https://aphextwin.bandcamp.com/track/windowlicker",
  "title": "Windowlicker",
  "artist": "Aphex Twin",
  "album": "Windowlicker",
  "duration": 286,
  "stream": "https://d4agf3fowqb0d.cloudfront.net/stream/...",
  "artwork": "..."
}
```

Note the `stream` field, a direct MP3/OGG URL.

### Index SoundCloud

SoundCloud has many user-uploaded and official releases.

```bash
media-archivist add --db-file aphex.json --soundcloud "Aphex Twin"
```

Expected output:

```
Searching for 'Aphex Twin'... |████████████| 47 entries
db now contains 117 entries (after dedup)
```

Check coverage:

```bash
media-archivist stats --db-file aphex.json
```

Output:

```
Total entries: 117
Live streams: 0
Playlists: 0
Field coverage:
  source: 117/117 (100%)
  title: 117/117 (100%)
  url: 117/117 (100%)
  duration: 102/117 (87%)
  artist: 110/117 (94%)
```

## Part 2: Fingerprint and Link Duplicates

Now we have 117 entries across three sources. Many are the same song listed multiple times. We use `media_archivist.canon.link()` to find cross-source duplicates via a fingerprint based on normalized artist and title.

### Understanding fingerprints

Two entries match if:
1. Their normalized artist and title are identical.
2. Their durations are within ±2 seconds (to tolerate edits, live versions, remixes that *should* stay separate).

For example:
- "Aphex Twin" + "Windowlicker" (286s) on YouTube Music and Bandcamp → same fingerprint
- "Aphex Twin" + "Windowlicker (live)" (302s) → different fingerprint (exceeds ±2s tolerance)
- "Aphex Twin" + "Windowlicker" (286s) on SoundCloud → same fingerprint

### Run the linker

```bash
media-archivist link --db-file aphex.json
```

This creates a sidecar file `aphex.links.json` (the source file is not modified):

```json
{
  "<sha1>": ["<id-youtube_music>", "<id-bandcamp>", "<id-soundcloud>"],
  "<sha1>:1": ["<id-youtube_music-live>", "<id-soundcloud-live>"],
  ...
}
```

Each key is a fingerprint hash. The value is a list of matching entry IDs. The suffix (`:1`, `:2`, etc.) represents duration clusters within the same fingerprint.

**The source database is untouched**, `link` only writes the sidecar.

## Part 3: Deduplicate to Canonical JSONL

Now we collapse duplicates into a single canonical JSONL, preferring sources with richer metadata and direct stream URLs.

### Source preference order

The default preference is:

```
bandcamp > internet_archive > youtube_music > soundcloud > youtube
```

This order favors:
1. **Bandcamp & IA**, ship direct stream URLs, enabling offline playback.
2. **YouTube Music**, rich metadata (album, year, explicit, duration in seconds).
3. **SoundCloud & YouTube**, general purpose, lower metadata richness.

For this music dataset, the default is perfect: Bandcamp tracks have stream URLs. YouTube Music provides quality metadata. SoundCloud is a fallback.

### Run the deduper

```bash
media-archivist dedupe --db-file aphex.json \
    --output aphex.canonical.jsonl \
    --prefer bandcamp,internet_archive,youtube_music,soundcloud,youtube
```

Expected output:

```
Loaded 117 entries from aphex.json
Linked 27 fingerprint groups (52 entries, 65 singletons)
Deduped to 92 canonical rows
Wrote 92 rows to aphex.canonical.jsonl
```

The deduped JSONL looks like:

```json
{
  "id": "sha1:...",
  "source": "bandcamp",
  "url": "https://aphextwin.bandcamp.com/track/windowlicker",
  "title": "Windowlicker",
  "artist": "Aphex Twin",
  "album": "Windowlicker",
  "year": 1999,
  "duration": 286,
  "explicit": false,
  "stream": "https://d4agf3fowqb0d.cloudfront.net/stream/...",
  "raw": {
    "source": "bandcamp",
    "url": "...",
    "artwork": "...",
    ...
  },
  "raw": {
    "alternates": [
      {
        "source": "youtube_music",
        "videoId": "...",
        "url": "https://music.youtube.com/watch?v=..."
      },
      {
        "source": "soundcloud",
        "url": "https://soundcloud.com/...",
        ...
      }
    ]
  }
}
```

Each canonical row carries:
- **Canonical fields**, the best version from the preferred source.
- **`raw.alternates`**, metadata from other sources in the fingerprint group (for reference).

### Inspect the results

```bash
# Count rows
wc -l aphex.canonical.jsonl

# Show one entry
head -1 aphex.canonical.jsonl | jq '.'

# Filter: entries with stream URLs
jq 'select(.stream != null)' aphex.canonical.jsonl | wc -l
```

Result:

```
92 aphex.canonical.jsonl
75 entries with .stream (Bandcamp + SoundCloud)
```

## Part 4: Export to CSV for Spreadsheets

For analysis or manual review, export to CSV:

```bash
media-archivist export --db-file aphex.json --format csv \
    --fields source,title,artist,album,duration,stream -o aphex.csv
```

Open in a spreadsheet:

```bash
head -20 aphex.csv | column -t -s,
```

Output:

```
source           title                artist       album              duration stream
youtube_music    Windowlicker         Aphex Twin   Windowlicker       286      (null)
bandcamp         Windowlicker         Aphex Twin   Windowlicker       286      https://...
soundcloud       Windowlicker         Aphex Twin   Windowlicker       286      https://...
youtube_music    Come to Daddy        Aphex Twin   Come to Daddy      275      (null)
...
```

## Part 5: Load into HuggingFace Datasets

You can load the JSONL into HuggingFace Datasets:

```python
from datasets import load_dataset

# Load the canonical JSONL as a HuggingFace dataset
dataset = load_dataset("json", data_files="aphex.canonical.jsonl")

print(dataset)
# DatasetDict({
#     train: Dataset({
#         features: ['id', 'source', 'url', 'title', 'artist', 'album', ...],
#         num_rows: 92
#     })
# })

# Access rows
for row in dataset["train"]:
    print(f"{row['title']} by {row['artist']} ({row['duration']}s)")
```

To push to the HuggingFace Hub (requires `huggingface_hub`):

```python
dataset.push_to_hub("your-username/aphex-twin-dataset")
```

Your dataset is now public and citable: `https://huggingface.co/datasets/your-username/aphex-twin-dataset`.

## Part 6: Working with Canonical Queries (--canonical flag)

Once you have deduplicated data, you can query the canonical view directly:

```bash
# Filter by source
media-archivist list --db-file aphex.json --canonical \
    --source bandcamp --limit 10
```

Output:

```
Windowlicker (Bandcamp)    https://aphextwin.bandcamp.com/track/...
Come to Daddy (Bandcamp)   https://aphextwin.bandcamp.com/track/...
...
```

### Use --where for complex queries

The `--where` flag accepts sandboxed Python-like expressions. Some examples:

```bash
# Entries with stream URLs (direct download)
media-archivist list --db-file aphex.json --canonical --has-stream

# Tracks over 5 minutes
media-archivist urls --db-file aphex.json --canonical \
    --where 'duration > 300'

# Explicit tracks from YouTube Music
media-archivist list --db-file aphex.json --canonical \
    --where 'source=="youtube_music" and explicit==True'

# Entries from any source except SoundCloud
media-archivist export --db-file aphex.json --canonical --format jsonl \
    --where 'source != "soundcloud"' -o no_soundcloud.jsonl
```

Available operators: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, `and`, `or`, `not`. String functions: `len()`, `lower()`, `upper()`. Denied: attribute access, list comprehensions, imports, lambdas.

## Part 7: Advanced, Per-Source Deduplication

If you want to keep cover versions and remixes *separate*, deduplicate per-source instead of globally:

```bash
# Dedupe only YouTube Music entries (ignore other sources)
media-archivist dedupe --db-file aphex.json \
    --source youtube_music \
    --output aphex_ytmusic_only.jsonl
```

This keeps the Bandcamp and SoundCloud versions separate, even if they match the fingerprint. Useful for training models on *diverse* renditions of the same work.

## Part 8: Handling Metadata Gaps

Notice that SoundCloud entries don't always include duration or artist. Use `--where` to filter them out:

```bash
media-archivist export --db-file aphex.json --canonical --format jsonl \
    --where 'duration != None and artist != None' \
    -o aphex_complete.jsonl
```

Or filter on the number of available fields:

```bash
media-archivist export --db-file aphex.json --canonical \
    --where 'len([x for x in [duration,artist,album] if x!=None]) >= 2' \
    -o aphex_enriched.jsonl
```

## Part 9: Monitoring for New Releases

Once your dataset is stable, keep it in sync with background monitoring:

```bash
# Poll Aphex Twin's official Bandcamp every 24 hours
media-archivist monitor --db-file aphex.json --interval 86400 \
    https://aphextwin.bandcamp.com
```

The monitor runs in the foreground and re-syncs on a schedule. To run as a daemon, use a system tool like `nohup` or `systemd`:

```bash
nohup media-archivist monitor --db-file aphex.json --interval 86400 \
    https://aphextwin.bandcamp.com > monitor.log 2>&1 &
```

## Part 10: Merge Multiple Datasets

If you've built separate Aphex Twin, Autechre, and Boards of Canada music datasets, merge them:

```bash
media-archivist merge --db-file electronic_music.json \
    aphex.json autechre.json boc.json \
    --overwrite
```

Now `electronic_music.json` contains all 300+ tracks. You can dedupe the combined set:

```bash
media-archivist dedupe --db-file electronic_music.json \
    --output electronic_music.canonical.jsonl
```

## Workflow Diagram

```
Step 1: Index three sources
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  YouTube Music   │  │    Bandcamp      │  │    SoundCloud    │
│   (85 entries)   │  │   (32 entries)   │  │   (47 entries)   │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │                     │                     │
         └─────────────────────┴─────────────────────┘
                        │
                 aphex.json (117 entries)
                        │
Step 2: Link duplicates by fingerprint
                        │
                aphex.links.json
                 (27 fingerprint groups)
                        │
Step 3: Deduplicate with source preference
                        │
           aphex.canonical.jsonl
             (92 canonical rows with
               alternates from other sources)
                        │
Step 4: Export and analyze
            ┌───────────┼───────────┐
            │           │           │
       aphex.csv  Python/Pandas  HuggingFace
                                  Dataset
```

## Key Takeaways

1. **Index from multiple sources**, same artist, different platforms.
2. **Fingerprint by normalized title + artist**, finds real duplicates.
3. **Dedupe with source preference**, Bandcamp > YouTube Music > SoundCloud.
4. **Export to JSONL**, one row per canonical work, alternates in `raw.alternates`.
5. **Use --canonical and --where**, query the deduplicated view without re-deduping.
6. **Load into HuggingFace**, share your dataset publicly.

## Troubleshooting

### No Bandcamp results

**Problem:** `media-archivist add --db-file aphex.json --bandcamp "Aphex Twin"` returns 0 entries.

**Cause:** Bandcamp search returns artist profiles, not individual tracks. You need an artist URL or a direct album/track link.

**Solution:** Use the direct URL: `https://aphextwin.bandcamp.com` (artist profile) or search for specific albums: `--bandcamp "Aphex Twin windowlicker"`.

### Deduplication is too aggressive

**Problem:** Different versions (live, remix, remaster) are collapsing into one canonical entry.

**Cause:** Duration is within ±2 seconds. Title normalizes away "live" / "remix" suffixes.

**Solution:** Use per-source deduplication (`--source youtube_music`) or widen the exported JSONL to include alternates and manually filter.

### Stream URLs are null

**Problem:** `stream` field is missing from Bandcamp/SoundCloud entries.

**Cause:** The entry may be a playlist, a deleted track, or the platform didn't expose a stream URL.

**Solution:** Filter with `--where 'stream != None'` or check the source directly (some Bandcamp entries don't have preview streams).

## Next Steps

1. **Canonicalize against external metadata**, Run `canonicalize` to mint canonical IDs and link to MusicBrainz, Wikidata, TMDB (see [Disambiguation](disambiguation.md)).
2. **Build ML datasets**, Load the JSONL into `datasets` and train retrieval models.
3. **Scale to playlists**, Use the same workflow for multi-source playlists (e.g., curated lo-fi beats collections).
4. **Automate updates**, Use `monitor` to re-sync periodically and push updates to HuggingFace.

---
[← Getting Started](getting-started.md) · [Home](index.md) · [FAQ →](faq.md)
