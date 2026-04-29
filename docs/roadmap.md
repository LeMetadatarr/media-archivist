# Roadmap

Live mirror of the approved roadmap at
`~/.claude/plans/plan-a-full-roadmap-gleaming-ripple.md`. Updated when
milestones land.

## Status

- ✅ **v0.1** — Cross-source backends (YouTube, YT Music, IA, Bandcamp,
  SoundCloud), CLI, dataset export.
- 🚧 **v0.2 — Foundation.** *In progress.*
  - ✅ Pydantic raw models for every backend (`models/raw.py`).
  - ✅ Validated write paths in `youtube`, `music`, `bandcamp`,
    `soundcloud`, `ia`.
  - ✅ `MediaArchive` envelope + `EnvelopeJsonStorage[XDG]` —
    legacy-compatible, validated on read, written back as v2 on store.
  - ✅ IA bare-except replaced with metadata-API dispatch.
  - ✅ Tests: `test_models`, `test_url_parsing`, `test_storage`.
  - ✅ `CliArgs` pydantic validators per subcommand (`cli_args.py`).
  - ✅ Dropped the `YoutubeArchivistError` alias and v0.1 legacy DB
    inference (no backwards compat — pre-first-release).
  - ✅ `tqdm` progress bars on every backend's long iterators
    (`progress.py`).
  - ✅ CI workflow rewrite — replaced broken `setup.py bdist_wheel` /
    Py3.8 with the OpenVoiceOS reusable workflows (`build-tests`, `lint`,
    `coverage`, `license-check`, `release[-preview]`).
  - ✅ GitHub remote pointed at `TigreGotico/media-archivist` (not pushed
    yet — first push deferred until the v1.0 milestone).
  - ⬜ Repo directory rename (cosmetic; deferred).
- ✅ **v0.3 — Two-tier schema, canonical view, fingerprint dedup.**
  - `MediaEntry` pydantic model + per-backend view adapters
    (`models/canonical.py`, `views.py`).
  - `Index` SDK with sandboxed `--where` expression evaluator
    (`index.py`).
  - Cross-source fingerprint, duration-aware clustering, sidecar links,
    canonical JSONL dedupe (`canon.py`).
  - CLI: `link`, `dedupe` subcommands; `--canonical`, `--where`,
    `--source`, `--has-stream`, `--explicit/--no-explicit` flags on
    `list` / `urls` / `export`.
- 🚧 **v0.3.5 — Disambiguation, canonical IDs & external providers.** *In progress.*
  - ✅ `Signals`, `ExternalIds`, `CanonicalRecord`, `QuarantineEntry`
    pydantic models with strict-on-read configuration
    (`models/{signals,external_ids,canonical_record}.py`).
  - ✅ Built-in providers, env-var driven activation
    (`providers/{musicbrainz,wikidata,tmdb,arr}.py`); `arr` covers
    Sonarr / Radarr / Readarr / Lidarr.
  - ✅ `canonicalize.py` orchestrator: signal extraction, provider
    lookups, conservative quarantine on any disagreement, sidecar I/O,
    `_meta.canonical_id` row stamps.
  - ✅ `MediaEntry` + `Index.view()` surface `canonical_id`,
    `canonical_status`, `external_ids` from sidecar joins.
  - ✅ CLI: `providers`, `canonicalize`, `quarantine-list`,
    `quarantine-resolve`, `quarantine-reject`.
  - ✅ Tests: `test_disambiguation.py` (10 cases, fully offline via a
    stub provider).
  - ✅ Docs: `disambiguation.md`. Example:
    `examples/canonicalize_movies.py`.
  - ⬜ Dedicated `tvdb`, `openlibrary`, `imdb` providers (currently
    via Wikidata / TMDB joins).
  - ⬜ HTTP fixture-based integration tests for the live providers
    (vcr.py / cassette pattern).
- ✅ **v0.6 — Datasets & sharing.**
  - `enrich/` package: lyrics (Bandcamp), transcripts (yt-dlp + VTT
    parser), content_type (tutubo classifier); `EnrichedBlock`
    pydantic model under `_meta.enriched`.
  - `hub.py`: HuggingFace Hub publisher with auto-generated
    `DatasetCard` from envelope `source_mix` + canonical sidecar.
  - Deterministic train/val/test splits keyed on `canonical_id` →
    `--split` flag on `export`. Per-field bucketing via `--split-by`.
  - `snapshot.py`: dated DB copies + structural `diff` ignoring
    volatile `_meta` fields.
  - CLI: `enrich`, `snapshot`, `diff`, `hub-publish` subcommands.
  - 8 new offline tests; 60 total green.
  - Docs: `docs/datasets.md`. Recipes folder shipped by the docs
    agents in parallel.
- ⬜ **v0.4** — Discovery & RSS-incremental sync.
- ⬜ **v0.5** — Service mode (FastAPI, M3U/RSS endpoints, async scheduler).
- ⬜ **v1.0** — Schema semver, docs site, ≥80% coverage.

See the plan file for the full design rationale per phase.
