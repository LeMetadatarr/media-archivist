# Changelog

## [0.20.2a1](https://github.com/LeMetadatarr/media-archivist/tree/0.20.2a1) (2026-09-02)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.20.1a1...0.20.2a1)

**Merged pull requests:**

- fix: replace eval-based BinOp dispatch with operator functions [\#17](https://github.com/LeMetadatarr/media-archivist/pull/17) ([JarbasAl](https://github.com/JarbasAl))

## [0.20.1a1](https://github.com/LeMetadatarr/media-archivist/tree/0.20.1a1) (2026-09-01)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.20.0a1...0.20.1a1)

**Merged pull requests:**

- fix: make database and sidecar writes atomic [\#20](https://github.com/LeMetadatarr/media-archivist/pull/20) ([JarbasAl](https://github.com/JarbasAl))
- fix: declare httpx in the test extra [\#19](https://github.com/LeMetadatarr/media-archivist/pull/19) ([JarbasAl](https://github.com/JarbasAl))
- docs: accurate module and CLI inventory in AGENTS.md, guarded by tests [\#18](https://github.com/LeMetadatarr/media-archivist/pull/18) ([JarbasAl](https://github.com/JarbasAl))

## [0.20.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.20.0a1) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.19.0a1...0.20.0a1)

**Merged pull requests:**

- feat: webhook notifications \(Discord/ntfy/generic\) on archive/download/subscription events [\#56](https://github.com/LeMetadatarr/media-archivist/pull/56) ([JarbasAl](https://github.com/JarbasAl))

## [0.19.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.19.0a1) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.18.0a1...0.19.0a1)

**Merged pull requests:**

- feat: subtitle fetch \(.srt/.vtt sidecars for archived streams, via yt-dlp\) [\#55](https://github.com/LeMetadatarr/media-archivist/pull/55) ([JarbasAl](https://github.com/JarbasAl))

## [0.18.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.18.0a1) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.17.0a1...0.18.0a1)

**Merged pull requests:**

- feat: periodic subscription auto-sync \(--interval\) + optional auto-download of new items [\#54](https://github.com/LeMetadatarr/media-archivist/pull/54) ([JarbasAl](https://github.com/JarbasAl))

## [0.17.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.17.0a1) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.16.0a1...0.17.0a1)

**Merged pull requests:**

- feat: saved collections \(smart playlists\) — per-collection M3U URL + .strm export for Jellyfin/Kodi [\#53](https://github.com/LeMetadatarr/media-archivist/pull/53) ([JarbasAl](https://github.com/JarbasAl))

## [0.16.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.16.0a1) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.15.0a1...0.16.0a1)

**Merged pull requests:**

- feat: channel/playlist subscriptions — auto-index new uploads \(CLI + /ui/subscriptions\) [\#52](https://github.com/LeMetadatarr/media-archivist/pull/52) ([JarbasAl](https://github.com/JarbasAl))

## [0.15.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.15.0a1) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.14.0a3...0.15.0a1)

**Merged pull requests:**

- feat: stream-health check + re-resolve \(flag dead/expired .strm, refresh via yt-dlp\) [\#51](https://github.com/LeMetadatarr/media-archivist/pull/51) ([JarbasAl](https://github.com/JarbasAl))

## [0.14.0a3](https://github.com/LeMetadatarr/media-archivist/tree/0.14.0a3) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.14.0a2...0.14.0a3)

**Merged pull requests:**

- docs: refresh README + docs around the streams job \(player, pagination, bulk quarantine, Jellyfin play-time hook\) [\#50](https://github.com/LeMetadatarr/media-archivist/pull/50) ([JarbasAl](https://github.com/JarbasAl))

## [0.14.0a2](https://github.com/LeMetadatarr/media-archivist/tree/0.14.0a2) (2026-08-05)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.14.0a1...0.14.0a2)

**Merged pull requests:**

- chore: remove library tagger \(relocated to metadatarr\) — media-archivist = streams, one job [\#49](https://github.com/LeMetadatarr/media-archivist/pull/49) ([JarbasAl](https://github.com/JarbasAl))

## [0.14.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.14.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.13.0a1...0.14.0a1)

**Merged pull requests:**

- feat: source-aware stream resolution \(bandcamp/soundcloud via native libs, yt-dlp for youtube\) [\#48](https://github.com/LeMetadatarr/media-archivist/pull/48) ([JarbasAl](https://github.com/JarbasAl))

## [0.13.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.13.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.12.0a1...0.13.0a1)

**Merged pull requests:**

- feat: /strm?resolve returns a 302 to a fresh yt-dlp URL \(Jellyfin play-time hook\) + optional proxy mode [\#47](https://github.com/LeMetadatarr/media-archivist/pull/47) ([JarbasAl](https://github.com/JarbasAl))

## [0.12.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.12.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.11.0a1...0.12.0a1)

**Merged pull requests:**

- feat: bulk accept/reject in Quarantine \(multi-select + select-all\) [\#45](https://github.com/LeMetadatarr/media-archivist/pull/45) ([JarbasAl](https://github.com/JarbasAl))

## [0.11.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.11.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.10.0a1...0.11.0a1)

**Merged pull requests:**

- feat: tagger uses embedded {tmdb-}/{imdb-} ids from Radarr/Sonarr names + skips trailers/extras [\#44](https://github.com/LeMetadatarr/media-archivist/pull/44) ([JarbasAl](https://github.com/JarbasAl))

## [0.10.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.10.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.9.0a1...0.10.0a1)

**Merged pull requests:**

- feat: Library pagination \(offset/limit + prev/next controls, filtered totals\) [\#43](https://github.com/LeMetadatarr/media-archivist/pull/43) ([JarbasAl](https://github.com/JarbasAl))

## [0.9.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.9.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.8.0a1...0.9.0a1)

**Merged pull requests:**

- feat: tag existing local media libraries \(scan -\> metadatarr resolve -\> .nfo sidecars, non-destructive\) [\#42](https://github.com/LeMetadatarr/media-archivist/pull/42) ([JarbasAl](https://github.com/JarbasAl))

## [0.8.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.8.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.7.0a1...0.8.0a1)

**Merged pull requests:**

- feat: optional download-to-library action \(scheduler-backed, progress-tracked\) [\#41](https://github.com/LeMetadatarr/media-archivist/pull/41) ([JarbasAl](https://github.com/JarbasAl))

## [0.7.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.7.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.6.0a1...0.7.0a1)

**Merged pull requests:**

- feat: Jellyfin/Kodi .nfo metadata sidecars + library layouts for .strm export [\#40](https://github.com/LeMetadatarr/media-archivist/pull/40) ([JarbasAl](https://github.com/JarbasAl))

## [0.6.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.6.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.5.0a1...0.6.0a1)

**Merged pull requests:**

- feat: yt-dlp stream resolution for player + /strm \(keep archived streams playable\) [\#39](https://github.com/LeMetadatarr/media-archivist/pull/39) ([JarbasAl](https://github.com/JarbasAl))

## [0.5.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.5.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.4.0a1...0.5.0a1)

**Merged pull requests:**

- feat: inline media player in entry detail \(watch/listen in-browser\) [\#38](https://github.com/LeMetadatarr/media-archivist/pull/38) ([JarbasAl](https://github.com/JarbasAl))

## [0.4.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.4.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.3.0a1...0.4.0a1)

**Merged pull requests:**

- feat: yt-dlp stream resolver + optional download \(core module + CLI\) [\#37](https://github.com/LeMetadatarr/media-archivist/pull/37) ([JarbasAl](https://github.com/JarbasAl))

## [0.3.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.3.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.2.2a1...0.3.0a1)

**Merged pull requests:**

- feat: WebUI UX + a11y polish \(safe quarantine actions, keyboard nav, visible DSL errors, honest health dot\) [\#36](https://github.com/LeMetadatarr/media-archivist/pull/36) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.2a1](https://github.com/LeMetadatarr/media-archivist/tree/0.2.2a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.2.1a1...0.2.2a1)

**Merged pull requests:**

- fix: cap where-DSL allocation \(unauth OOM\), honest healthz, indexed lookup, path leak, bandcamp explicit [\#35](https://github.com/LeMetadatarr/media-archivist/pull/35) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.1a1](https://github.com/LeMetadatarr/media-archivist/tree/0.2.1a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.2.0a2...0.2.1a1)

**Merged pull requests:**

- fix: thread-safe task submission and scheduler durability [\#33](https://github.com/LeMetadatarr/media-archivist/pull/33) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.0a2](https://github.com/LeMetadatarr/media-archivist/tree/0.2.0a2) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.2.0a1...0.2.0a2)

**Merged pull requests:**

- test: expand WebUI coverage + docs polish [\#34](https://github.com/LeMetadatarr/media-archivist/pull/34) ([JarbasAl](https://github.com/JarbasAl))

## [0.2.0a1](https://github.com/LeMetadatarr/media-archivist/tree/0.2.0a1) (2026-08-04)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.3a3...0.2.0a1)

**Merged pull requests:**

- feat: build-free htmx WebUI [\#32](https://github.com/LeMetadatarr/media-archivist/pull/32) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.3a3](https://github.com/LeMetadatarr/media-archivist/tree/0.1.3a3) (2026-08-03)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.3a2...0.1.3a3)

**Merged pull requests:**

- refactor: split cli.py monolith into media\_archivist/commands package [\#31](https://github.com/LeMetadatarr/media-archivist/pull/31) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.3a2](https://github.com/LeMetadatarr/media-archivist/tree/0.1.3a2) (2026-08-03)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.3a1...0.1.3a2)

**Merged pull requests:**

- refactor: rename canon.py → dedupe.py \(deprecated alias kept\) [\#30](https://github.com/LeMetadatarr/media-archivist/pull/30) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.3a1](https://github.com/LeMetadatarr/media-archivist/tree/0.1.3a1) (2026-08-03)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.2a1...0.1.3a1)

**Merged pull requests:**

- fix: test isolation — stub media leaked across tests via class mutation [\#29](https://github.com/LeMetadatarr/media-archivist/pull/29) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.2a1](https://github.com/LeMetadatarr/media-archivist/tree/0.1.2a1) (2026-08-03)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a9...0.1.2a1)

**Merged pull requests:**

- fix: floor ThreadPoolExecutor workers, bound provider HTTP timeout [\#28](https://github.com/LeMetadatarr/media-archivist/pull/28) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a9](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a9) (2026-08-03)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a8...0.1.1a9)

**Merged pull requests:**

- ci: grant conventional-label workflow write permissions [\#27](https://github.com/LeMetadatarr/media-archivist/pull/27) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a8](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a8) (2026-08-02)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a7...0.1.1a8)

**Merged pull requests:**

- fix: pin mediavocab\>=2.0.0a0 and metadatarr\>=0.5.0a0 \(prerelease floor pins\) [\#26](https://github.com/LeMetadatarr/media-archivist/pull/26) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a7](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a7) (2026-08-02)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a6...0.1.1a7)

**Merged pull requests:**

- deep: bugs, real-fixture tests, docs/examples [\#25](https://github.com/LeMetadatarr/media-archivist/pull/25) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a6](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a6) (2026-08-02)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a5...0.1.1a6)

**Merged pull requests:**

- docs: QA pass — accuracy, org-move URLs, related projects [\#24](https://github.com/LeMetadatarr/media-archivist/pull/24) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a5](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a5) (2026-07-30)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a4...0.1.1a5)

**Merged pull requests:**

- docs: rewrite README in Simplified Technical English [\#22](https://github.com/LeMetadatarr/media-archivist/pull/22) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a4](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a4) (2026-06-27)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a3...0.1.1a4)

**Merged pull requests:**

- docs: timeless documentation cleanup [\#15](https://github.com/LeMetadatarr/media-archivist/pull/15) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a3](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a3) (2026-06-27)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a2...0.1.1a3)

**Merged pull requests:**

- chore: add LICENSE, fix repo URLs, drop duplicate license workflow [\#13](https://github.com/LeMetadatarr/media-archivist/pull/13) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a2](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a2) (2026-06-27)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.1a1...0.1.1a2)

**Merged pull requests:**

- chore: migrate to mediavocab 1.0 [\#10](https://github.com/LeMetadatarr/media-archivist/pull/10) ([JarbasAl](https://github.com/JarbasAl))

## [0.1.1a1](https://github.com/LeMetadatarr/media-archivist/tree/0.1.1a1) (2026-05-07)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.0...0.1.1a1)

**Merged pull requests:**

- chore\(deps\): update actions/checkout action to v6 [\#4](https://github.com/LeMetadatarr/media-archivist/pull/4) ([renovate[bot]](https://github.com/apps/renovate))

## [0.1.0](https://github.com/LeMetadatarr/media-archivist/tree/0.1.0) (2026-05-07)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/0.1.0a2...0.1.0)

## [0.1.0a2](https://github.com/LeMetadatarr/media-archivist/tree/0.1.0a2) (2026-05-07)

[Full Changelog](https://github.com/LeMetadatarr/media-archivist/compare/a642b6baf3a810e0b51932c563324fc2f5a9ea9c...0.1.0a2)

**Merged pull requests:**

- Configure Renovate [\#1](https://github.com/LeMetadatarr/media-archivist/pull/1) ([renovate[bot]](https://github.com/apps/renovate))



\* *This Changelog was automatically generated by [github_changelog_generator](https://github.com/github-changelog-generator/github-changelog-generator)*
