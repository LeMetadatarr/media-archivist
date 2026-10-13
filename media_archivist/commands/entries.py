"""Entry-oriented CLI command handlers (add, list, export, import, ...)."""
from __future__ import annotations

import csv
import json
import sys

from media_archivist import cli_args as _cli_args
from media_archivist.commands._helpers import (
    DEFAULT_FIELDS,
    _filter_entries,
    _index_for,
    _make_archivist,
    _project,
    _resolve_view,
    _validated_args,
)


def cmd_add(args) -> int:
    _validated_args(_cli_args.AddArgs, args)
    archivist = _make_archivist(args)
    for url in args.urls:
        archivist.archive(url)
    print(f"db now contains {len(archivist.video_urls)} entries", file=sys.stderr)
    return 0


def cmd_urls(args) -> int:
    if args.canonical or args.where or args.source_filter or args.has_stream is not None:
        for entry in _resolve_view(args):
            if entry.url:
                print(entry.url)
        return 0
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
    use_view = (args.canonical or args.where or args.source_filter
                or args.has_stream is not None or args.explicit_filter is not None)
    if use_view:
        entries = _resolve_view(args)
        if args.json_out:
            json.dump([e.model_dump(mode="json") for e in entries], sys.stdout,
                      indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
        for e in entries:
            print(f"{e.title or '(no title)'}\t{e.url}")
        return 0
    archivist = _make_archivist(args)
    entries = _filter_entries(archivist.sorted_entries(), args.grep)
    if args.limit:
        entries = entries[: args.limit]
    if args.json_out:
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

    use_view = (args.canonical or args.where or args.source_filter
                or args.has_stream is not None)
    if use_view:
        view_entries = _resolve_view(args, defaults_grep=False)
        # When --canonical, project the MediaEntry rows; entries var holds dicts either way.
        entries = [e.model_dump(mode="json") for e in view_entries]
        if args.grep:
            needle = args.grep.lower()
            entries = [e for e in entries if needle in (e.get("title") or "").lower()]
    else:
        archivist = _make_archivist(args)
        entries = _filter_entries(archivist.sorted_entries(), args.grep)
        if args.limit:
            entries = entries[: args.limit]

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None

    # ---- splits ----
    split_groups: dict[str, list] = {"": list(entries)}  # default: single group
    if args.split:
        from media_archivist.hub import split_jsonl
        split_groups = split_jsonl(list(entries), args.split)
    elif args.split_by:
        groups: dict[str, list] = {}
        for e in entries:
            key = str(e.get(args.split_by) or "")
            groups.setdefault(key, []).append(e)
        split_groups = groups

    def _emit(rows: list, sink) -> None:
        if args.format == "jsonl":
            for entry in rows:
                row = _project(entry, fields) if fields else entry
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
        elif args.format == "json":
            payload = [_project(e, fields) if fields else e for e in rows]
            json.dump(payload, sink, indent=2, ensure_ascii=False)
            sink.write("\n")
        elif args.format == "csv":
            cols = fields or DEFAULT_FIELDS
            writer = csv.DictWriter(sink, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for entry in rows:
                row = {c: entry.get(c) for c in cols}
                for k, v in list(row.items()):
                    if isinstance(v, (list, dict)):
                        row[k] = json.dumps(v, ensure_ascii=False)
                writer.writerow(row)
        elif args.format == "txt":
            for entry in rows:
                url = entry.get("url") or ""
                if url:
                    sink.write(url + "\n")
        else:
            raise ValueError(f"unknown format: {args.format}")

    if not (args.split or args.split_by):
        out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
        try:
            _emit(list(entries), out)
        finally:
            if args.output:
                out.close()
        print(f"exported {len(entries)} entries", file=sys.stderr)
        return 0

    # Split path: requires --output as a base path (e.g. -o data.jsonl).
    if not args.output:
        print("error: --split / --split-by require -o BASE_PATH", file=sys.stderr)
        return 2
    from pathlib import Path
    base = Path(args.output)
    stem, suffix = base.with_suffix(""), base.suffix or f".{args.format}"
    total_emitted = 0
    for label, rows in split_groups.items():
        out_path = Path(f"{stem}.{label}{suffix}") if label else base
        with out_path.open("w", encoding="utf-8") as f:
            _emit(rows, f)
        total_emitted += len(rows)
        print(f"  {out_path}: {len(rows)} rows", file=sys.stderr)
    print(f"exported {total_emitted} entries across {len(split_groups)} files",
          file=sys.stderr)
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


def cmd_strm_export(args) -> int:
    """Write one .strm per matching entry, ready for Jellyfin / Kodi to pick up."""
    _validated_args(_cli_args.StrmExportArgs, args)
    from media_archivist.strm import export_strm

    db_path = args.db_file or _index_for(args).path
    n = export_strm(
        db_path,
        args.output_dir,
        base_url=args.base_url,
        source=args.source_filter,
        where=args.where,
        has_stream=args.has_stream,
        limit=args.limit,
        dry_run=args.dry_run,
        layout=args.layout,
        nfo=args.nfo,
    )
    print(f"{'would write' if args.dry_run else 'wrote'} {n} .strm files",
          file=sys.stderr)
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
