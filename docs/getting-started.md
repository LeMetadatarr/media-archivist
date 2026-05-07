# Getting Started with media_archivist

This guide gets you from zero to indexing in 10 minutes. You'll install the tool, perform your first archive operation, and pipe the results to `yt-dlp` for download.

## Installation

Choose the right variant for your use case:

```bash
# Core: YouTube, YouTube Music, Internet Archive
pip install media_archivist

# Add Bandcamp
pip install media_archivist[bandcamp]

# Add SoundCloud
pip install media_archivist[soundcloud]

# Everything
pip install media_archivist[all]
```

Verify the install:

```bash
media-archivist --version
```

## Your First Index

Create a new database and index a single YouTube channel:

```bash
media-archivist add --db-file my_first.json https://www.youtube.com/@LinusTechTips
```

Expected output (on stderr):

```
Fetching channel videos... |████████████| 42 entries
db now contains 42 entries
```

The JSON file is created and ready to inspect:

```bash
ls -lh my_first.json
```

## Browse the Results

List the 10 most recent entries:

```bash
media-archivist list --db-file my_first.json --limit 10
```

You'll see tab-separated rows of `title<TAB>url`:

```
Linus Tech Tips: 2024 CPU Review       https://www.youtube.com/watch?v=abc123
Linus Tech Tips: Linux on Laptops       https://www.youtube.com/watch?v=xyz789
...
```

For more detail, ask for JSON:

```bash
media-archivist list --db-file my_first.json --limit 3 --json
```

This dumps the full metadata for each video (title, views, description, tags, etc.).

### Search within the DB

Find all entries matching a keyword:

```bash
media-archivist list --db-file my_first.json --grep "cpu" --limit 5
```

Get statistics:

```bash
media-archivist stats --db-file my_first.json
```

Output:

```
Total entries: 42
Live streams: 2
Playlists: 1
Field coverage:
  videoId: 42/42 (100%)
  title: 42/42 (100%)
  url: 42/42 (100%)
  ...
```

## Export URLs for Download

Extract just the URLs for piping to `yt-dlp`:

```bash
media-archivist urls --db-file my_first.json > urls.txt
head urls.txt
```

Result:

```
https://www.youtube.com/watch?v=abc123
https://www.youtube.com/watch?v=xyz789
...
```

## Pipe to yt-dlp

Now download the actual videos. This is why `media_archivist` is **metadata-only** — it does the heavy lifting of listing, filtering, and deduplication so `yt-dlp` just downloads:

```bash
# Download the first 5 videos
media-archivist urls --db-file my_first.json | head -5 | yt-dlp -a -

# Download with quality control (720p max)
media-archivist urls --db-file my_first.json | yt-dlp -a - -f 'best[height<=720]'

# Download and name by title (%(title)s)
media-archivist urls --db-file my_first.json | yt-dlp -a - -o '%(title)s.%(ext)s'
```

## Filtering During Index

Use `--blacklist` to skip unwanted entries during archiving:

```bash
media-archivist add --db-file my_first.json \
    --blacklist "#shorts" --blacklist "trailer" \
    https://www.youtube.com/@LinusTechTips
```

Use `--require` to index *only* entries matching specific keywords:

```bash
media-archivist add --db-file my_first.json \
    --require "cpu" --require "review" \
    https://www.youtube.com/@LinusTechTips
```

## Understanding Backends

Each backend is activated with a flag. Here's how to pick one:

### YouTube (default)

```bash
# No flag needed — YouTube is the default
media-archivist add --db-file yt.json https://www.youtube.com/@SomeChannel
media-archivist add --db-file yt.json https://www.youtube.com/watch?v=abc123
media-archivist add --db-file yt.json https://www.youtube.com/playlist?list=PLxxx
```

Works with channels, playlists, and individual video URLs.

**Note:** Bare channel scraping does not expose video duration, so `--min-duration` is a no-op. See "Why is min_duration sometimes ignored?" in [FAQ](faq.md).

### YouTube Music

```bash
media-archivist add --db-file songs.json --music "lo-fi beats"
media-archivist add --db-file songs.json --music \
    https://music.youtube.com/playlist?list=PLxxx
```

YouTube Music entries include rich metadata: artist, album, year, duration (in seconds), explicit flag, and music video / audio-only flags.

```bash
# Skip explicit tracks
media-archivist add --db-file songs.json --music --skip-explicit "lo-fi beats"

# Keep only audio-only tracks
media-archivist add --db-file songs.json --music --only-audio "lo-fi beats"
```

### Internet Archive

```bash
media-archivist add --db-file ia_movies.json --ia classic_cartoons
media-archivist add --db-file ia_movies.json --ia Popeye_forPresident
```

Pass collection names or individual item IDs. Downloads format URLs (MPEG4, Ogg Video, etc.) are validated and stored.

### Bandcamp

Requires `pip install media_archivist[bandcamp]`.

```bash
media-archivist add --db-file bc.json --bandcamp \
    https://artistname.bandcamp.com/album/some-album

media-archivist add --db-file bc.json --bandcamp "ambient drone"
```

Bandcamp entries include direct audio stream URLs (when Bandcamp exposes them), album/track metadata, and artist info.

### SoundCloud

Requires `pip install media_archivist[soundcloud]`.

```bash
media-archivist add --db-file sc.json --soundcloud \
    https://soundcloud.com/some-artist

media-archivist add --db-file sc.json --soundcloud "footwork"
```

SoundCloud entries include artist, track metadata, and resolved stream URLs (if `resolve_streams=True` is set).

## Database Location: XDG vs Explicit Path

Two ways to store your DB:

### Option 1: Explicit file (Recommended for datasets)

```bash
media-archivist add --db-file ./my_data.json https://www.youtube.com/@SomeChannel
```

The file lives in your current directory and can be committed to git.

### Option 2: Named XDG database

```bash
media-archivist add --db my_dataset https://www.youtube.com/@SomeChannel
```

The file is auto-placed at `~/.local/share/media_archivist/my_dataset.json` and is shared across all commands using the same `--db my_dataset` name. Good for persistent, system-wide collections.

You cannot mix `--db` and `--db-file`; exactly one is required per command.

## Common Pitfalls

### "error: import failed for bandcamp backend"

**Problem:** You used `--bandcamp` but didn't install the optional dependency.

**Solution:** `pip install media_archivist[bandcamp]`

### URLs are blank in the export

**Problem:** You exported via `--format txt` or `urls` command and got empty lines.

**Cause:** Some entries (e.g., YouTube playlists indexed as entries) may have missing URLs in certain backends.

**Solution:** Check the full record with `--json` and inspect the `url` field. Filter with `--grep` if needed.

### `--min-duration` has no effect

**Problem:** You added a YouTube channel with `--min-duration 300` but short videos still appear.

**Cause:** Bare channel scraping via `tutubo` doesn't expose duration metadata. The filter is a no-op in that case.

**Solution:** Use `--music` (YT Music) for tracks with exposed duration, or use `--blacklist` to exclude videos by title pattern.

### Blacklist/require keywords are case-sensitive

**Problem:** `--blacklist "Shorts"` didn't match `#shorts`.

**Cause:** Matching is case-sensitive.

**Solution:** Use lowercase or regex-like patterns (e.g., `--blacklist "short"` to catch both).

## Next Steps

Now that you have a working database:

1. **Deduplicate:** If you've indexed the same media from multiple sources, use `link` and `dedupe` to find duplicates (see [Tutorial](tutorial.md)).

2. **Export datasets:** Convert your JSON to CSV/JSONL for ML training:
   ```bash
   media-archivist export --db-file my_first.json --format csv \
       --fields videoId,title,artist,duration,url -o dataset.csv
   ```

3. **Run canonicalization:** Add canonical IDs and external metadata from MusicBrainz / TMDB / IMDb (see [Disambiguation & external IDs](disambiguation.md)).

4. **Monitor for updates:** Keep a channel in sync with background polling:
   ```bash
   media-archivist monitor --db-file my_first.json --interval 3600 \
       https://www.youtube.com/@LinusTechTips
   ```

5. **Merge databases:** Combine multiple JSON files into one:
   ```bash
   media-archivist merge --db-file all.json ch1.json ch2.json --overwrite
   ```

For full CLI reference, see [CLI Architecture](cli.md).

6. **Run as a service (homelab):** Start the HTTP API with one command and
   connect Jellyfin, Kodi, or any M3U/RSS client:
   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   ```
   See [Running as a service](deploy.md) for details on the full HTTP surface,
   `/strm`, `/m3u`, and `/feed.rss` endpoints.
