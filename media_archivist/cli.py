"""Command-line interface for media_archivist.

Designed for dataset-creation workflows. Pairs naturally with ``yt-dlp``:
this tool indexes stream metadata into a JSON database; download on demand
by piping URLs to ``yt-dlp -a -``.

Two ways to point at a database:

- ``--db-file ./my_dataset.json`` — explicit path (recommended for datasets
  you commit alongside scripts).
- ``--db NAME`` — auto-placed under XDG at
  ``~/.local/share/media_archivist/<NAME>.json``.

Examples::

    media-archivist --db-file talks.json add https://www.youtube.com/@SomeChannel
    media-archivist --db-file talks.json urls | yt-dlp -a -
    media-archivist --db-file talks.json export --format jsonl > talks.jsonl
    media-archivist --db-file talks.json export --format csv \\
        --fields videoId,title,url,published > talks.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from typing import Iterable, List, Optional

from media_archivist.ia import IAArchivist
from media_archivist.music import YoutubeMusicArchivist
from media_archivist.version import __version__
from media_archivist.youtube import YoutubeArchivist

try:
    from media_archivist.bandcamp import BandcampArchivist
except Exception:  # pragma: no cover
    BandcampArchivist = None  # type: ignore
try:
    from media_archivist.soundcloud import SoundCloudArchivist
except Exception:  # pragma: no cover
    SoundCloudArchivist = None  # type: ignore

from pydantic import ValidationError

from media_archivist import cli_args as _cli_args

DEFAULT_FIELDS = ["videoId", "title", "url", "thumbnail", "published",
                  "views", "is_live", "tags", "description", "playlist"]


_BACKEND_TO_CLS = {
    "youtube": ("YoutubeArchivist", lambda: YoutubeArchivist),
    "ia": ("IAArchivist", lambda: IAArchivist),
    "music": ("YoutubeMusicArchivist", lambda: YoutubeMusicArchivist),
    "bandcamp": ("BandcampArchivist", lambda: BandcampArchivist),
    "soundcloud": ("SoundCloudArchivist", lambda: SoundCloudArchivist),
}


def _validated_args(model_cls, args, **overrides):
    """Convert an argparse namespace into a validated CliArgs model."""
    try:
        return _cli_args.from_namespace(model_cls, args, **overrides)
    except ValidationError as e:
        msg = "; ".join(err.get("msg", "invalid") for err in e.errors())
        raise SystemExit(f"error: {msg}")


def _make_archivist(args, *, db_override: Optional[str] = None,
                    db_file_override: Optional[str] = None):
    """Construct the right archivist for the validated CLI args."""
    backend = _cli_args.backend_from_namespace(args)
    cls_name, cls_factory = _BACKEND_TO_CLS[backend]
    cls = cls_factory()
    if cls is None:
        extra = {"bandcamp": "py_bandcamp", "soundcloud": "nuvem_de_som"}.get(backend)
        raise SystemExit(f"error: {backend} backend requires `pip install {extra}`")
    db_name = db_override if db_override is not None else args.db
    db_path = db_file_override if db_file_override is not None else args.db_file
    if not db_name and not db_path:
        raise SystemExit("error: pass --db NAME or --db-file PATH")
    kwargs = dict(
        db_name=db_name,
        db_path=db_path,
        required_kwords=getattr(args, "require", None) or [],
        blacklisted_kwords=getattr(args, "blacklist", None) or [],
        min_duration=getattr(args, "min_duration", -1),
    )
    if cls is YoutubeMusicArchivist:
        kwargs["skip_explicit"] = getattr(args, "skip_explicit", False)
        kwargs["only_audio"] = getattr(args, "only_audio", False)
    return cls(**kwargs)


def _filter_entries(entries: Iterable[dict], grep: Optional[str]) -> List[dict]:
    if not grep:
        return list(entries)
    needle = grep.lower()
    return [e for e in entries if needle in (e.get("title") or "").lower()]


def _project(entry: dict, fields: List[str]) -> dict:
    return {f: entry.get(f) for f in fields}


def cmd_add(args) -> int:
    _validated_args(_cli_args.AddArgs, args)
    archivist = _make_archivist(args)
    for url in args.urls:
        archivist.archive(url)
    print(f"db now contains {len(archivist.video_urls)} entries", file=sys.stderr)
    return 0


def cmd_urls(args) -> int:
    archivist = _make_archivist(args)
    entries = _filter_entries(archivist.sorted_entries(), args.grep)
    if args.limit:
        entries = entries[: args.limit]
    for entry in entries:
        url = entry.get("url") or ""
        if url:
            print(url)
    return 0


def cmd_list(args) -> int:
    archivist = _make_archivist(args)
    entries = _filter_entries(archivist.sorted_entries(), args.grep)
    if args.limit:
        entries = entries[: args.limit]
    if args.json:
        json.dump(entries, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    for entry in entries:
        title = entry.get("title") or "(no title)"
        url = entry.get("url") or ""
        print(f"{title}\t{url}")
    return 0


def cmd_dump(args) -> int:
    archivist = _make_archivist(args)
    json.dump(dict(archivist.db), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_export(args) -> int:
    """Emit the DB as JSON, JSONL, CSV, or a flat URL list — with optional field projection."""
    _validated_args(_cli_args.ExportArgs, args)
    archivist = _make_archivist(args)
    entries = _filter_entries(archivist.sorted_entries(), args.grep)
    if args.limit:
        entries = entries[: args.limit]

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        if args.format == "jsonl":
            for entry in entries:
                row = _project(entry, fields) if fields else entry
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        elif args.format == "json":
            rows = [_project(e, fields) if fields else e for e in entries]
            json.dump(rows, out, indent=2, ensure_ascii=False)
            out.write("\n")
        elif args.format == "csv":
            cols = fields or DEFAULT_FIELDS
            writer = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for entry in entries:
                row = {c: entry.get(c) for c in cols}
                # serialize lists/dicts so CSV stays flat
                for k, v in list(row.items()):
                    if isinstance(v, (list, dict)):
                        row[k] = json.dumps(v, ensure_ascii=False)
                writer.writerow(row)
        elif args.format == "txt":
            for entry in entries:
                url = entry.get("url") or ""
                if url:
                    out.write(url + "\n")
        else:
            print(f"unknown format: {args.format}", file=sys.stderr)
            return 2
    finally:
        if args.output:
            out.close()
    print(f"exported {len(entries)} entries", file=sys.stderr)
    return 0


def cmd_import(args) -> int:
    """Load entries from an external JSON or JSONL file into the DB."""
    _validated_args(_cli_args.ImportArgs, args)
    archivist = _make_archivist(args)
    added = 0
    with open(args.path, encoding="utf-8") as f:
        if args.path.endswith(".jsonl"):
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                url = entry.get("url")
                if not url:
                    continue
                if url in archivist.db and not args.overwrite:
                    continue
                archivist.db[url] = entry
                added += 1
        else:
            data = json.load(f)
            if isinstance(data, list):
                items = [(e.get("url"), e) for e in data]
            else:
                items = list(data.items())
            for url, entry in items:
                if not url:
                    continue
                if url in archivist.db and not args.overwrite:
                    continue
                archivist.db[url] = entry
                added += 1
    archivist.db.store()
    print(f"imported {added} entries", file=sys.stderr)
    return 0


def cmd_merge(args) -> int:
    """Merge multiple source DBs into the destination."""
    _validated_args(_cli_args.MergeArgs, args)
    dest = _make_archivist(args)
    added = 0
    for src_path in args.sources:
        src = _make_archivist(args, db_override=None, db_file_override=src_path)
        for url, entry in src.db.items():
            if url in dest.db and not args.overwrite:
                continue
            dest.db[url] = entry
            added += 1
    dest.db.store()
    print(f"merged {added} entries from {len(args.sources)} source(s)", file=sys.stderr)
    return 0


def cmd_stats(args) -> int:
    archivist = _make_archivist(args)
    entries = list(archivist.db.values())
    total = len(entries)
    live = sum(1 for e in entries if e.get("is_live"))
    by_playlist: dict[str, int] = {}
    for e in entries:
        pl = e.get("playlist")
        if pl:
            by_playlist[pl] = by_playlist.get(pl, 0) + 1
    field_coverage = {
        f: sum(1 for e in entries if e.get(f) not in (None, "", [], {}))
        for f in DEFAULT_FIELDS
    }
    out = {
        "total": total,
        "live": live,
        "playlists": by_playlist,
        "field_coverage": field_coverage,
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_prune(args) -> int:
    _validated_args(_cli_args.PruneArgs, args)
    archivist = _make_archivist(args)
    before = len(archivist.video_urls)
    if args.unavailable:
        archivist.remove_unavailable()
    if args.blacklist:
        archivist.remove_keyword(args.blacklist)
    if args.below is not None:
        archivist.remove_below_duration(args.below)
    if args.missing:
        archivist.remove_missing(args.missing)
    after = len(archivist.video_urls)
    print(f"pruned {before - after} entries ({before} → {after})", file=sys.stderr)
    return 0


def cmd_bootstrap(args) -> int:
    archivist = _make_archivist(args)
    if hasattr(archivist, "bootstrap_from_url"):
        archivist.bootstrap_from_url(args.url)
    else:
        print("bootstrap not supported for this archivist", file=sys.stderr)
        return 1
    return 0


def cmd_monitor(args) -> int:
    from media_archivist.youtube import YoutubeMonitor

    _validated_args(_cli_args.MonitorArgs, args)
    monitor = YoutubeMonitor(
        db_name=args.db,
        db_path=args.db_file,
        required_kwords=args.require or [],
        blacklisted_kwords=args.blacklist or [],
        min_duration=args.min_duration,
        sync_interval=args.interval,
    )
    monitor.start()
    for url in args.urls:
        monitor.monitor(url)
    print(f"monitoring {len(args.urls)} URLs every {args.interval}s — Ctrl-C to stop",
          file=sys.stderr)
    try:
        monitor.join()
    except KeyboardInterrupt:
        monitor.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-archivist",
        description="Index YouTube and Internet Archive streams into a local JSON database.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")

    common = argparse.ArgumentParser(add_help=False)
    g = common.add_mutually_exclusive_group()
    g.add_argument("--db", metavar="NAME",
                   help="DB name (auto-placed under XDG data dir as <NAME>.json)")
    g.add_argument("--db-file", metavar="PATH",
                   help="explicit path to the JSON DB file (recommended for datasets)")
    backend = common.add_mutually_exclusive_group()
    backend.add_argument("--ia", action="store_true",
                         help="use the Internet Archive backend")
    backend.add_argument("--music", action="store_true",
                         help="use the YouTube Music backend (rich track metadata)")
    backend.add_argument("--bandcamp", action="store_true",
                         help="use the Bandcamp backend (py_bandcamp)")
    backend.add_argument("--soundcloud", action="store_true",
                         help="use the SoundCloud backend (nuvem_de_som)")
    common.add_argument("--skip-explicit", action="store_true",
                        help="(YT Music) skip tracks flagged explicit")
    common.add_argument("--only-audio", action="store_true",
                        help="(YT Music) keep only audio-only tracks (no music videos)")
    common.add_argument("--require", action="append", metavar="KW",
                        help="only index entries whose title contains all of these keywords")
    common.add_argument("--blacklist", action="append", metavar="KW",
                        help="skip entries whose title contains any of these keywords")
    common.add_argument("--min-duration", type=int, default=-1,
                        help="minimum duration in seconds (applies when source exposes "
                             "length: search results, YT Music, IA — not bare channel scrapes)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", parents=[common], help="archive one or more URLs")
    p_add.add_argument("urls", nargs="+")
    p_add.set_defaults(func=cmd_add)

    p_urls = sub.add_parser("urls", parents=[common],
                            help="print stored URLs (pipe to `yt-dlp -a -`)")
    p_urls.add_argument("--grep", help="filter by substring in title")
    p_urls.add_argument("--limit", type=int, default=0)
    p_urls.set_defaults(func=cmd_urls)

    p_list = sub.add_parser("list", parents=[common], help="list entries (title<TAB>url)")
    p_list.add_argument("--grep", help="filter by substring in title")
    p_list.add_argument("--limit", type=int, default=0)
    p_list.add_argument("--json", action="store_true", help="emit JSON array")
    p_list.set_defaults(func=cmd_list)

    p_dump = sub.add_parser("dump", parents=[common], help="dump full DB as JSON")
    p_dump.set_defaults(func=cmd_dump)

    p_export = sub.add_parser("export", parents=[common],
                              help="export entries as a dataset (json/jsonl/csv/txt)")
    p_export.add_argument("--format", choices=["json", "jsonl", "csv", "txt"], default="jsonl")
    p_export.add_argument("--fields", metavar="A,B,C",
                          help="comma-separated field names to project (default: all)")
    p_export.add_argument("--grep", help="filter by substring in title")
    p_export.add_argument("--limit", type=int, default=0)
    p_export.add_argument("-o", "--output", metavar="PATH",
                          help="write to PATH instead of stdout")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", parents=[common],
                              help="import a JSON or JSONL file into the DB")
    p_import.add_argument("path", help=".json (dict or list) or .jsonl input file")
    p_import.add_argument("--overwrite", action="store_true",
                          help="replace existing entries with the same URL")
    p_import.set_defaults(func=cmd_import)

    p_merge = sub.add_parser("merge", parents=[common],
                             help="merge other DB files into this one")
    p_merge.add_argument("sources", nargs="+", help="source .json DB paths")
    p_merge.add_argument("--overwrite", action="store_true",
                         help="replace existing entries with the same URL")
    p_merge.set_defaults(func=cmd_merge)

    p_stats = sub.add_parser("stats", parents=[common],
                             help="print dataset statistics (total, live, per-playlist, field coverage)")
    p_stats.set_defaults(func=cmd_stats)

    p_prune = sub.add_parser("prune", parents=[common], help="remove entries by various criteria")
    p_prune.add_argument("--unavailable", action="store_true",
                         help="drop entries that no longer resolve (oEmbed probe)")
    p_prune.add_argument("--below", type=int, metavar="MINUTES",
                         help="drop entries shorter than MINUTES")
    p_prune.add_argument("--missing", action="append", metavar="FIELD",
                         help="drop entries missing FIELD (repeatable)")
    p_prune.set_defaults(func=cmd_prune)

    p_boot = sub.add_parser("bootstrap", parents=[common],
                            help="seed an empty DB from a remote JSON dump")
    p_boot.add_argument("url")
    p_boot.set_defaults(func=cmd_bootstrap)

    p_mon = sub.add_parser("monitor", parents=[common],
                           help="background-poll URLs and keep the DB in sync")
    p_mon.add_argument("urls", nargs="+")
    p_mon.add_argument("--interval", type=int, default=120,
                       help="seconds between syncs (default: 120)")
    p_mon.set_defaults(func=cmd_monitor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
