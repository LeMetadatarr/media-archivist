"""CLI handlers for ``resolve`` / ``download`` — thin wrappers over
:mod:`media_archivist.streams`.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from urllib.parse import urlparse

from media_archivist import streams as _streams


def _looks_like_url(value: str) -> bool:
    return urlparse(value).scheme in ("http", "https")


def _resolve_target_url(args) -> str:
    """Accept either a raw http(s) URL or a stored entry id."""
    target = args.url_or_id
    if _looks_like_url(target):
        return target
    if not args.db_file and not args.db:
        raise SystemExit(
            "error: entry id lookup requires --db-file PATH or --db NAME"
        )
    from media_archivist.commands._helpers import _index_for

    idx = _index_for(args)
    entry = idx.get(target)
    if entry is None:
        raise SystemExit(f"error: no entry with id {target!r} in the DB")
    url = entry.stream or entry.url
    if not url:
        raise SystemExit(f"error: entry {target!r} has no url or stream to resolve")
    return url


def cmd_resolve(args) -> int:
    try:
        target_url = _resolve_target_url(args)
        result = _streams.resolve_stream(target_url, prefer=args.format)
    except _streams.StreamResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json_out:
        json.dump(dataclasses.asdict(result), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(result.url)
    return 0


def _entries_to_download(args):
    """Resolve the list of (label, url) pairs a `download` invocation targets."""
    if args.url:
        return [(args.url, args.url)]
    if not args.db_file and not args.db:
        raise SystemExit(
            "error: pass --url, or --db-file/--db plus --id/--where/--source"
        )
    from media_archivist.commands._helpers import _index_for

    idx = _index_for(args)
    if args.id:
        entry = idx.get(args.id)
        if entry is None:
            raise SystemExit(f"error: no entry with id {args.id!r} in the DB")
        url = entry.stream or entry.url
        if not url:
            raise SystemExit(f"error: entry {args.id!r} has no url or stream")
        return [(args.id, url)]
    entries = idx.to_list(where=args.where, source=args.source_filter)
    return [(e.title or e.url, e.stream or e.url) for e in entries if (e.stream or e.url)]


def cmd_download(args) -> int:
    targets = _entries_to_download(args)
    if not targets:
        print("no matching entries to download", file=sys.stderr)
        return 0

    ok = 0
    for label, url in targets:
        def _hook(d: dict, _label=label) -> None:
            if d.get("status") == "downloading":
                pct = d.get("_percent_str", "").strip()
                print(f"  {_label}: {pct}", file=sys.stderr)

        try:
            path = _streams.download(
                url, args.output_dir, format=args.format, progress_hook=_hook,
            )
        except _streams.StreamDownloadError as e:
            print(f"error: {label}: {e}", file=sys.stderr)
            continue
        print(str(path))
        ok += 1

    print(f"downloaded {ok}/{len(targets)} entries", file=sys.stderr)
    return 0 if ok == len(targets) else 1
