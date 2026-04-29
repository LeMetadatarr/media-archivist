# Recipe: Automate with cron

Keep your media_archivist index fresh with daily or weekly background syncs via cron. Leverage link sidecar persistence to avoid re-scraping and optimize for re-entrancy.

## Goal

Set up automated, re-entrant syncs by:
1. Creating a shell script that adds new entries without re-indexing.
2. Persisting the link sidecar to skip redundant fingerprinting.
3. Pruning dead entries automatically.
4. Setting up cron jobs at appropriate intervals.
5. Logging results for monitoring.

## Prerequisites

```bash
# media_archivist installed
pip install media_archivist[all]

# Cron daemon (standard on Unix-like systems)
crontab -l  # verify cron is available
```

## Step 1: Create the sync script

Build a re-entrant shell script that:
- Adds new entries from upstream sources.
- Preserves the `.links.json` sidecar (so fingerprinting is incremental).
- Prunes dead videos.
- Logs all activity.

```bash
#!/bin/bash
# sync_youtube_channels.sh

set -e  # Exit on any error

# Configuration
DB_FILE="/home/user/media_archivist/channels.json"
LOG_FILE="/var/log/media_archivist_sync.log"
CHANNELS=(
    "https://www.youtube.com/@LinusTechTips"
    "https://www.youtube.com/@3Blue1Brown"
    "https://www.youtube.com/@StatQuest"
)

# Timestamp function
timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

# Logging function
log() {
    echo "[$(timestamp)] $*" >> "$LOG_FILE"
}

# Error handling
on_error() {
    log "ERROR: Sync failed at line $1"
    exit 1
}

trap 'on_error $LINENO' ERR

log "=== Starting sync ==="

# Ensure directory exists
mkdir -p "$(dirname "$DB_FILE")" "$(dirname "$LOG_FILE")"

# Add channels (this is idempotent; duplicates are skipped)
for ch in "${CHANNELS[@]}"; do
    log "Adding channel: $ch"
    media-archivist add --db-file "$DB_FILE" "$ch" 2>&1 | tee -a "$LOG_FILE"
done

# Prune dead videos
log "Pruning unavailable videos..."
media-archivist prune --db-file "$DB_FILE" --unavailable 2>&1 | tee -a "$LOG_FILE" || log "No dead videos found"

# Report stats
log "Database statistics:"
media-archivist stats --db-file "$DB_FILE" 2>&1 | tee -a "$LOG_FILE"

log "=== Sync complete ==="
```

Save it:

```bash
$ mkdir -p ~/scripts
$ cat > ~/scripts/sync_youtube_channels.sh <<'SCRIPT_EOF'
#!/bin/bash
set -e

DB_FILE="/home/user/media_archivist/channels.json"
LOG_FILE="/var/log/media_archivist_sync.log"
CHANNELS=(
    "https://www.youtube.com/@LinusTechTips"
    "https://www.youtube.com/@3Blue1Brown"
    "https://www.youtube.com/@StatQuest"
)

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(timestamp)] $*" >> "$LOG_FILE"; }
on_error() { log "ERROR: Sync failed at line $1"; exit 1; }
trap 'on_error $LINENO' ERR

log "=== Starting sync ==="
mkdir -p "$(dirname "$DB_FILE")" "$(dirname "$LOG_FILE")"

for ch in "${CHANNELS[@]}"; do
    log "Adding channel: $ch"
    media-archivist add --db-file "$DB_FILE" "$ch" 2>&1 | tee -a "$LOG_FILE"
done

log "Pruning unavailable videos..."
media-archivist prune --db-file "$DB_FILE" --unavailable 2>&1 | tee -a "$LOG_FILE" || log "No dead videos found"

log "Database statistics:"
media-archivist stats --db-file "$DB_FILE" 2>&1 | tee -a "$LOG_FILE"

log "=== Sync complete ==="
SCRIPT_EOF

chmod +x ~/scripts/sync_youtube_channels.sh
```

Test it:

```bash
$ ~/scripts/sync_youtube_channels.sh
```

Expected output:
```
$ tail /var/log/media_archivist_sync.log
[2026-04-29 12:34:56] === Starting sync ===
[2026-04-29 12:34:57] Adding channel: https://www.youtube.com/@LinusTechTips
[2026-04-29 12:35:12] Archived 21 videos (0 new, 21 already present)
[2026-04-29 12:35:13] Adding channel: https://www.youtube.com/@3Blue1Brown
[2026-04-29 12:35:28] Archived 17 videos (2 new, 15 already present)
[2026-04-29 12:35:29] Adding channel: https://www.youtube.com/@StatQuest
[2026-04-29 12:35:41] Archived 34 videos (1 new, 33 already present)
[2026-04-29 12:35:42] Pruning unavailable videos...
[2026-04-29 12:35:43] No dead videos found
[2026-04-29 12:35:44] Database statistics:
[2026-04-29 12:35:44] Total entries: 72
[2026-04-29 12:35:45] === Sync complete ===
```

The script is idempotent — adding the same channel twice doesn't duplicate entries.

## Step 2: Add to crontab

Schedule the sync:

```bash
# Edit crontab
$ crontab -e
```

Add entries. Cron syntax: `MM HH DD MM DOW CMD`

**Daily at 2 AM:**

```crontab
0 2 * * * /home/user/scripts/sync_youtube_channels.sh
```

**Every 6 hours:**

```crontab
0 */6 * * * /home/user/scripts/sync_youtube_channels.sh
```

**Every Sunday at 1 AM:**

```crontab
0 1 * * 0 /home/user/scripts/sync_youtube_channels.sh
```

**Twice daily (8 AM and 8 PM):**

```crontab
0 8,20 * * * /home/user/scripts/sync_youtube_channels.sh
```

Example full crontab:

```bash
$ crontab -e
```

```crontab
# Media Archivist sync — daily at 2 AM
0 2 * * * /home/user/scripts/sync_youtube_channels.sh

# Longer sync every Sunday at 1 AM (in case of network issues)
0 1 * * 0 timeout 1800 /home/user/scripts/sync_youtube_channels.sh || true
```

Verify:

```bash
$ crontab -l
```

Output:
```
0 2 * * * /home/user/scripts/sync_youtube_channels.sh
0 1 * * 0 timeout 1800 /home/user/scripts/sync_youtube_channels.sh || true
```

## Step 3: Optimize with link sidecar persistence

The `.links.json` sidecar stores fingerprints. If you delete it on every sync, you re-compute fingerprints unnecessarily. Preserve it:

In the script, verify the sidecar exists after sync:

```bash
#!/bin/bash
# sync_with_link_persistence.sh

set -e
DB_FILE="/path/to/channels.json"
LINKS_FILE="${DB_FILE%.json}.links.json"

# ... [add entries] ...

# Preserve and validate the link sidecar
if [ -f "$LINKS_FILE" ]; then
    log "Link sidecar present ($(stat -f%z "$LINKS_FILE" 2>/dev/null || stat -c%s "$LINKS_FILE") bytes)"
else
    log "Creating link sidecar..."
    media-archivist link --db-file "$DB_FILE"
fi

# ... [rest of script] ...
```

This ensures:
- First run: `.links.json` is created via `media-archivist link`.
- Subsequent runs: the sidecar is reused (no redundant fingerprinting).
- If you add new entries, their fingerprints are added incrementally.

## Step 4: Handle re-entrancy and locking

If multiple sync jobs overlap, they might conflict. The `json_database` library provides locking, but ensure clean exits:

```bash
#!/bin/bash
set -e

DB_FILE="/path/to/channels.json"
LOCK_FILE="/tmp/media_archivist_sync.lock"
LOG_FILE="/var/log/media_archivist_sync.log"

# Prevent overlapping runs
if [ -f "$LOCK_FILE" ]; then
    lock_age=$(( $(date +%s) - $(stat -c%Y "$LOCK_FILE") ))
    if [ "$lock_age" -lt 3600 ]; then
        log "Sync already running (lock age: ${lock_age}s)"
        exit 0
    else
        log "Stale lock found (${lock_age}s old), removing"
        rm -f "$LOCK_FILE"
    fi
fi

trap 'rm -f "$LOCK_FILE"' EXIT
touch "$LOCK_FILE"

# [rest of sync script]
```

## Step 5: Monitor sync results

View logs:

```bash
$ tail -f /var/log/media_archivist_sync.log
```

Or grep for errors:

```bash
$ grep ERROR /var/log/media_archivist_sync.log
```

Create a weekly summary:

```bash
#!/bin/bash
# weekly_sync_report.sh

LOG_FILE="/var/log/media_archivist_sync.log"

echo "=== Weekly Sync Report ==="
echo "Total syncs: $(grep -c '=== Starting sync ===' "$LOG_FILE")"
echo "Successful: $(grep -c '=== Sync complete ===' "$LOG_FILE")"
echo "Errors: $(grep -c 'ERROR:' "$LOG_FILE")"
echo ""
echo "Latest sync:"
tail -20 "$LOG_FILE"
```

Add to crontab (e.g., every Monday at 9 AM):

```crontab
0 9 * * 1 /home/user/scripts/weekly_sync_report.sh | mail -s "Media Archivist Sync Report" user@example.com
```

## Step 6: Set up incremental canonicalization (v0.3.5+)

Once your index is stable, run canonicalize on a slower schedule to enrich with external IDs:

```bash
#!/bin/bash
# canonicalize_weekly.sh

set -e

DB_FILE="/path/to/channels.json"
LOG_FILE="/var/log/media_archivist_canonicalize.log"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(timestamp)] $*" >> "$LOG_FILE"; }

log "=== Starting canonicalization ==="

media-archivist canonicalize --db-file "$DB_FILE" \
    --providers wikidata \
    --providers tmdb \
    2>&1 | tee -a "$LOG_FILE"

log "=== Canonicalization complete ==="

# Report quarantine status
log "Quarantine status:"
media-archivist quarantine-list --db-file "$DB_FILE" 2>&1 | tee -a "$LOG_FILE" || log "Quarantine empty"
```

Schedule separately:

```crontab
# Canonicalize every Sunday at 3 AM (after regular sync)
0 3 * * 0 /home/user/scripts/canonicalize_weekly.sh
```

## Step 7: Export and archive

Create regular backups of your canonical view:

```bash
#!/bin/bash
# export_canonical.sh

set -e

DB_FILE="/path/to/channels.json"
EXPORT_DIR="/home/user/media_archivist/exports"
DATE=$(date +%Y-%m-%d)

mkdir -p "$EXPORT_DIR"

# Export canonical JSONL
media-archivist export --db-file "$DB_FILE" \
    --canonical \
    --format jsonl \
    -o "$EXPORT_DIR/canonical_$DATE.jsonl"

# Also keep a rolling "latest" copy
ln -sf "canonical_$DATE.jsonl" "$EXPORT_DIR/canonical_latest.jsonl"

# Compress old exports (keep 4 weeks)
find "$EXPORT_DIR" -name "canonical_*.jsonl" -mtime +28 -delete

echo "Exported canonical view to $EXPORT_DIR/canonical_$DATE.jsonl"
```

Schedule weekly:

```crontab
# Export every Saturday at 4 AM
0 4 * * 6 /home/user/scripts/export_canonical.sh
```

## Step 8: Verify cron jobs are running

Check the system log:

```bash
# View cron execution history
$ grep CRON /var/log/syslog | tail -20

# Or on macOS:
$ log stream --predicate 'process == "cron"' --level debug
```

Or monitor output:

```bash
# Install SSMTP for email (optional)
apt-get install ssmtp

# Add to crontab:
0 2 * * * /home/user/scripts/sync_youtube_channels.sh 2>&1 | mail -s "Media Archivist Sync" user@example.com
```

## What to do next

- **Combine with yt-dlp:** Export URLs daily and feed them to a download queue:
  ```bash
  media-archivist export --db-file "$DB_FILE" --format txt -o urls_today.txt
  cat urls_today.txt | yt-dlp -a - --external-downloader aria2c
  ```

- **Monitor quarantine:** Auto-resolve trivial conflicts or alert on complex ones:
  ```bash
  media-archivist quarantine-list --db-file "$DB_FILE" | mail -s "Quarantine Alert" user@example.com
  ```

- **Track Git history:** Commit the JSON DB daily for diff-friendly versioning:
  ```bash
  cd /path/to/project && \
  cp channels.json _archive/channels_$(date +%Y-%m-%d).json && \
  git add channels.json && \
  git commit -m "Daily sync: $(date +%Y-%m-%d)"
  ```

- **Exponential backoff for network errors:** Wrap script in retry logic:
  ```bash
  for attempt in 1 2 3; do
      /home/user/scripts/sync_youtube_channels.sh && break
      sleep $((2 ** attempt))
  done
  ```

- **Test cron environment:** Cron runs with minimal environment variables. Test manually:
  ```bash
  env -i HOME="$HOME" /usr/bin/bash -c '/home/user/scripts/sync_youtube_channels.sh'
  ```

## See also

- [Documentary archive](./documentary-archive.md) — similar cron setup for batch downloads.
- [Storage format](../storage.md) — JSON structure if you need to parse results.
- [CLI architecture](../cli.md) — detailed subcommand reference.
