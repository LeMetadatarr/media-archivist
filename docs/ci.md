# CI / release automation

The repository delegates to the
[`OpenVoiceOS/gh-automations`](https://github.com/OpenVoiceOS/gh-automations)
reusable workflows (pinned to `@dev`) rather than per-repo CI logic.

| Workflow | File | Triggered by | Purpose |
| --- | --- | --- | --- |
| Build & Test | `.github/workflows/build-tests.yml` | push / PR to `master` / `dev` | `python -m build` + `pytest` on Py 3.10 / 3.11 / 3.12. |
| Lint | `.github/workflows/lint.yml` | push / PR | `ruff check` over `media_archivist/` and `test/`. |
| Coverage | `.github/workflows/coverage.yml` | push / PR to `master` | `pytest --cov` report. |
| License Check | `.github/workflows/license-check.yml` | push / PR | Reject GPL / AGPL / EULA dependencies (Apache-2.0 universal-donor policy). |
| Release Preview | `.github/workflows/release-preview.yml` | push to `dev` | Bumps alpha version, opens release PR. |
| Release | `.github/workflows/release.yml` | tag `v*` | Publishes a stable release. |

## Branching model

- `master`, stable. Tagged releases cut here.
- `dev`, integration branch. PRs land here first. The alpha-release
  workflow opens a release PR `dev → master` once changes are ready to
  ship.

## Local checks before pushing

```bash
pip install -e .[bandcamp,soundcloud,test]
pytest test/                    # 30+ tests, no network
ruff check media_archivist test
python -m build                 # smoke-tests the build
```

## Notes

- All reusable workflows are referenced via `@dev` per repo convention.
- The `install_extras` input lists optional dependency groups that should
  be installed before running tests (`bandcamp`, `soundcloud`).
- The release workflow reads the version from
  `media_archivist/version.py` automatically.

---
[← CLI Architecture](cli.md) · [Home](index.md) · [Roadmap →](roadmap.md)
