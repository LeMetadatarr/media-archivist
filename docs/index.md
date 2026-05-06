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

- [Getting Started](./getting-started.md) — install, first run, 10-minute walkthrough.
- [Tutorial: Cross-Source Music Dataset](./tutorial.md) — end-to-end workflow: index from YouTube Music / Bandcamp / SoundCloud, deduplicate, export to JSONL.
- [Recipes](./recipes/index.md) — practical end-to-end workflows: music libraries, documentaries, podcasts, ML datasets, Arr integration, cross-source deduplication, automation via cron.
- [FAQ](./faq.md) — 20+ common questions: installation, backend selection, ToS, performance, filtering, canonicalization, troubleshooting.
- [Schema & validation](./schema.md) — pydantic models for every backend
  and the canonical envelope.
- [Storage format](./storage.md) — on-disk JSON layout.
- [Canonical view & dedupe](./canonical.md) — `MediaEntry`, `Index` SDK,
  `--where` filter language, fingerprint, link, dedupe.
- [Disambiguation & external IDs](./disambiguation.md) — canonical_id,
  signal comparison, quarantine workflow, MusicBrainz / TMDB / Wikidata
  / Arr-stack providers.
- [Entities & relations](./entities.md) — first-class artists, actors,
  directors, albums, labels; `<db>.entities.json` sidecar; `--where`
  dotted access (`relations.director`, `external_ids.imdb`).
- [Release variants](./variants.md) — `VariantKind`, edition/region/source_format signals, `EntityKind.RELEASE`, `include_variants` flag, `list_variants()` extension point.
- [Anime, manga & books providers](./providers_anime_books.md) — `anilist`, `jikan_anime`, `jikan_manga`, `google_books`; zero-setup, no keys; `MediaType.EPISODIC_SERIES + content_genres=["anime"]`, `MediaType.COMIC + content_genres=["manga"]`; `EntityKind.STUDIO`.
- [metadatarr resolver integration](./metadatarr.md) — all ~24 built-in
  providers (MusicBrainz, Wikidata, TMDB, AniList, Jikan, Google Books,
  LibriVox, Apple Podcasts, *arr family, Discogs, Blu-ray.com,
  DVDCompare, OpenLibrary, Anna's Archive, Bandcamp, SoundCloud,
  YouTube/YT Music, Metal Archives, AudioDB, TVMaze, …) live in
  metadatarr; `media_archivist.providers` is a thin re-export.
- [Datasets, enrichment & sharing](./datasets.md) — `enrich`, `export
  --split`, `snapshot` / `diff`, HuggingFace `hub-publish`.
- [Running as a service](./deploy.md) — Docker + systemd templates,
  HTTP surface reference.
- [Jellyfin / Kodi remote media](./jellyfin.md) — `.strm` export
  pointing at the server's `/strm/<id>` endpoint.
- [CLI architecture](./cli.md) — subcommand reference, validation rules.
- [CI / release automation](./ci.md) — workflows and branching model.
- [Roadmap](./roadmap.md) — phased plan to v1.0 (mirrors
  `~/.claude/plans/plan-a-full-roadmap-gleaming-ripple.md`).
- **[Reference Documentation](./reference/index.md)** — exhaustive technical reference: CLI, SDK, data models, WHERE language, providers, file formats.

## Design principles

1. **Metadata-first.** Index streams; never download. Pair with `yt-dlp`.
2. **Pydantic at every stage.** Every value entering or leaving the index
   is validated against a typed model — no untyped dicts in the write
   path, no defensive reads on the consumer side.
3. **Two-tier schema.** Raw per-source rows stay diff-stable and
   source-faithful on disk; a canonical view is computed on read.
4. **Self-contained.** No required integration with any other ecosystem.
