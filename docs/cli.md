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

`--db` and `--db-file` are mutually exclusive; exactly one is required.

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

- `_BaseCliArgs._exactly_one_target` — exactly one of `--db` / `--db-file`.
- `PruneArgs._at_least_one_action` — at least one of `--unavailable`,
  `--below`, `--missing`, `--blacklist`.
- `MonitorArgs._no_ia_for_monitor` — IA backend is incompatible with the
  monitor loop.
- `MergeArgs._at_least_one_source` — `merge` requires source paths.
- `ExportArgs.format` is constrained to `Literal["json", "jsonl", "csv",
  "txt"]`.
- `extra="forbid"` on the base model surfaces typos in any subcommand.

## Reusing the validators in scripts

The same models are public — call them directly when scripting:

```python
from media_archivist.cli_args import ExportArgs

# Reject invalid format with a clear pydantic error before doing any work.
args = ExportArgs(db_file="talks.json", format="jsonl",
                  fields="videoId,title,url")
```

See [`examples/scripted_export.py`](../examples/scripted_export.py).
