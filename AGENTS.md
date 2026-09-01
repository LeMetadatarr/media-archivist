# media-archivist — agent guide

Cross-source media indexer: scrapes stream metadata from YouTube, YouTube
Music, Internet Archive, Bandcamp, and SoundCloud, validates it through
Pydantic raw models, projects to a canonical `MediaEntry` view,
canonicalizes/dedupes across sources via `metadatarr`, and serves the JSON DB
over a FastAPI HTTP API. Metadata-only — it never downloads media; pair with
`yt-dlp`.

## Setup

```bash
pip install -e .              # core
pip install -e .[server]      # FastAPI HTTP service
pip install -e .[hub]         # HuggingFace Hub publish
pip install -e .[all,test]    # everything + pytest
```

Bandcamp and SoundCloud backends need their client libraries installed
separately (`py_bandcamp`, `nuvem_de_som`); the CLI prints the exact package
name when a backend's client is missing.

Package name is `media_archivist`; CLI entry point is `media-archivist`
(`media_archivist.cli:main`).

## Test

```bash
pytest test/
```

Tests are network-free: they use captured JSON fixtures under `test/fixtures/`
and mock backend clients. The `examples/live/` scripts hit the network and are
NOT part of the test suite — never run them in CI.

## Lint

```bash
ruff check media_archivist test
```

## Layout

Module inventory below is generated from `git ls-files 'media_archivist/*.py'
'media_archivist/**/*.py'`; every path listed is verifiable with that command.

<!-- module-inventory -->
- `media_archivist/__init__.py` — package init; optional-backend imports are
  guarded, set to `None` if their client dependency is missing.
- `media_archivist/base.py` — `JsonArchivist` base class (filtering, XDG vs
  explicit DB path, duration handling).
- `media_archivist/youtube.py` — YouTube backend (`YoutubeArchivist`,
  `YoutubeMonitor`), via `tutubo`.
- `media_archivist/music.py` — YouTube Music backend, via `tutubo.ytmus`.
- `media_archivist/ia.py` — Internet Archive backend, via `internetarchive`.
- `media_archivist/bandcamp.py` — Bandcamp backend, via `py_bandcamp`.
- `media_archivist/soundcloud.py` — SoundCloud backend, via `nuvem_de_som`.
- `media_archivist/collections.py` — saved collections (smart playlists): a
  named, re-runnable filter.
- `media_archivist/dedupe.py` — cross-source canonicalization: fingerprint
  (normalized artist|title, sha1), `link`/`dedupe` into sidecars.
- `media_archivist/canon.py` — deprecated alias re-exporting `dedupe.py`;
  removed in a future release.
- `media_archivist/canonicalize.py` — metadatarr resolver fan-out, quarantine
  on conflict, `.canonical.json`/`.quarantine.json`/`.entities.json` sidecars.
- `media_archivist/entities.py` — entity sidecar I/O + merge.
- `media_archivist/health.py` — flag dead/expired `.strm` entries and
  re-resolve them.
- `media_archivist/hub.py` — HuggingFace Hub publish.
- `media_archivist/index.py` — read-side SDK (`Index`) with a sandboxed
  `--where` AST expression evaluator over `MediaEntry` fields.
- `media_archivist/discover.py` — content discovery via tutubo's
  content-type-aware search factories.
- `media_archivist/nfo.py` — Kodi/Jellyfin `.nfo` sidecar generation.
- `media_archivist/notify.py` — outbound webhook notifications (Discord /
  ntfy / generic JSON).
- `media_archivist/progress.py` — progress-bar utility shared by every
  backend.
- `media_archivist/exceptions.py` — shared exception types.
- `media_archivist/snapshot.py` — dated snapshots of a DB and a structural
  diff between two snapshots.
- `media_archivist/storage.py` — envelope-aware JSON storage.
- `media_archivist/streams.py` — stream resolution core, source-aware,
  yt-dlp by default.
- `media_archivist/strm.py` — Jellyfin/Kodi `.strm` export.
- `media_archivist/subscriptions.py` — channel/playlist subscriptions:
  auto-index new uploads on sync.
- `media_archivist/subtitles.py` — subtitle/caption fetching as
  Jellyfin/Kodi-ready sidecar files.
- `media_archivist/sync.py` — incremental refresh helpers.
- `media_archivist/version.py` — package version (auto-bumped; never
  hand-edited).
- `media_archivist/views.py` — per-backend adapters from raw rows to
  `MediaEntry`.
- `media_archivist/cli.py` — argparse CLI wiring; parser built by
  `build_parser()`.
- `media_archivist/cli_args.py` — validated CLI argument models.
- `media_archivist/models/__init__.py` — models package init.
- `media_archivist/models/raw.py` — per-backend on-disk row models
  (discriminated union, `extra="allow"`) + `parse_raw`/`Source`.
- `media_archivist/models/canonical.py` — `MediaEntry` read-time view
  (`extra="forbid"`, computed on read, never persisted).
- `media_archivist/models/canonical_record.py` — sidecar storage models for
  the canonical/quarantine maps.
- `media_archivist/models/archive.py` — `MediaArchive` envelope
  (`{"_meta": ..., "entries": ...}`, the on-disk file shape).
- `media_archivist/models/enriched.py` — pydantic shape for the
  `_meta.enriched` block on raw rows.
- `media_archivist/models/api.py` — HTTP API request/response models.
- `media_archivist/models/dataset_card.py` — HuggingFace dataset-card model.
- `media_archivist/providers/__init__.py` — pure re-export of metadatarr
  resolver providers; no media-archivist-specific providers live here.
- `media_archivist/enrich/__init__.py` — enrich package init.
- `media_archivist/enrich/orchestrator.py` — enricher fan-out orchestration.
- `media_archivist/enrich/lyrics.py` — lyrics enricher (optional deps).
- `media_archivist/enrich/transcripts.py` — transcript enricher (optional
  deps).
- `media_archivist/enrich/content_type.py` — content-type enricher (optional
  deps).
- `media_archivist/server/__init__.py` — server package init.
- `media_archivist/server/app.py` — FastAPI app factory + uvicorn entry
  point.
- `media_archivist/server/routes.py` — routes: `/strm`, `/m3u`, `/feed.rss`,
  `/healthz`, `/providers`, `/canonicalize`, `/quarantine`.
- `media_archivist/server/scheduler.py` — background scheduler for the
  server.
- `media_archivist/server/web.py` — server-rendered htmx WebUI, mounted onto
  the same FastAPI app as the HTTP API.
- `media_archivist/commands/__init__.py` — command handler modules for the
  media-archivist CLI.
- `media_archivist/commands/canonical.py` — canonicalization / dedupe CLI
  command handlers.
- `media_archivist/commands/collections.py` — CLI handlers for saved
  collections (smart playlists).
- `media_archivist/commands/entities.py` — entity sidecar CLI command
  handlers.
- `media_archivist/commands/entries.py` — entry-oriented CLI command
  handlers (add, list, export, import, ...).
- `media_archivist/commands/health.py` — CLI handler for
  `media-archivist health`.
- `media_archivist/commands/quarantine.py` — quarantine sidecar CLI command
  handlers.
- `media_archivist/commands/remote.py` — remote-facing CLI command handlers
  (serve, discover, sync, enrich, ...).
- `media_archivist/commands/streams.py` — CLI handlers for `resolve` /
  `download`, thin wrappers over `streams.py`.
- `media_archivist/commands/subscriptions.py` — CLI handlers for
  channel/playlist subscriptions.
- `media_archivist/commands/subtitles.py` — CLI handler for `subtitles`
  (fetch .srt/.vtt sidecar files).
<!-- /module-inventory -->

`deploy/` — Dockerfile, docker-compose, systemd unit. `docs/` is a MkDocs
site. `examples/` includes a `learn/` tutorial track and a `live/` network
smoke-test track (never runs in CI).

## CLI subcommands

Generated from `grep -n "add_parser(" media_archivist/cli.py`; every name
below is a registered subparser choice in `build_parser()`.

<!-- cli-subcommand-inventory -->
- `add`
- `urls`
- `list`
- `dump`
- `export`
- `import`
- `merge`
- `stats`
- `prune`
- `bootstrap`
- `strm-export`
- `subtitles`
- `serve`
- `discover`
- `sync`
- `enrich`
- `snapshot`
- `diff`
- `hub-publish`
- `providers`
- `canonicalize`
- `quarantine-list`
- `quarantine-resolve`
- `quarantine-reject`
- `entities-list`
- `entities-show`
- `entities-stats`
- `link`
- `dedupe`
- `resolve`
- `download`
- `health`
- `monitor`
- `subscribe`
- `unsubscribe`
- `subscriptions`
- `sync-subscriptions`
- `collection-add`
- `collection-remove`
- `collection-export`
- `collections`
- `notify-test`
<!-- /cli-subcommand-inventory -->

## Conventions

- Branches: `dev` (work) / `master` (stable). NEVER `main`.
- Never edit `media_archivist/version.py` — gh-automations bumps semver from
  conventional-commit prefixes (`feat:`/`fix:`/`feat!:`).
- New repos private by default; do not make public without asking.
- Commit identity: JarbasAi <jarbasai@mailfence.com>.
- Reference `OpenVoiceOS/gh-automations` reusable workflows at `@dev`.
- No Neon / `neon-*` references.
- No meta-commentary in docs/commits/PRs/code (no history, dates, "before
  times").
- CI is provided by `OpenVoiceOS/gh-automations`.

## Gotchas

- The on-disk JSON is NOT `MediaEntry` — it is per-backend raw rows;
  `MediaEntry` is a read-time view computed by `views.to_media_entry`. Write
  against raw models, read against the canonical view.
- `--where` is a restricted AST evaluator (allowed funcs `len`/`lower`/`upper`,
  comparisons, boolean/arith ops only); unknown names raise `WhereError`. Do
  not loosen it into a general `eval`.
- All resolver providers live in `metadatarr`, NOT here — there are no
  media-archivist-specific resolver providers. The resolver gates on three
  axes: `media`, `modality`, `genre_filter`.
- The FastAPI service is single-tenant with no authentication; it is meant for
  LAN / behind a reverse proxy only.
- Two overlapping CI files exist for license check (`license-check.yml` and
  `license_check.yml`); both call the gh-automations reusable workflow.

## Testing

45 modules in `test/`, offline (see `test/fixtures/` for captured JSON
payloads and mocked backend clients).
