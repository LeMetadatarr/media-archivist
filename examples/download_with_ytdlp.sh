#!/usr/bin/env bash
# On-demand download: filter the index, pipe URLs into yt-dlp.
#
# Usage: ./download_with_ytdlp.sh "search term" [output_dir]

set -euo pipefail

DB="$(dirname "$0")/documentaries.json"
QUERY="${1:-ocean}"
OUTDIR="${2:-$(dirname "$0")/downloads}"

mkdir -p "$OUTDIR"

media-archivist urls --db-file "$DB" --grep "$QUERY" \
    | yt-dlp -a - \
        -o "$OUTDIR/%(channel)s/%(title)s [%(id)s].%(ext)s" \
        --download-archive "$OUTDIR/.archive.txt" \
        --no-overwrites
