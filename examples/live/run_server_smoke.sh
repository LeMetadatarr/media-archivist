#!/usr/bin/env bash
# End-to-end live test: boot media-archivist serve, hit every endpoint
# from server_client.py, tear down. Reports PASS/FAIL.
#
# Requires the package installed in editable mode with the [server]
# extra (`pip install -e '.[server]' httpx`).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DB="$(mktemp --suffix=.json)"
PORT=${PORT:-18007}
LOG="$(mktemp)"

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -f "$DB" "$LOG" "${DB%.json}.tasks.json" \
          "${DB%.json}.canonical.json" \
          "${DB%.json}.quarantine.json" \
          "${DB%.json}.links.json"
}
trap cleanup EXIT

cd "$ROOT"

echo "==> seeding DB at $DB"
python - <<PY
from media_archivist.storage import EnvelopeJsonStorage
db = EnvelopeJsonStorage("$DB")
db["https://www.youtube.com/watch?v=demo1"] = {
    "source": "youtube", "url": "https://www.youtube.com/watch?v=demo1",
    "videoId": "demo1", "title": "Demo Video",
    "duration": 240, "tags": ["demo"],
}
db["https://x.bandcamp.com/track/demo"] = {
    "source": "bandcamp", "url": "https://x.bandcamp.com/track/demo",
    "title": "Demo Track", "artist": "Foo", "duration": 200,
    "stream": "https://x.bandcamp.com/stream.mp3",
}
db.store()
print("seeded:", len(db))
PY

echo "==> launching server on :$PORT"
media-archivist serve --db-file "$DB" --host 127.0.0.1 --port "$PORT" \
    > "$LOG" 2>&1 &
SERVER_PID=$!

# Wait for the server to come up.
for i in $(seq 1 40); do
    if curl -sf "http://127.0.0.1:$PORT/stats" > /dev/null; then
        break
    fi
    sleep 0.25
done
if ! curl -sf "http://127.0.0.1:$PORT/stats" > /dev/null; then
    echo "server failed to start; log:"
    cat "$LOG"
    exit 1
fi

echo "==> exercising client"
python "$HERE/../server_client.py" "http://127.0.0.1:$PORT"

echo "==> server log tail"
tail -n 10 "$LOG"

echo "==> PASS"
