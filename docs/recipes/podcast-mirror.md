# Recipe: Podcast mirror from Internet Archive

Index an Internet Archive collection or channel into a searchable database. Export as M3U playlist and metadata for offline listening.

## Goal

Create a self-contained podcast index from Internet Archive by:
1. Finding and indexing an IA collection (e.g., historical broadcasts, indie podcasts).
2. Exporting playable stream URLs and metadata.
3. Building an M3U playlist for your podcast player.
4. Optional: Setting up cron-based syncs for new episodes.

## Prerequisites

```bash
# Install media_archivist with Internet Archive support
pip install media_archivist

# Install internetarchive CLI for exploration (optional)
pip install internetarchive

# Podcast player (e.g., mpv, Plex, Jellyfin, or any RFC 8216 M3U-compatible player)
```

## Background: Finding IA collections

Internet Archive hosts millions of items. Common categories:

- **Broadcasts:** historical radio, news archives.
- **Podcasts:** community shows, indie audio projects.
- **Lectures:** academic talks, conferences.

Browse IA at [archive.org/search.php](https://archive.org/search.php) or use the CLI:

```bash
$ ia search 'subject:(Podcast)' --output json | jq '.[0:3]'
```

For this recipe, we'll use a real IA collection: **`podcastaddict`** (a curated list of podcast metadata).

## Step 1: Index an IA collection

```bash
# Index the collection (this fetches metadata, not audio)
$ media-archivist add --db-file recipe_podcasts.json --ia podcastaddict
```

Expected output:
```
Archived 156 items from collection 'podcastaddict'
Stored 156 entries
```

Verify:

```bash
$ media-archivist stats --db-file recipe_podcasts.json
```

Expected output:
```
Total entries: 156
Sources:
  internet_archive: 156

Field coverage:
  title: 156/156 (100%)
  url: 156/156 (100%)
  published: 124/156 (79%)
  description: 156/156 (100%)
  duration: 0/156 (0%)    # IA items don't expose duration pre-fetch
  tags: 89/156 (57%)
```

## Step 2: Inspect the index

List entries:

```bash
$ media-archivist list --db-file recipe_podcasts.json --limit 5
```

Expected output:
```
title	url
BrainStuff Daily	https://archive.org/details/brainstuff_daily
Stuff to Blow Your Mind	https://archive.org/details/stuff_to_blow_your_mind
Stuff You Missed in History Class	https://archive.org/details/stuffyoumissed...
The Stuff You Should Know Podcast	https://archive.org/details/stuff_you_should_know
More Perfect	https://archive.org/details/radiolabmoreperfect
```

Search for specific podcasts:

```bash
$ media-archivist list --db-file recipe_podcasts.json --grep "history"
```

Output:
```
title	url
Stuff You Missed in History Class	https://archive.org/details/stuffyoumissed...
Our Fake History	https://archive.org/details/ourfakehistory
```

## Step 3: Prune by keyword (optional)

Remove entries you're not interested in:

```bash
# Remove music-focused items
$ media-archivist prune --db-file recipe_podcasts.json \
    --blacklist "music" \
    --blacklist "remix"
```

Stats after pruning:

```bash
$ media-archivist stats --db-file recipe_podcasts.json
```

## Step 4: Export to JSONL

Create a machine-readable index:

```bash
$ media-archivist export --db-file recipe_podcasts.json \
    --format jsonl \
    --fields title,url,description,tags \
    -o recipe_podcasts_metadata.jsonl
```

Sample row:
```json
{
  "title": "Stuff You Missed in History Class",
  "url": "https://archive.org/details/stuffyoumissed...",
  "description": "Science Stuff Podcast Network. From the producers of Stuff You Should Know...",
  "tags": ["podcast", "history", "educational"]
}
```

## Step 5: Build M3U playlist

Create a playlist file compatible with most audio players (Plex, Jellyfin, mpv, VLC):

```bash
# Extract URLs in M3U3 format (basic URL list)
$ cat > recipe_podcasts.m3u << 'EOF'
#EXTM3U
EOF

$ media-archivist export --db-file recipe_podcasts.json --format txt \
    >> recipe_podcasts.m3u
```

Or with metadata (extended M3U):

```python
# create_m3u.py
import json
import sys

with open("recipe_podcasts.json") as f:
    data = json.load(f)

with open("recipe_podcasts_extended.m3u", "w") as out:
    out.write("#EXTM3U\n")
    for entry in data.get("entries", {}).values():
        # IA items don't expose duration, use -1
        out.write(f"#EXTINF:-1,{entry['title']}\n")
        out.write(f"{entry['url']}\n")

print(f"Wrote recipe_podcasts_extended.m3u")
```

Run:

```bash
$ python create_m3u.py
```

Verify:

```bash
$ head -10 recipe_podcasts_extended.m3u
#EXTM3U
#EXTINF:-1,BrainStuff Daily
https://archive.org/details/brainstuff_daily
#EXTINF:-1,Stuff to Blow Your Mind
https://archive.org/details/stuff_to_blow_your_mind
```

## Step 6: Import to your podcast player

### Option A: Jellyfin (self-hosted media server)

1. Point Jellyfin's music/podcast library at an IA mirror or download directory.
2. Enable "Scan library on startup."
3. Browse via the web UI.

Alternatively, add the playlist:

```bash
curl -X POST http://localhost:8096/Playlists \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "Name": "Podcast Archive",
  "Ids": [],
  "PlaylistMediaType": "Audio"
}
EOF
```

### Option B: Plex

Add the M3U via:
1. Settings → Libraries → Add Library → Music.
2. Add folder containing your M3U.
3. Or use the web UI to import the playlist directly.

### Option C: mpv (command line)

```bash
# Play the entire playlist
$ mpv --playlist=recipe_podcasts.m3u

# Or shuffle
$ mpv --playlist=recipe_podcasts.m3u --shuffle
```

### Option D: VLC

File → Open → recipe_podcasts.m3u (or drag and drop).

## Step 7: Download for offline use (optional)

If you want local copies (Note: Internet Archive items may have usage rights; check before downloading):

```bash
# Extract just the URLs
$ media-archivist export --db-file recipe_podcasts.json --format txt \
    -o recipe_podcasts_urls.txt

# Download with yt-dlp
$ yt-dlp -a recipe_podcasts_urls.txt \
    -o "%(title)s.%(ext)s" \
    -P ~/Podcasts/archive
```

(yt-dlp can extract audio from many IA item pages.)

## Step 8: Set up periodic syncs

Refresh the index periodically to detect new episodes:

```bash
# Refresh the index monthly
$ media-archivist add --db-file recipe_podcasts.json --ia podcastaddict
```

Changes (new items) are automatically merged; duplicates are skipped.

## What to do next

- **Filter by publication date:** Find recent episodes only:
  ```bash
  media-archivist list --db-file recipe_podcasts.json \
      --where 'published>"2024-01"'
  ```
  (Note: `published` field varies by IA item; may be relative or missing.)

- **Combine with other sources:** Index a YouTube channel of podcast episodes alongside IA:
  ```bash
  media-archivist add --db-file recipe_podcasts.json https://www.youtube.com/@SomePodcast
  ```

- **Canonicalize for deduplication:** If you're combining multiple sources, link and dedupe:
  ```bash
  media-archivist link --db-file recipe_podcasts.json
  media-archivist dedupe --db-file recipe_podcasts.json -o recipe_podcasts_canonical.jsonl
  ```

- **Track metadata changes:** Commit the JSON DB to Git for version control:
  ```bash
  git add recipe_podcasts.json recipe_podcasts.m3u
  git commit -m "Podcast archive snapshot"
  ```

## See also

- [Documentary archive](./documentary-archive.md) — similar workflow for video content.
- [Cross-source dedup with quarantine](./cross-source-dedup-with-quarantine.md) — for deduping if you combine IA with YouTube podcasts.
- [Storage format](../storage.md) — IA entry schema.
