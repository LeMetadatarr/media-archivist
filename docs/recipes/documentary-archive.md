# Recipe: Curated documentary archive

Index multiple documentary YouTube channels into a single database, prune unwanted titles, and export a URL list for batch downloading with yt-dlp.

## Goal

Build an offline archive of curated documentaries by:
1. Indexing several high-quality documentary channels.
2. Pruning dead/unwanted videos.
3. Exporting a clean URL list for download.
4. Optionally scheduling periodic syncs.

## Prerequisites

```bash
# Install media_archivist (YouTube is included by default)
pip install media_archivist

# Install yt-dlp for batch downloading
pip install yt-dlp

# Your download target directory
mkdir -p ~/Videos/documentaries
```

## Step 1: Identify and index channels

Choose channels known for high-quality, educational content:

```bash
CHANNELS=(
    "https://www.youtube.com/@FreeDocumentary"
    "https://www.youtube.com/@FDSpace"
    "https://www.youtube.com/@FreeDocumentaryOcean"
    "https://www.youtube.com/@FreeDocumentaryNature"
    "https://www.youtube.com/@DocumentaryStudio"
)

for ch in "${CHANNELS[@]}"; do
    media-archivist add --db-file recipe_documentaries.json "$ch"
done
```

Expected output (after all channels):
```
Archived 12 videos from https://www.youtube.com/@FreeDocumentary
Archived 15 videos from https://www.youtube.com/@FDSpace
...
Total: 89 entries stored
```

## Step 2: Inspect the index

List the first 10 documentaries:

```bash
$ media-archivist list --db-file recipe_documentaries.json --limit 10
```

Expected output:
```
title	url
Coral Reefs: Rainforests of the Sea	https://www.youtube.com/watch?v=dQw4w9WgXcQ
The Universe's Strangest Moons	https://www.youtube.com/watch?v=xyz789...
Ancient Rome: Rise and Fall	https://www.youtube.com/watch?v=abc123...
```

Check coverage:

```bash
$ media-archivist stats --db-file recipe_documentaries.json
```

Expected output:
```
Total entries: 89
  Live: 0
  Playlists indexed: 5
  Dead / unavailable: 2

Field coverage:
  title: 89/89 (100%)
  url: 89/89 (100%)
  published: 87/89 (98%)
  description: 89/89 (100%)
  duration: 89/89 (100%)
  tags: 78/89 (88%)
```

## Step 3: Prune unwanted content

Remove entries that are no longer available or match unwanted keywords:

```bash
# Remove dead videos
$ media-archivist prune --db-file recipe_documentaries.json --unavailable

# Remove videos matching certain keywords (trailers, shorts, etc.)
$ media-archivist prune --db-file recipe_documentaries.json \
    --blacklist "trailer" \
    --blacklist "#shorts" \
    --blacklist "clip"
```

After pruning:

```bash
$ media-archivist stats --db-file recipe_documentaries.json
```

Expected output:
```
Total entries: 82 (7 removed)
```

## Step 4: Filter by duration (optional)

If you only want feature-length documentaries (>45 minutes):

```bash
$ media-archivist prune --db-file recipe_documentaries.json --below 2700
```

(2700 seconds = 45 minutes)

## Step 5: Export URL list

Generate a plain-text file of all URLs for batch downloading:

```bash
$ media-archivist export --db-file recipe_documentaries.json \
    --format txt -o recipe_documentaries_urls.txt
```

Verify:

```bash
$ wc -l recipe_documentaries_urls.txt
82

$ head -5 recipe_documentaries_urls.txt
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://www.youtube.com/watch?v=xyz789
https://www.youtube.com/watch?v=abc123
https://www.youtube.com/watch?v=def456
https://www.youtube.com/watch?v=ghi789
```

## Step 6: Download with yt-dlp

Use the URL list to batch-download:

```bash
$ yt-dlp \
    -a recipe_documentaries_urls.txt \
    -o "%(title)s.%(ext)s" \
    -P ~/Videos/documentaries
```

yt-dlp options:
- `-a FILE` — read URLs from a file.
- `-o TEMPLATE` — output filename template (`%(title)s`, `%(channel)s`, etc.).
- `-P DIR` — output directory.
- `-f "best[ext=mp4]"` — prefer MP4 codec.
- `-N 4` — download 4 videos in parallel.

Monitor progress:

```bash
$ ls ~/Videos/documentaries | wc -l
# Should grow to ~82 over time
```

## Step 7: Verify your archive

Check what you've downloaded:

```bash
$ media-archivist list --db-file recipe_documentaries.json --json > recipe_documentaries.jsonl

$ wc -l recipe_documentaries.jsonl
82
```

Export richer metadata (for reference):

```bash
$ media-archivist export --db-file recipe_documentaries.json \
    --format jsonl \
    --fields videoId,title,url,published,duration,description \
    -o recipe_documentaries_metadata.jsonl
```

Sample row:
```json
{
  "videoId": "dQw4w9WgXcQ",
  "title": "Coral Reefs: Rainforests of the Sea",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "published": "2024-01-15",
  "duration": 3456,
  "description": "A stunning journey into the world of coral reefs..."
}
```

## Step 8: Set up periodic syncs (optional)

To keep your archive fresh, add a cron job that re-syncs once per week:

```bash
# Create a script
cat > ~/scripts/sync_documentaries.sh <<'EOF'
#!/bin/bash
set -e

DB="/path/to/recipe_documentaries.json"
CHANNELS=(
    "https://www.youtube.com/@FreeDocumentary"
    "https://www.youtube.com/@FDSpace"
    "https://www.youtube.com/@FreeDocumentaryOcean"
    "https://www.youtube.com/@FreeDocumentaryNature"
    "https://www.youtube.com/@DocumentaryStudio"
)

echo "$(date): Syncing documentary channels..."
for ch in "${CHANNELS[@]}"; do
    media-archivist add --db-file "$DB" "$ch" || true
done

echo "$(date): Pruning unavailable videos..."
media-archivist prune --db-file "$DB" --unavailable || true

echo "$(date): Exporting URL list..."
media-archivist export --db-file "$DB" --format txt -o "${DB%.json}_urls.txt"

echo "$(date): Done. URL list: ${DB%.json}_urls.txt"
EOF

chmod +x ~/scripts/sync_documentaries.sh
```

Add to crontab:

```bash
$ crontab -e

# Add this line (runs every Sunday at 2 AM):
0 2 * * 0 /home/user/scripts/sync_documentaries.sh >> /tmp/sync_docs.log 2>&1
```

## Step 9: Browse offline

Your downloaded documentaries are now available locally:

```bash
$ ls ~/Videos/documentaries | head -5
Coral Reefs: Rainforests of the Sea.mp4
The Universe's Strangest Moons.mp4
Ancient Rome: Rise and Fall.mp4
...
```

Open with your media player:

```bash
# VLC
vlc ~/Videos/documentaries/

# mpv (command line)
mpv ~/Videos/documentaries/

# Create an M3U playlist
ls ~/Videos/documentaries/*.mp4 | sed 's|/home/user|..|' > documentaries.m3u
```

## What to do next

- **Add tags:** Tag entries manually to organize by topic:
  ```bash
  media-archivist list --db-file recipe_documentaries.json --grep "nature" | ...
  ```

- **Integrate with Plex/Jellyfin:** Point your media server at `~/Videos/documentaries/` and let it auto-discover metadata via IMDb/TMDB.

- **Canonicalize with external IDs:** Enhance your index with IMDb IDs for better recommendations:
  ```bash
  media-archivist canonicalize --db-file recipe_documentaries.json \
      --providers tmdb --providers wikidata
  ```

- **Track changes:** Commit the JSON DB to Git alongside your download scripts for reproducibility:
  ```bash
  git add recipe_documentaries.json ~/scripts/sync_documentaries.sh
  git commit -m "Documentary archive snapshot"
  ```

## See also

- [Podcast mirror](./podcast-mirror.md) — for audio-focused collections.
- [Integrate with Arr stack](./integrate-with-arr-stack.md) — how to use Radarr for more sophisticated movie/doc management.
- [Storage format](../storage.md) — details on the JSON structure if you want to edit entries manually.
