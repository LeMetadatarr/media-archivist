# media_archivist — developer docs

Cross-source media indexer (YouTube, YouTube Music, Internet Archive,
Bandcamp, SoundCloud) backed by a single validated JSON database.

This site documents the **internals**: schema, validation layers, storage
format, CLI architecture. For end-user usage see
[`README.md`](../README.md).

## Layout

```
media_archivist/
├── base.py                 JsonArchivist — shared filter/persist logic
├── youtube.py              YoutubeArchivist + YoutubeMonitor
├── music.py                YoutubeMusicArchivist
├── bandcamp.py             BandcampArchivist
├── soundcloud.py           SoundCloudArchivist
├── ia.py                   IAArchivist
├── cli.py                  argparse entry point (media-archivist CLI)
├── storage.py              EnvelopeJsonStorage[XDG] — validated on-disk format
├── models/
│   ├── raw.py              one Raw*Entry pydantic model per backend
│   └── archive.py          MediaArchive envelope + ArchiveMeta
├── exceptions.py
└── version.py
```

## Documentation index

- [Schema & validation](./schema.md) — pydantic models for every backend
  and the canonical envelope.
- [Storage format](./storage.md) — on-disk JSON layout.
- [Canonical view & dedupe](./canonical.md) — `MediaEntry`, `Index` SDK,
  `--where` filter language, fingerprint, link, dedupe.
- [CLI architecture](./cli.md) — subcommand reference, validation rules.
- [CI / release automation](./ci.md) — workflows and branching model.
- [Roadmap](./roadmap.md) — phased plan to v1.0 (mirrors
  `~/.claude/plans/plan-a-full-roadmap-gleaming-ripple.md`).

## Design principles

1. **Metadata-first.** Index streams; never download. Pair with `yt-dlp`.
2. **Pydantic at every stage.** Every value entering or leaving the index
   is validated against a typed model — no untyped dicts in the write
   path, no defensive reads on the consumer side.
3. **Two-tier schema.** Raw per-source rows stay diff-stable and
   source-faithful on disk; a canonical view is computed on read (v0.3+).
4. **Self-contained.** No required integration with any other ecosystem.
