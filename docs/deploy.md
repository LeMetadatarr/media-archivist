# Running as a service

`media-archivist serve` boots the FastAPI HTTP surface
([reference](./reference/cli.md#serve)). For long-lived deployments the
repository ships two ready-to-use templates under `deploy/`.

## Docker

```bash
docker build -t media-archivist -f deploy/Dockerfile .
docker run -d --name media-archivist \
    -p 8000:8000 \
    -v "$HOME/media-archivist-data:/data" \
    media-archivist
```

The container runs `media-archivist serve --host 0.0.0.0` against
`/data/index.json`. Mount a host directory at `/data` to persist the
DB plus all sidecars (`<db>.canonical.json`, `<db>.quarantine.json`,
`<db>.tasks.json`, `<db>.links.json`).

`yt-dlp` is preinstalled so transcript enrichment works out of the
box.

```bash
docker run -d --name media-archivist \
    -p 8000:8000 \
    -v "$HOME/media-archivist-data:/data" \
    media-archivist
```

## Systemd (per-user)

```bash
sudo cp deploy/media-archivist.service /etc/systemd/system/
sudo systemctl daemon-reload
systemctl --user enable --now media-archivist@$USER.service
journalctl --user -fu media-archivist@$USER.service
```

The unit reads its DB path / host / port from environment variables;
override them via:

```bash
mkdir -p ~/.config/systemd/user/media-archivist@.service.d
cat > ~/.config/systemd/user/media-archivist@.service.d/override.conf <<EOF
[Service]
Environment=MEDIA_ARCHIVIST_DB_FILE=/srv/media-archivist/index.json
Environment=MEDIA_ARCHIVIST_PORT=18000
EOF
systemctl --user daemon-reload
systemctl --user restart media-archivist@$USER.service
```

## HTTP surface

| Method | Path                 | Purpose |
| ------ | -------------------- | ------- |
| `GET`  | `/entries`           | Query the canonical view (filters: `source`, `where`, `grep`, `has_stream`, `explicit`, `limit`). |
| `GET`  | `/entries/{id}`      | Fetch a single `MediaEntry` by id. |
| `POST` | `/archive`           | Enqueue an archive task; returns a `Task`. |
| `GET`  | `/tasks/{id}`        | Task progress (`queued`, `running`, `ok`, `error`). |
| `GET`  | `/feed.rss`          | RSS feed of recently-added entries. |
| `GET`  | `/m3u`               | M3U playlist of stream URLs. |
| `GET`  | `/stats`             | Source mix, canonical / quarantined counts. |
| `GET`  | `/docs`              | Auto-generated OpenAPI / Swagger UI. |

The schedulable surface is single-tenant by design — submitted tasks
queue and run sequentially. Task state persists in
`<db>.tasks.json`; if the service is restarted, anything still pending
is re-queued automatically.
