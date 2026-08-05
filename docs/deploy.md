# Running as a service

`media-archivist serve` boots the FastAPI HTTP surface
([reference](./reference/cli.md#serve)). For long-lived deployments the
repository ships two ready-to-use templates under `deploy/`.

The same process also serves the build-free Web UI at `/`, alongside the
JSON API and the Swagger docs at `/docs`. See [`webui.md`](./webui.md) for a
page-by-page tour.

> **Security note:** The service has no built-in authentication. It is designed
> for single-tenant, LAN use. Do not expose port 8000 directly to the internet
>, put it behind your existing reverse proxy (Caddy, Traefik, nginx) and
> restrict access accordingly.

## Docker Compose (recommended)

The fastest path from zero to running:

```bash
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f
```

The compose file:
- pins a named `media-archivist-data` volume so the index survives container
  rebuilds
- sets `restart: unless-stopped` for automatic recovery after a reboot
- wires a `/healthz` healthcheck so Docker (and tools like Uptime Kuma) report
  container health correctly

## Docker

```bash
docker build -t media-archivist -f deploy/Dockerfile .
docker run -d --name media-archivist \
    -p 8000:8000 \
    -v "$HOME/media-archivist-data:/data" \
    media-archivist
```

The image carries a `HEALTHCHECK` that polls `/healthz` every 30 s.

The container runs `media-archivist serve --host 0.0.0.0` against
`/data/index.json`. The `/data` mount holds the index plus all sidecars
(`<db>.canonical.json`, `<db>.quarantine.json`, `<db>.tasks.json`,
`<db>.links.json`). `yt-dlp` is preinstalled so transcript enrichment and
stream resolution work out of the box.

## Systemd (per-user)

```bash
sudo cp deploy/media-archivist.service /etc/systemd/system/
sudo systemctl daemon-reload
systemctl --user enable --now media-archivist@$USER.service
journalctl --user -fu media-archivist@$USER.service
```

The unit reads its DB path / host / port from environment variables.override them via:

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

## Homelab integration points

Once the service is up, three endpoints connect it to the rest of your stack:

| Endpoint | Client |
| -------- | ------ |
| `GET /strm/{id}` | Jellyfin / Kodi `.strm` files. Plain `text/plain` URL by default; `?resolve=1` turns it into a play-time yt-dlp hook that 302-redirects to a freshly resolved stream (`?mode=proxy` to stream bytes through instead). See [Jellyfin docs](./jellyfin.md). |
| `GET /m3u` | Any M3U-capable client (VLC, Kodi, IPTV apps). Accepts `source`, `where`, `has_stream`, `limit` query params. |
| `GET /feed.rss` | Podcast clients, Jellyfin RSS plugin, Freshrss. Accepts `limit`. |
| `GET /healthz` | Uptime Kuma, Docker healthcheck, Compose healthcheck, k8s liveness probe. Returns `{status: "ok", version, db_path}`. |

## HTTP surface

| Method | Path                 | Purpose |
| ------ | -------------------- | ------- |
| `GET`  | `/entries`           | Query the canonical view (filters: `source`, `where`, `grep`, `has_stream`, `explicit`, `limit`). |
| `GET`  | `/entries/{id}`      | Fetch a single `MediaEntry` by id. |
| `POST` | `/archive`           | Enqueue an archive task. Returns a `Task`. |
| `POST` | `/entries/{id}/download` | Enqueue an optional download of one entry via `yt-dlp` to `MEDIA_ARCHIVIST_DOWNLOAD_DIR`. Returns a `Task`. `503` if `yt-dlp` isn't available. |
| `GET`  | `/tasks/{id}`        | Task progress (`queued`, `running`, `ok`, `error`). |
| `GET`  | `/feed.rss`          | RSS feed of recently-added entries. |
| `GET`  | `/m3u`               | M3U playlist of stream URLs. |
| `GET`  | `/stats`             | Source mix, canonical / quarantined counts. |
| `GET`  | `/healthz`           | Liveness check, `{status: "ok", version, db_path}`. |
| `GET`  | `/providers`         | Registry introspection, every provider with `available`, `media`, `modality`, `genre_filter`. |
| `POST` | `/canonicalize`      | Run providers across the DB. Body: `{providers?: [str], stamp_rows?: bool, max_workers?: int}`. Returns counts. |
| `GET`  | `/quarantine`        | List quarantined rows + their conflicts. |
| `POST` | `/quarantine/{id}/accept` | Accept a quarantined row (optional `?canonical_id=` to link). |
| `POST` | `/quarantine/{id}/reject` | Reject and force a fresh canonical_id. |
| `GET`  | `/docs`              | Auto-generated OpenAPI / Swagger UI. |

The schedulable surface is single-tenant by design, submitted tasks
queue and run sequentially. Task state persists in
`<db>.tasks.json`. If the service is restarted, anything still pending
is re-queued automatically.

---
[← Datasets, Enrichment & Sharing](datasets.md) · [Home](index.md) · [Jellyfin / Kodi Remote Media →](jellyfin.md)
