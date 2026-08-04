# Web UI

`media-archivist serve` ships a build-free [htmx](https://htmx.org) Web UI
alongside the JSON API, no Node, no JS build step. It's meant for homelab
use: point it at a DB file, open a browser, manage your index.

## Launch it

```bash
pip install "media-archivist[server]"
media-archivist serve --db-file talks.json
# open http://localhost:8000/
```

Or with Docker (see [`deploy.md`](./deploy.md) for the full compose setup):

```bash
docker compose -f deploy/docker-compose.yml up
```

## Dashboard

Landing page: entry counts, quarantine count, provider coverage
(e.g. `20/27 providers`), a source-mix bar chart, quick links to the M3U
playlist, RSS feed and `.strm` export, and a one-click canonicalize button
to re-run the resolver against the DB.

![Dashboard](img/dashboard.png)

## Library

Browse every entry in the DB. The filter form supports source, free-text
grep, and the `where` DSL (the same query language the CLI and API use),
plus a result limit. Each row shows a yes/no stream badge so you can spot
entries missing a resolvable stream at a glance.

![Library](img/library.png)

Click into a row for the entry detail drawer: thumbnail, full metadata,
external IDs, and a link to resolve the entry via `/strm/{id}`.

![Entry detail](img/entry-detail.png)

## Archive

Kick off a new archive job from the browser: paste a URL, pick a backend
(YouTube, YT Music, Bandcamp, SoundCloud, Internet Archive), and set
`--require` / `--blacklist` / `--min-duration` filters. Progress polls live,
no page refresh needed.

![Archive](img/archive.png)

## Quarantine

When the resolver can't confidently match an entry against the canonical
index, it lands here instead of silently merging. Each conflict is shown
with the competing candidates side by side, readable diffs, and
Accept/Reject buttons, no need to hand-edit the quarantine JSON sidecar.

![Quarantine](img/quarantine.png)

## Providers

A grid of every metadatarr provider media-archivist knows about, and
whether it's currently available (installed + reachable).

![Providers](img/providers.png)

## Responsive, themeable

The UI works down to phone width and follows your OS light/dark preference.
Useful for checking on an archive job from your homelab network without a
laptop.

![Mobile](img/mobile.png)

## Security

Like the rest of the HTTP service, the Web UI has **no built-in
authentication**. It's designed for single-tenant, LAN-only use. If you need
to reach it beyond your local network, put it behind a reverse proxy
(Caddy, Traefik, nginx) — see [`deploy.md`](./deploy.md) for examples and
the full route table.

## Troubleshooting

- **Port already in use** — `media-archivist serve` binds `:8000` by
  default. Pass `--port` to pick another one, or find and stop whatever's
  already bound: `lsof -i :8000`.
- **"where: ..." error on the Library page** — the `where` DSL rejected
  your filter (unsupported syntax, unknown field, or disallowed operator).
  It's surfaced inline as an HTTP 400 on the filter fragment, not a page
  crash; fix the expression and resubmit.
- **A provider shows "unavailable" on the Providers page** — most
  metadatarr providers need an API key or local credentials file. Check the
  provider's own docs for the expected environment variable; an unavailable
  provider is skipped during canonicalization, it doesn't block indexing.
- **Running behind a reverse proxy** — the UI honours `root_path` for
  every link and htmx `hx-get`/`hx-post` target, so it works fine mounted
  under a sub-path. See [`deploy.md`](./deploy.md) for a Caddy/Traefik/nginx
  example; forward `X-Forwarded-*` headers as usual.
