#!/usr/bin/env bash
# Build a "free documentaries" dataset from three YouTube channels.
#
# Usage: ./index_documentaries.sh
# Produces: documentaries.json, documentaries.csv, documentaries.jsonl, urls.txt

set -euo pipefail

DB="$(dirname "$0")/documentaries.json"

CHANNELS=(
    "https://www.youtube.com/@FreeDocumentary"
    "https://www.youtube.com/@FDSpace"
    "https://www.youtube.com/@FreeDocumentaryOcean"
)

echo "==> indexing ${#CHANNELS[@]} channels into $DB"
media-archivist add --db-file "$DB" \
    --blacklist "#shorts" \
    "${CHANNELS[@]}"

echo "==> stats"
media-archivist stats --db-file "$DB"

echo "==> exporting CSV (videoId, title, url, published, tags, description)"
media-archivist export --db-file "$DB" --format csv \
    --fields videoId,title,url,published,tags,description \
    -o "$(dirname "$0")/documentaries.csv"

echo "==> exporting JSONL (full records)"
media-archivist export --db-file "$DB" --format jsonl \
    -o "$(dirname "$0")/documentaries.jsonl"

echo "==> exporting flat URL list"
media-archivist export --db-file "$DB" --format txt \
    -o "$(dirname "$0")/urls.txt"

echo "==> done. Pipe urls.txt into yt-dlp to download:"
echo "    yt-dlp -a $(dirname "$0")/urls.txt"
