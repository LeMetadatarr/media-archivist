# Recipe: Integrate with Arr stack (Sonarr, Radarr, Readarr, Lidarr)

Use `media_archivist` as a metadata enricher for your Arr-based media server. Index YouTube trailers/clips, canonicalize against TMDB, Wikidata, and your Arr instances, then use external IDs for linkage.

## Goal

Enhance your Arr media management by:
1. Indexing YouTube clips/trailers into media_archivist.
2. Setting up provider credentials (TMDB, Radarr, Sonarr, etc.).
3. Running canonicalize to enrich entries with IMDb IDs, TMDB IDs, runtime, etc.
4. Using external IDs to auto-link entries back to your Arr libraries.

## Prerequisites

```bash
# Install media_archivist with all backends
pip install media_archivist[all]

# Install jq for JSON inspection
apt-get install jq  # or: brew install jq

# Running Arr instances (at least one of):
# - Radarr (movies): http://localhost:7878
# - Sonarr (TV): http://localhost:8989
# - Readarr (books): http://localhost:8787
# - Lidarr (music): http://localhost:8686
```

## Step 1: Configure provider credentials

Set environment variables for the providers you want to use:

```bash
# TMDB (free API key from https://www.themoviedb.org/settings/api)
export MEDIA_ARCHIVIST_TMDB_KEY="YOUR_TMDB_API_KEY"

# Radarr (movies)
export MEDIA_ARCHIVIST_RADARR_URL="http://localhost:7878"
export MEDIA_ARCHIVIST_RADARR_KEY="YOUR_RADARR_API_KEY"

# Sonarr (TV)
export MEDIA_ARCHIVIST_SONARR_URL="http://localhost:8989"
export MEDIA_ARCHIVIST_SONARR_KEY="YOUR_SONARR_API_KEY"

# Readarr (books)
export MEDIA_ARCHIVIST_READARR_URL="http://localhost:8787"
export MEDIA_ARCHIVIST_READARR_KEY="YOUR_READARR_API_KEY"

# Lidarr (music)
export MEDIA_ARCHIVIST_LIDARR_URL="http://localhost:8686"
export MEDIA_ARCHIVIST_LIDARR_KEY="YOUR_LIDARR_API_KEY"

# Wikidata (free, no auth needed)
```

Find your Arr API keys:
- **Radarr:** Settings → General → API Key.
- **Sonarr:** Settings → General → API Key.
- **Readarr:** Settings → General → API Key.
- **Lidarr:** Settings → General → API Key.

Get your TMDB key:
1. Sign up at https://www.themoviedb.org/settings/api.
2. Copy your "API Read Access Token (v4 auth)."

Make these persistent (e.g., in `~/.bashrc` or a `.env` file):

```bash
# ~/.bashrc or ~/.zshrc
export MEDIA_ARCHIVIST_TMDB_KEY="..."
export MEDIA_ARCHIVIST_RADARR_URL="http://localhost:7878"
export MEDIA_ARCHIVIST_RADARR_KEY="..."
# ... etc
```

## Step 2: Check active providers

Verify which providers are available given your configuration:

```bash
$ media-archivist providers
```

Expected output (with full credentials):
```
Active providers:
  tmdb              — TMDB (movies, TV)
  wikidata          — Wikidata (cross-references)
  arr_radarr        — Radarr (movies)
  arr_sonarr        — Sonarr (TV shows)
  arr_readarr       — Readarr (books)
  arr_lidarr        — Lidarr (music)
```

Expected output (with partial credentials):
```
Active providers:
  tmdb              — TMDB
  wikidata          — Wikidata
  arr_radarr        — Radarr
  (Sonarr, Readarr, Lidarr require additional config)
```

## Step 3: Index trailers/clips from YouTube

Example: Index movie trailers from a YouTube channel dedicated to film clips.

```bash
# Index trailers
$ media-archivist add --db-file recipe_arr_enrichment.json \
    "https://www.youtube.com/@MovieTrailers" \
    "https://www.youtube.com/@FilmSociety"

# Check what we have
$ media-archivist stats --db-file recipe_arr_enrichment.json
```

Expected output:
```
Total entries: 342
Sources:
  youtube: 342

Field coverage:
  title: 342/342 (100%)
  description: 320/342 (94%)
```

## Step 4: Canonicalize with providers

Run disambiguation against all active providers:

```bash
$ media-archivist canonicalize --db-file recipe_arr_enrichment.json
```

Or specify providers explicitly:

```bash
$ media-archivist canonicalize --db-file recipe_arr_enrichment.json \
    --providers tmdb \
    --providers wikidata \
    --providers arr_radarr \
    --providers arr_sonarr
```

Expected output:
```
Running canonicalization against 4 providers...

Processing 342 entries:
  TMDB lookups: 342
  Wikidata lookups: 289
  Radarr lookups: 187
  Sonarr lookups: 45

Results:
  Matched (assigned canonical_id): 298
  Quarantined (conflicting signals): 8
  Unmatched (no provider response): 36
```

Check the canonical sidecar:

```bash
$ jq '.records | length' recipe_arr_enrichment.canonical.json
```

Output:
```
298
```

## Step 5: Inspect external IDs

View which entries got linked to external services:

```bash
# List entries with IMDb IDs
$ media-archivist list --db-file recipe_arr_enrichment.json --canonical \
    --where 'external_ids.imdb!=None' --limit 5 --json | jq '.[] | {title: .title, imdb: .external_ids.imdb}'
```

Expected output:
```json
{
  "title": "Oppenheimer - Official Trailer",
  "imdb": "tt15398776"
}
{
  "title": "Barbie - Teaser Trailer",
  "imdb": "tt1517268"
}
```

Or check TMDB IDs:

```bash
$ media-archivist list --db-file recipe_arr_enrichment.json --canonical \
    --where 'external_ids.tmdb_movie!=None' --limit 5 --json | \
    jq '.[] | {title, tmdb: .external_ids.tmdb_movie}'
```

## Step 6: Handle quarantined entries

Some entries may conflict with provider data (e.g., mismatched year, duration). Inspect them:

```bash
$ media-archivist quarantine-list --db-file recipe_arr_enrichment.json
```

Expected output (if any):
```
Quarantined: 8 entries

Example conflicts:
  row_id: abc123...
  local_title: "The Matrix (2023 Remake)"
  local_year: 2023
  provider_response:
    provider: tmdb
    signals:
      title: "The Matrix"
      year: 1999
  conflicts: ["year"]
```

Resolve them manually:

```bash
# Accept the provider's suggestion (use their canonical_id)
$ media-archivist quarantine-resolve --db-file recipe_arr_enrichment.json \
    --row-id abc123...

# Or reject (force a new canonical_id to keep your entry separate)
$ media-archivist quarantine-reject --db-file recipe_arr_enrichment.json \
    --row-id abc123...
```

After resolution:

```bash
$ media-archivist quarantine-list --db-file recipe_arr_enrichment.json
```

Output (once resolved):
```
Quarantine empty: 0 entries
```

## Step 7: Export enriched data for external use

Export the canonical view with external IDs:

```bash
$ media-archivist export --db-file recipe_arr_enrichment.json \
    --canonical \
    --format jsonl \
    --fields title,url,source,external_ids \
    -o recipe_arr_enrichment_linked.jsonl
```

Sample row:
```json
{
  "title": "Oppenheimer - Official Trailer",
  "url": "https://www.youtube.com/watch?v=...",
  "source": "youtube",
  "external_ids": {
    "imdb": "tt15398776",
    "tmdb_movie": "987654",
    "tmdb_id": "987654",
    "wikidata_qid": "Q12345678"
  }
}
```

## Step 8: Link back to your Arr instances (manual integration)

Use external IDs to auto-add entries to your Arr instances. Example script:

```python
# link_to_radarr.py
import json
import requests
from pathlib import Path

RADARR_URL = "http://localhost:7878"
RADARR_KEY = "<YOUR_KEY>"

JSONL_FILE = Path("recipe_arr_enrichment_linked.jsonl")

with open(JSONL_FILE) as f:
    for line in f:
        entry = json.loads(line)
        
        # Skip if no TMDB ID
        tmdb_id = entry.get("external_ids", {}).get("tmdb_movie")
        if not tmdb_id:
            continue
        
        title = entry["title"]
        imdb = entry.get("external_ids", {}).get("imdb")
        
        print(f"Adding to Radarr: {title} (TMDB: {tmdb_id}, IMDb: {imdb})")
        
        # Radarr add-movie endpoint
        response = requests.post(
            f"{RADARR_URL}/api/v3/movie",
            headers={"X-Api-Key": RADARR_KEY},
            json={
                "title": title,
                "tmdbId": int(tmdb_id),
                "monitored": True,
                "addOptions": {
                    "monitor": "movieOnly",
                    "searchForMovie": True,
                },
            },
        )
        
        if response.status_code == 201:
            print(f"  ✓ Added to Radarr")
        else:
            print(f"  ✗ Failed: {response.status_code} {response.text}")
```

Run:

```bash
$ python link_to_radarr.py
```

(Repeat for Sonarr/Readarr/Lidarr with appropriate endpoints and ID fields.)

## Step 9: Keep canonical sidecar in sync

The `recipe_arr_enrichment.canonical.json` and `recipe_arr_enrichment.quarantine.json` sidecars are automatically maintained. Re-run canonicalize periodically:

```bash
# Weekly refresh (cron)
0 2 * * 0 media-archivist canonicalize --db-file /path/to/recipe_arr_enrichment.json
```

## What to do next

- **Auto-monitor new uploads:** Set up the monitor subcommand to keep your channel index fresh.
- **Dedupe trailers:** If you have trailers indexed from multiple sources, dedupe them:
  ```bash
  media-archivist link --db-file recipe_arr_enrichment.json
  media-archivist dedupe --db-file recipe_arr_enrichment.json -o canonical.jsonl
  ```
- **Build a trailer library:** Treat trailers as a separate Radarr/Sonarr indexer by querying the canonical JSONL.
- **Track changes:** Commit `recipe_arr_enrichment.json` and the canonical sidecar to Git.

## See also

- [Disambiguation & external IDs](../disambiguation.md) — provider registry, quarantine workflow.
- [Canonical view & dedupe](../canonical.md) — external_ids model.
- [Cross-source dedup with quarantine](./cross-source-dedup-with-quarantine.md) — detailed quarantine workflow.
- Arr API docs:
  - [Radarr API](https://radarr.servarr.com/docs/api/)
  - [Sonarr API](https://sonarr.tv/docs/api/)
  - [Readarr API](https://readarr.com/docs/api/)
  - [Lidarr API](https://lidarr.servarr.com/docs/api/)
