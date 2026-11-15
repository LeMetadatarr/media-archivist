# CLI architecture

`media-archivist` is built on `argparse` with a thin pydantic validation
layer (`media_archivist/cli_args.py`) that catches invalid argument
combinations before any I/O runs.

## Subcommand reference

| Subcommand   | Validator       | Purpose |
| ------------ | --------------- | ------- |
| `add`        | `AddArgs`       | Archive one or more URLs into the DB. |
| `urls`       | `UrlsArgs`      | Print stored URLs (pipe to `yt-dlp -a -`). |
| `list`       | `ListArgs`      | Print `title<TAB>url` rows or full JSON. |
| `dump`       | `DumpArgs`      | Dump the full envelope as JSON. |
| `export`     | `ExportArgs`    | Project entries to JSON / JSONL / CSV / TXT. |
| `import`     | `ImportArgs`    | Load entries from a JSON or JSONL file. |
| `merge`      | `MergeArgs`     | Merge other DB files into this one. |
| `stats`      | `StatsArgs`     | Total / live / playlists / field coverage. |
| `prune`      | `PruneArgs`     | Drop entries by various criteria. |
| `bootstrap`  | `BootstrapArgs` | Seed an empty DB from a remote JSON dump. |
| `monitor`    | `MonitorArgs`   | Background-poll URLs and keep the DB in sync. |

## Common flags

Every subcommand accepts:

```
--db NAME            DB name (auto-placed under XDG)
--db-file PATH       Explicit path to the JSON DB file
--ia | --music | --bandcamp | --soundcloud   Backend selector (default YouTube)
--require KW         Only index entries whose title contains all of these
--blacklist KW       Skip entries whose title contains any of these
--min-duration N     Minimum duration in seconds (where length is exposed)
--skip-explicit      (YT Music) skip tracks flagged explicit
--only-audio         (YT Music) keep only audio-only tracks
```

`--db` and `--db-file` are mutually exclusive. Exactly one is required.

## Validation behaviour

Each handler calls `_validated_args(SomeArgsModel, ns)` early, which
constructs the appropriate pydantic model from the argparse namespace.
On `ValidationError` the CLI exits with code 1 and a single-line error:

```text
$ media-archivist prune --db-file talks.json
error: Value error, prune requires at least one of: --unavailable, --below, --missing, --blacklist

$ media-archivist monitor --db-file talks.json --ia https://...
error: Value error, --ia is not supported with monitor

$ media-archivist list
error: pass --db NAME or --db-file PATH
```

Validators encode rules that are awkward to express with `argparse`
alone:

- `_BaseCliArgs._exactly_one_target`, exactly one of `--db` / `--db-file`.
- `PruneArgs._at_least_one_action`, at least one of `--unavailable`,
  `--below`, `--missing`, `--blacklist`.
- `MonitorArgs._no_ia_for_monitor`, IA backend is incompatible with the
  monitor loop.
- `MergeArgs._at_least_one_source`, `merge` requires source paths.
- `ExportArgs.format` is constrained to `Literal["json", "jsonl", "csv",
  "txt"]`.
- `extra="forbid"` on the base model surfaces typos in any subcommand.

## Reusing the validators in scripts

The same models are public, call them directly when scripting:

```python
from media_archivist.cli_args import ExportArgs

# Reject invalid format with a clear pydantic error before doing any work.
args = ExportArgs(db_file="talks.json", format="jsonl",
                  fields="videoId,title,url")
```

See [`examples/scripted_export.py`](../examples/scripted_export.py).

## Subscriptions from the command line

These commands follow channels and playlists in a JSON database. The server
keeps its own subscriptions in the working index; see the [app API](app-api.md).

```bash
# Subscriptions: remember a channel/playlist/collection so new uploads keep
# getting auto-indexed, without re-typing every URL each run
media-archivist subscribe --db-file talks.json https://www.youtube.com/@LinusTechTips
media-archivist subscribe --db-file talks.json --backend ia --label "Cartoons" \
    https://archive.org/details/classic_cartoons
# --download flags a subscription to always download newly-indexed items
media-archivist subscribe --db-file talks.json --download \
    https://www.youtube.com/@SomeArtist
media-archivist subscriptions --db-file talks.json
media-archivist sync-subscriptions --db-file talks.json   # archive() dedupes; only new uploads land
media-archivist unsubscribe --db-file talks.json https://www.youtube.com/@LinusTechTips

# Periodic auto-sync: keep re-running sync-subscriptions forever, on an
# interval, indexing (and optionally downloading) new uploads as they show
# up. This is a foreground daemon (Ctrl-C to stop) — run it under
# systemd/docker for unattended "curate once, keep growing" operation:
#   systemd: ExecStart=media-archivist sync-subscriptions --db-file talks.json --interval 3600 --download
#   docker:  CMD ["media-archivist", "sync-subscriptions", "--db-file", "/data/talks.json", "--interval", "3600"]
media-archivist sync-subscriptions --db-file talks.json --interval 3600
# --download (with --interval or alone) downloads every newly-indexed item
# this run, in addition to any subscription's own --download flag
media-archivist sync-subscriptions --db-file talks.json --download
```

## Output formats

| `--format` | Use case |
| --- | --- |
| `jsonl` *(default)* | streaming pipelines, HuggingFace `datasets`, `jq` |
| `json` | small datasets, human inspection |
| `csv` | pandas, spreadsheets, list/dict fields auto-serialized to JSON strings |
| `txt` | flat URL list for `yt-dlp -a -` / `wget -i` / `xargs` |

Combine with `--fields` to project only what you need, `--grep` to filter by
title substring, and `--limit N` to cap row count.

## Stored fields per video

| field | source |
| --- | --- |
| `videoId`, `url`, `title`, `thumbnail` | tutubo `Video` |
| `tags` | union of `Video.keywords` and inferred `Video.tags` |
| `is_live`, `published`, `views`, `description` | tutubo channel-grid metadata |
| `playlist` | only set when archived from a playlist |

The `examples/` directory of the repository holds end-to-end dataset-creation scripts.

---
[← Jellyfin / Kodi Remote Media](jellyfin.md) · [Home](index.md) · [CI / Release Automation →](ci.md)
