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
- ⬜ **v0.6** — Datasets & sharing (HF Hub, transcripts, lyrics).
- ⬜ **v0.4** — Discovery & RSS-incremental sync.
- ⬜ **v0.5** — Service mode (FastAPI, M3U/RSS endpoints, async scheduler).
- ⬜ **v1.0** — Schema semver, docs site, ≥80% coverage.

See the plan file for the full design rationale per phase.
