# Recipe: Cross-source dedup with quarantine workflow

Combine media from multiple sources into one index, then resolve ambiguities via the quarantine system. Walk through disambiguation, resolve conflicting rows, and produce a clean canonical dataset.

## Goal

Merge multiple cross-source indexes while maintaining data integrity by:
1. Indexing the same content from multiple sources (YouTube, YouTube Music, Bandcamp, SoundCloud, Internet Archive).
2. Running canonicalize to detect conflicts.
3. Inspecting quarantined rows where provider data disagrees.
4. Resolving quarantines (accepting provider suggestions or rejecting them).
5. Exporting a clean canonical view.

## Prerequisites

```bash
# Install with all backends
pip install "media_archivist[all]" py_bandcamp nuvem_de_som

# wikidata / musicbrainz / skyhook are free — no config needed
```

## Step 1: Build a multi-source index

Index the same artist on multiple sources to create cross-source duplicates:

```bash
# Index on YouTube Music (audio)
$ media-archivist add --db-file recipe_dedup_test.json --music "Bjork"

# Index on Bandcamp (audio + commercial)
$ media-archivist add --db-file recipe_dedup_test.json --bandcamp "Bjork"

# Index on SoundCloud (audio + remixes)
$ media-archivist add --db-file recipe_dedup_test.json --soundcloud "Bjork"

# Index on Internet Archive (audio archive)
$ media-archivist add --db-file recipe_dedup_test.json --ia "bjork_albums"
```

Check coverage:

```bash
$ media-archivist stats --db-file recipe_dedup_test.json
```

Expected output:
```
Total entries: 127
Sources:
  youtube_music: 34
  bandcamp: 28
  soundcloud: 41
  internet_archive: 24

Field coverage:
  title: 127/127 (100%)
  artist: 121/127 (96%)
  duration: 89/127 (70%)
  year: 34/127 (27%)
```

## Step 2: Link cross-source fingerprints

Compute fingerprints to find potential duplicates:

```bash
$ media-archivist link --db-file recipe_dedup_test.json
```

Expected output:
```
Fingerprint computed. Wrote /path/to/recipe_dedup_test.links.json
```

Inspect the links sidecar:

```bash
$ jq 'to_entries | map(select(.value | length > 1)) | length' recipe_dedup_test.links.json
```

Output (groups with 2+ entries):
```
22
```

Sample multi-source matches:

```bash
$ jq 'to_entries | map(select(.value | length > 1)) | .[0:2]' recipe_dedup_test.links.json
```

Output:
```json
[
  {
    "key": "a1b2c3d4e5f6...",
    "value": [
      "ytm_xyz",
      "bc_one",
      "sc_two"
    ]
  },
  {
    "key": "b2c3d4e5f6g7...",
    "value": [
      "bc_album_1",
      "ytm_abc",
      "ia_bjork_def"
    ]
  }
]
```

This tells us:
- Entry `ytm_xyz` (YouTube Music) is a duplicate of Bandcamp `bc_one` and SoundCloud `sc_two`.
- Entry `bc_album_1` (Bandcamp) shares the fingerprint with YouTube Music `ytm_abc` and IA `ia_bjork_def`.

## Step 3: Run canonicalize

Query external metadata providers to find official IDs and detect conflicts:

```bash
$ media-archivist canonicalize --db-file recipe_dedup_test.json \
    --providers wikidata --providers musicbrainz
```

Expected output:
```
Running canonicalization against 2 providers...

Processing 127 entries:
  Wikidata lookups: 127
  MusicBrainz lookups: 98

Results:
  Matched (assigned canonical_id): 89
  Quarantined (conflicting signals): 6
  Unmatched (no provider response): 32
```

The 6 quarantined entries are ones where our local metadata disagrees with the provider.

## Step 4: Inspect the quarantine

List quarantined entries:

```bash
$ media-archivist quarantine-list --db-file recipe_dedup_test.json
```

Expected output:
```
Quarantine status: 6 entries

Entry 1:
  row_id: 9f8e7d6c5b4a...
  local_title: "Post-Punk (Remaster 2020)"
  local_artist: "Bjork"
  local_year: 2020
  local_duration: 2456

  Provider match (wikidata):
    title: "Post-Punk"
    artist: "Bjork"
    year: 1993
    duration: 2340

  Conflicts: ["year", "duration"]
  Confidence: 0.88

Entry 2:
  row_id: a0b1c2d3e4f5...
  local_title: "Debut (Reissue 2021)"
  local_artist: "Bjork"
  local_year: 2021
  
  Provider match (musicbrainz):
    title: "Debut"
    artist: "Bjork"
    year: 1993

  Conflicts: ["year"]
  Confidence: 0.92
```

## Step 5: Understand conflict types

Conflicts occur when:

| Signal | Tolerance | Conflict example |
| --- | --- | --- |
| `title` | ≥ 0.92 fuzzy | "Post-Punk" vs "Post Punk" — no conflict |
| | | "Debut: Reissue" vs "Debut" — no conflict (fuzzy match) |
| | | "Debut" vs "Mezmerize" — conflict! |
| `artist` | ≥ 0.90 fuzzy | "Bjork" vs "Björk" — no conflict |
| | | "Bjork" vs "Bjork & Trent Reznor" — conflict! |
| `year` | ±1 | 2020 vs 2021 — no conflict |
| | | 2020 vs 1993 — **CONFLICT** (delta > 1) |
| `duration` | ±5 sec | 2340 vs 2345 — no conflict |
| | | 2340 vs 2456 — **CONFLICT** (delta > 5 sec) |
| `country` | exact ISO 3166 | "IS" vs "is" — no conflict |
| | | "IS" vs "US" — conflict! |
| `medium` | exact enum | `music` vs `music` — no conflict |
| | | `music` vs `podcast` — conflict! |
| `language` | exact ISO 639-1 | "en" vs "fr" — conflict! |

See [Disambiguation & external IDs](../disambiguation.md) for full details.

## Step 6: Resolve quarantined rows (Option A: Accept)

Accept the provider's suggestion to merge under the provider's canonical_id:

```bash
# Accept entry 1's provider suggestion (Wikidata ID for the original album)
$ media-archivist quarantine-resolve --db-file recipe_dedup_test.json \
    --row-id 9f8e7d6c5b4a...
```

Expected output:
```
Resolved row_id 9f8e7d6c5b4a...
  → linked to canonical_id c5b4a3029f8e...
  → external_ids.musicbrainz_recording: "mb-rec-xxx"
  → external_ids.wikidata_qid: "Q1234567"
Moved from quarantine to canonical.
```

The entry is now marked as `canonical_status: "matched"` and included in the canonical view.

## Step 7: Resolve quarantined rows (Option B: Reject)

Reject the provider's suggestion to keep your local entry distinct (as a separate work):

Use this when:
- The local entry is a remix, remaster, or live version that genuinely differs.
- You want to keep both the original and the variant.
- The provider data is wrong.

```bash
# Reject entry 2 (keep the 2021 Reissue distinct from the 1993 Debut)
$ media-archivist quarantine-reject --db-file recipe_dedup_test.json \
    --row-id a0b1c2d3e4f5...
```

Expected output:
```
Rejected row_id a0b1c2d3e4f5...
  → allocated new canonical_id: e4f5a0b1c2d3... (salted to avoid collision)
  → canonical_status: "matched"
```

This creates a *new* canonical_id distinct from the provider's suggestion, ensuring the reissue is treated as a separate work.

## Step 8: Check quarantine status

Verify all quarantines are resolved:

```bash
$ media-archivist quarantine-list --db-file recipe_dedup_test.json
```

Expected output (after resolving all 6):
```
Quarantine empty: 0 entries

All entries have canonical status:
  matched (accepted or rejected): 95
  unmatched (no provider response): 32
```

## Step 9: Verify canonical sidecars

Check the canonical and quarantine sidecars:

```bash
# Canonical sidecar (now contains all 95 matched records)
$ jq '.records | length' recipe_dedup_test.canonical.json
95

# Quarantine sidecar (now empty)
$ jq '.entries | length' recipe_dedup_test.quarantine.json
0
```

Inspect a canonical record:

```bash
$ jq '.records | .[0]' recipe_dedup_test.canonical.json
```

Sample:
```json
{
  "canonical_id": "c5b4a3029f8e...",
  "signals": {
    "title": "Post-Punk",
    "artist": "Bjork",
    "year": 1993,
    "duration": 2340,
    "medium": "music",
    "language": "en"
  },
  "members": ["9f8e7d6c5b4a...", "ytm_xyz", "bc_one"],
  "external_ids": {
    "musicbrainz_recording": "mb-rec-xxx",
    "wikidata_qid": "Q1234567",
    "musicbrainz_release": "mb-rel-yyy"
  },
  "provider_log": [
    {
      "provider": "wikidata",
      "timestamp": "2026-04-29T12:34:56Z",
      "confidence": 0.94,
      "signals_matched": 5,
      "signals_total": 6
    }
  ]
}
```

The `members` array lists all source rows that collapsed into this canonical record.

## Step 10: Export canonical view

Generate a deduplicated JSONL:

```bash
$ media-archivist export --db-file recipe_dedup_test.json \
    --canonical \
    --format jsonl \
    -o recipe_dedup_test_canonical.jsonl
```

Verify:

```bash
$ wc -l recipe_dedup_test_canonical.jsonl
127  # Raw entries

$ jq '.raw.alternates | length' recipe_dedup_test_canonical.jsonl | \
    jq -s 'map(. // 0) | add'
32   # Entries collapsed (alternates)

# So: 127 - 32 = 95 unique works
```

Sample canonical entry (with alternates):

```bash
$ head -1 recipe_dedup_test_canonical.jsonl | jq .
```

Output:
```json
{
  "id": "c5b4a3029f8e...",
  "source": "bandcamp",
  "url": "https://bjork.bandcamp.com/album/post-punk",
  "title": "Post-Punk",
  "artist": "Bjork",
  "album": "Post-Punk",
  "duration": 2340,
  "year": 1993,
  "tags": ["synth-pop", "experimental"],
  "explicit": false,
  "thumbnail": "https://...",
  "canonical_id": "c5b4a3029f8e...",
  "canonical_status": "matched",
  "external_ids": {
    "musicbrainz_recording": "mb-rec-xxx",
    "wikidata_qid": "Q1234567"
  },
  "raw": {
    "source": "bandcamp",
    "alternates": [
      {
        "source": "youtube_music",
        "videoId": "ytm_xyz",
        "url": "https://music.youtube.com/watch?v=...",
        "duration": 2338,
        "year": 1993
      },
      {
        "source": "soundcloud",
        "url": "https://soundcloud.com/bjork-rec/post-punk",
        "duration": 2340
      }
    ]
  }
}
```

The `alternates` field preserves all non-preferred source entries (in case you need to revert the dedup decision).

## Step 11: Load the canonical dataset

Use the deduplicated JSONL:

```python
import json
from pathlib import Path

jsonl_file = Path("recipe_dedup_test_canonical.jsonl")

with open(jsonl_file) as f:
    canonical_entries = [json.loads(line) for line in f]

print(f"Loaded {len(canonical_entries)} canonical entries")

# Filter to Bandcamp + YouTube Music (preferred sources)
preferred = [e for e in canonical_entries if e["source"] in ("bandcamp", "youtube_music")]
print(f"  Preferred sources: {len(preferred)}")

# Entries with all metadata
complete = [e for e in canonical_entries if e.get("duration") and e.get("year")]
print(f"  Complete metadata: {len(complete)}")

# Entries with external IDs
with_ids = [e for e in canonical_entries if e.get("external_ids")]
print(f"  With external IDs: {len(with_ids)}")
```

Expected output:
```
Loaded 95 canonical entries
  Preferred sources: 63
  Complete metadata: 87
  With external IDs: 89
```

## What to do next

- **Re-run canonicalize:** If you add new entries, re-run canonicalize to refresh canonical IDs and detect new conflicts.
  ```bash
  media-archivist add --db-file recipe_dedup_test.json --music "Bjork"
  media-archivist canonicalize --db-file recipe_dedup_test.json --providers wikidata
  ```

- **Use external IDs:** Feed the external IDs into downstream systems (Lidarr, Plex, music players):
  ```bash
  media-archivist list --db-file recipe_dedup_test.json --canonical \
      --where 'external_ids.musicbrainz_release!=None' --json | \
      jq '.[] | {title, artist, mbid: .external_ids.musicbrainz_release}'
  ```

- **Track quarantine history:** Commit canonical and quarantine sidecars to Git to track resolution decisions.

- **Build a recommender:** Use canonical entries to train a music recommendation model (filtered by external_ids).

## See also

- [Disambiguation & external IDs](../disambiguation.md) — full provider registry, signal tolerances.
- [Canonical view & dedupe](../canonical.md) — fingerprinting, duration clustering.
- [Cross-source music library](./music-library-from-bandcamp-soundcloud-and-ytmusic.md) — simpler multi-source workflow without disambiguation.
