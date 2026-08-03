# media-archivist — agent guide

Cross-source media indexer: scrapes stream metadata from YouTube, YouTube Music, Internet Archive, Bandcamp, SoundCloud (plus adult-tube and radio backends), validates it through Pydantic raw models, projects to a canonical `MediaEntry` view, canonicalizes/dedupes across sources via `metadatarr`, and serves the JSON DB over a FastAPI HTTP API. Metadata-only — it never downloads media; pair with `yt-dlp`.

## Setup

```bash
pip install -e .                       # core (YouTube + IA + YT Music)
pip install -e .[bandcamp,soundcloud]  # add Bandcamp / SoundCloud backends
pip install -e .[server]               # FastAPI HTTP service
pip install -e .[all,test]             # everything + pytest
```

Package name is `media_archivist`; CLI entry point is `media-archivist` (`media_archivist.cli:main`).

## Test

```bash
pytest test/
```

Tests are network-free: they use captured JSON fixtures under `test/fixtures/` and mock backend clients. The `examples/live/` scripts hit the network and are NOT part of the test suite — never run them in CI.

## Lint

```bash
ruff check media_archivist test
```

## Layout

- `media_archivist/base.py` — `JsonArchivist` base class (filtering, XDG vs explicit DB path, duration handling).
- Backend archivists (one module each): `youtube.py` (`YoutubeArchivist`, `YoutubeMonitor`), `music.py` (YT Music), `ia.py` (Internet Archive), `bandcamp.py`, `soundcloud.py`, `heartradio.py`, and the adult-tube backends (`pornhub.py`, `hanime.py`, `xnxx.py`, `xhamster.py`, `xvideos.py`, `redtube.py`, `youporn.py`, `spankbang.py`, `eporner.py`, `youjizz.py`, `alphaporno.py`, `hellporno.py`, `pornoxo.py`, `sunporno.py`, `fullporner.py`, `hentaisea.py`). All optional backends are imported defensively in `__init__.py` (set to `None` if their client is missing).
- `models/` — Pydantic layers: `raw.py` (per-backend on-disk row models + `parse_raw`/`Source`), `canonical.py` (`MediaEntry` read-time view), `canonical_record.py`, `enriched.py`, `archive.py` (`MediaArchive` envelope), `api.py`, `dataset_card.py`. Entity types are re-exported from `metadatarr.resolve.entities`; `VariantKind` from `mediavocab`.
- `index.py` — read-side SDK (`Index`) with a sandboxed `--where` AST expression evaluator over `MediaEntry` fields.
- `views.py` — `to_media_entry` adapters projecting each raw row to the canonical view.
- `canon.py` / `canonicalize.py` — fingerprinting (normalized artist|title, sha1), cross-source `link`/`dedupe` into sidecars, and the metadatarr resolver pass.
- `entities.py` — entity sidecar I/O + merge.
- `enrich/` — orchestrator + enrichers (`lyrics`, `transcripts`, `content_type`), each gated on optional deps.
- `discover.py`, `sync.py`, `refresh_streams.py`, `snapshot.py`, `strm.py`, `hub.py`, `doctor.py` — discovery, incremental RSS sync, stream-URL refresh, dated snapshots/diff, Jellyfin/Kodi `.strm` export, HuggingFace Hub publish, sidecar audit.
- `server/` — FastAPI app (`app.py`), routes (`routes.py`: `/strm`, `/m3u`, `/feed.rss`, `/healthz`, `/providers`, `/canonicalize`, `/quarantine`), scheduler.
- `cli.py` / `cli_args.py` — ~30 subcommands (add, list, export, import, merge, stats, prune, urls, monitor, discover, sync, enrich, canonicalize, link, dedupe, entities, quarantine, snapshot, diff, strm-export, serve, doctor, refresh-streams, hub-publish, providers, bootstrap, dump).
- `deploy/` — Dockerfile, docker-compose, systemd unit. `docs/` is a MkDocs site. `examples/` includes a `learn/` tutorial track and a `live/` network smoke-test track.

## Conventions

- Branches: `dev` (work) / `master` (stable). NEVER `main`.
- Never edit `media_archivist/version.py` — gh-automations bumps semver from conventional-commit prefixes (`feat:`/`fix:`/`feat!:`).
- New repos private by default; do not make public without asking.
- Commit identity: JarbasAi <jarbasai@mailfence.com>.
- Reference `OpenVoiceOS/gh-automations` reusable workflows at `@dev`.
- No Neon / `neon-*` references.
- No meta-commentary in docs/commits/PRs/code (no history, dates, "before times").
- CI is provided by `OpenVoiceOS/gh-automations`.

## Gotchas

- The on-disk JSON is NOT `MediaEntry` — it is per-backend raw rows; `MediaEntry` is a read-time view computed by `views.to_media_entry`. Write against raw models, read against the canonical view.
- `--min-duration` is a no-op for plain YouTube channel/playlist scrapes (tutubo's channel grid lacks track length); it applies only to backends where duration is reliable (YT Music, Bandcamp, SoundCloud, IA, search previews). See `JsonArchivist._DURATION_RELIABLE`.
- `--where` is a restricted AST evaluator (allowed funcs `len`/`lower`/`upper`, comparisons, boolean/arith ops only); unknown names raise `WhereError`. Do not loosen it into a general `eval`.
- All resolver providers live in `metadatarr`, NOT here — there are no media-archivist-specific resolver providers. The resolver gates on three axes: `media`, `modality`, `genre_filter`.
- The FastAPI service is single-tenant with no authentication; it is meant for LAN / behind a reverse proxy only.
- Two overlapping CI files exist for license check (`license-check.yml` and `license_check.yml`); both call the gh-automations reusable workflow.
