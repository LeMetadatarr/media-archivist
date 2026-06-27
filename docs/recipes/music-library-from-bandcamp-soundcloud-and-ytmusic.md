# Recipe: Cross-source music library

Build a unified music index from YouTube Music, Bandcamp, and SoundCloud for the same artist. Fingerprint duplicates, dedupe, and export canonical JSONL.

## Goal

Create a searchable, cross-platform music dataset for an artist by:
1. Indexing the artist on three separate sources.
2. Linking cross-source duplicates via fingerprinting.
3. Deduping to prefer sources with direct stream URLs.
4. Exporting to JSONL for consumption by music apps or ML pipelines.

## Prerequisites

```bash
# Install all backends
pip install media_archivist[all]

# Your favorite artist (we'll use Aphex Twin as the example)
export ARTIST="Aphex Twin"
```

## Step 1: Index the artist on YouTube Music

```bash
$ media-archivist add --db-file recipe_music_library.json --music \
    "Aphex Twin"
```

Expected output:
```
Archived search results for 'Aphex Twin' (27 tracks found)
Stored 27 entries
```

The database now contains YouTube Music entries with rich metadata:

```bash
$ media-archivist list --db-file recipe_music_library.json --limit 3 --json | head -50
```

Sample entry:
```json
{
  "source": "youtube_music",
  "url": "https://music.youtube.com/watch?v=...",
  "videoId": "xyz123",
  "title": "Windowlicker",
  "artist": "Aphex Twin",
  "album": "Windowlicker",
  "year": 1999,
  "duration": 234,
  "explicit": true,
  "thumbnail": "https://lh3.googleusercontent.com/...",
  "published": "1999-03-08",
  "tags": ["electronic", "glitch", "IDM"]
}
```

## Step 2: Index the artist on Bandcamp

```bash
$ media-archivist add --db-file recipe_music_library.json --bandcamp \
    "Aphex Twin"
```

Expected output:
```
Archived search results for 'Aphex Twin' (12 tracks found)
Stored 39 entries (27 from before + 12 new)
```

Bandcamp entries carry a direct `stream` URL (when available):

```bash
$ media-archivist list --db-file recipe_music_library.json \
    --source bandcamp --limit 2 --json
```

Sample:
```json
{
  "source": "bandcamp",
  "url": "https://aphextwin.bandcamp.com/track/...",
  "title": "On",
  "artist": "Aphex Twin",
  "album": "Drukqs",
  "track_number": 5,
  "duration": 312,
  "stream": "https://a.bcbits.com/b/...",
  "thumbnail": "https://..."
}
```

## Step 3: Index the artist on SoundCloud

```bash
$ media-archivist add --db-file recipe_music_library.json --soundcloud \
    "Aphex Twin"
```

Expected output:
```
Archived search results for 'Aphex Twin' (18 tracks found)
Stored 57 entries (39 from before + 18 new)
```

## Step 4: Check overall coverage

```bash
$ media-archivist stats --db-file recipe_music_library.json
```

Expected output:
```
Total entries: 57
Sources:
  youtube_music: 27
  bandcamp: 12
  soundcloud: 18

Fields with coverage:
  title: 57/57 (100%)
  artist: 55/57 (96%)
  duration: 57/57 (100%)
  stream: 12/57 (21%)
```

## Step 5: Link cross-source duplicates

Fingerprinting groups by normalized `(artist, title)` and clusters by duration tolerance (±2 seconds by default):

```bash
$ media-archivist link --db-file recipe_music_library.json
```

Expected output:
```
Fingerprint computed. Wrote /path/to/recipe_music_library.links.json
```

Inspect the sidecar to see which tracks linked:

```bash
$ jq 'keys | length' recipe_music_library.links.json
```

Output:
```
28
```

This means 28 unique fingerprint groups. Examine a few:

```bash
$ jq '.[keys[0:3]]' recipe_music_library.links.json
```

Sample (a track that appeared on 2 sources):
```json
{
  "a1b2c3d4e5f6g7h8i9j0": [
    "yt_mus_xyz123",
    "bc_on_312"
  ]
}
```

## Step 6: Dedupe to canonical JSONL

Deduplication prefers sources in order: bandcamp > internet_archive > youtube_music > soundcloud > youtube (because Bandcamp and IA ship direct stream URLs):

```bash
$ media-archivist dedupe --db-file recipe_music_library.json \
    --output recipe_music_canonical.jsonl \
    --prefer bandcamp,internet_archive,youtube_music,soundcloud,youtube
```

Expected output:
```
Deduplicated 57 entries into 28 canonical MediaEntry rows
Wrote 28 rows to recipe_music_canonical.jsonl
```

Inspect the canonical output:

```bash
$ wc -l recipe_music_canonical.jsonl
```

Output:
```
28
```

Check a sample row:

```bash
$ head -1 recipe_music_canonical.jsonl | jq .
```

Sample canonical entry:
```json
{
  "id": "a1b2c3d4e5f6g7h8i9j0",
  "source": "bandcamp",
  "url": "https://aphextwin.bandcamp.com/track/on",
  "title": "On",
  "artist": "Aphex Twin",
  "album": "Drukqs",
  "duration": 312,
  "year": null,
  "tags": ["IDM"],
  "explicit": false,
  "thumbnail": "https://...",
  "published": null,
  "is_live": false,
  "stream": "https://a.bcbits.com/b/...",
  "raw": {
    "source": "bandcamp",
    "alternates": [
      {
        "source": "youtube_music",
        "videoId": "abc123",
        "duration": 310,
        "url": "https://music.youtube.com/watch?v=..."
      }
    ]
  }
}
```

The `raw.alternates` field records which entries were collapsed (non-preferred sources).

## Step 7: Export for consumption

### Option A: Plain text (URLs only)

```bash
$ media-archivist export --db-file recipe_music_library.json \
    --format txt --canonical -o recipe_music_urls.txt
```

Inspect:
```bash
$ head -5 recipe_music_urls.txt
```

Output:
```
https://aphextwin.bandcamp.com/track/on
https://aphextwin.bandcamp.com/track/hello
https://music.youtube.com/watch?v=xyz123
https://soundcloud.com/aphextwin/something
https://music.youtube.com/watch?v=abc456
```

### Option B: CSV for spreadsheets or pandas

```bash
$ media-archivist export --db-file recipe_music_library.json \
    --format csv --canonical --canonical-only \
    --fields title,artist,album,duration,year,source,url \
    -o recipe_music.csv
```

Inspect:
```bash
$ head -3 recipe_music.csv
```

Output:
```
title,artist,album,duration,year,source,url
On,Aphex Twin,Drukqs,312,2001,bandcamp,https://aphextwin.bandcamp.com/track/on
Hello,Aphex Twin,Drukqs,287,2001,bandcamp,https://aphextwin.bandcamp.com/track/hello
```

### Option C: Load into Hugging Face datasets

```bash
$ pip install datasets

$ media-archivist export --db-file recipe_music_library.json \
    --format jsonl --canonical -o recipe_music_hf.jsonl
```

Then in Python:

```python
from datasets import Dataset

ds = Dataset.from_json("recipe_music_hf.jsonl")
print(f"Loaded {len(ds)} canonical entries")

# Project to just the fields you need for your app
ds = ds.map(lambda r: {
    "id": r["id"],
    "title": r["title"],
    "artist": r["artist"],
    "duration": r["duration"],
    "url": r["url"],
    "stream": r.get("stream"),
}, remove_columns=ds.column_names)

print(ds)
# Dataset({
#     features: ['id', 'title', 'artist', 'duration', 'url', 'stream'],
#     num_rows: 28
# })

# Optionally push to Hugging Face Hub
# ds.push_to_hub("your-username/aphex-twin-music-index")
```

## Step 8: Refresh the index

When you want to re-sync with the upstream sources (e.g., weekly), just re-run step 1–3. The CLI automatically merges new entries:

```bash
$ media-archivist add --db-file recipe_music_library.json --music "Aphex Twin"
$ media-archivist add --db-file recipe_music_library.json --bandcamp "Aphex Twin"
$ media-archivist add --db-file recipe_music_library.json --soundcloud "Aphex Twin"
```

Then repeat the link and dedupe steps. The sidecar files (`.links.json`) are automatically refreshed.

## What to do next

- **Filter by duration:** Add `--min-duration 180` (in seconds) to index only tracks longer than 3 minutes.
- **Resolve stream URLs:** For SoundCloud, pass `--soundcloud --resolve-streams` to fetch playable HLS URLs.
- **Canonicalize with MusicBrainz:** Once you have your canonical JSONL, pipe it through:
  ```bash
  media-archivist canonicalize --db-file recipe_music_library.json \
      --providers musicbrainz --providers wikidata
  ```
  This will lookup MusicBrainz recording / release / artist IDs (see [Disambiguation & external IDs](../disambiguation.md)).
- **Integrate with music players:** The canonical JSONL is a standard format — load it into Subsonic, Funkwhale, or any music app that accepts JSON playlists.
- **Track changes:** Commit `recipe_music_library.json` and `recipe_music_canonical.jsonl` to Git. Changes to upstream metadata will show as clean diffs.

## See also

- [Canonical view & dedupe](../canonical.md) — deeper dive into fingerprinting and duration tolerance.
- [`examples/cross_source_dataset.py`](../../examples/cross_source_dataset.py) — programmatic equivalent.
