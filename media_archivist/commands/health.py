# SPDX-License-Identifier: Apache-2.0
"""CLI handler for ``media-archivist health``."""
from __future__ import annotations

import sys

from media_archivist import health as _health
from media_archivist.commands._helpers import _index_for


def cmd_health(args) -> int:
    db_path = args.db_file or _index_for(args).path

    results = _health.check_library(
        db_path, source=args.source_filter, where=args.where,
        limit=args.limit or None,
    )

    counts = {"ok": 0, "dead": 0, "expired": 0, "no-stream": 0, "gone": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        line = f"[{r.status:9s}] {r.source:16s} {r.entry_id}  {r.title}"
        if r.reason:
            line += f"  ({r.reason})"
        print(line)

    print(
        f"\nsummary: {len(results)} checked — "
        f"ok={counts['ok']} dead={counts['dead']} expired={counts['expired']} "
        f"no-stream={counts['no-stream']} gone={counts['gone']}",
        file=sys.stderr,
    )

    gone = [r for r in results if r.status == "gone"]
    if gone:
        # This is the headline case (deleted/unavailable source videos) —
        # surface it prominently and separately from the re-resolve flow,
        # since re-resolving a deleted video is pointless: it needs a
        # human decision (remove/replace), not a refresh.
        print(
            f"\n{len(gone)} entries point at deleted/unavailable sources "
            "(will NOT be re-resolved — re-run with --remove-gone to drop "
            "them from the index):",
            file=sys.stderr,
        )
        for r in gone:
            print(f"  gone: {r.title}  {r.url}", file=sys.stderr)

    if args.remove_gone and gone:
        from media_archivist.index import Index

        idx = Index(db_path)
        removed = 0
        for r in gone:
            entry = idx.get(r.entry_id)
            if entry is None:
                continue
            if args.dry_run:
                print(f"would remove {r.entry_id}: {r.title}", file=sys.stderr)
                continue
            if _health.remove_entry(db_path, entry):
                removed += 1
                print(f"removed {r.entry_id}: {r.title}", file=sys.stderr)
        if not args.dry_run:
            print(f"removed {removed}/{len(gone)} gone entries", file=sys.stderr)

    if not args.reresolve:
        return 0

    unhealthy = [r for r in results if r.status in ("dead", "expired")]
    if not unhealthy:
        print("nothing to re-resolve", file=sys.stderr)
        return 0

    from media_archivist.index import Index

    idx = Index(db_path)
    refreshed = failed = 0
    for r in unhealthy:
        entry = idx.get(r.entry_id)
        if entry is None:
            failed += 1
            print(f"re-resolve {r.entry_id}: entry no longer in DB", file=sys.stderr)
            continue
        result = _health.reresolve_entry(db_path, entry, dry_run=args.dry_run)
        if result.ok:
            refreshed += 1
            verb = "would refresh" if args.dry_run else "refreshed"
            print(
                f"re-resolve {r.entry_id}: {verb} "
                f"{result.old_stream or '(none)'} -> {result.new_stream}",
                file=sys.stderr,
            )
        else:
            failed += 1
            print(f"re-resolve {r.entry_id}: failed ({result.error})", file=sys.stderr)

    mode = "dry-run" if args.dry_run else "applied"
    print(
        f"re-resolve summary ({mode}): {refreshed} ok, {failed} failed "
        f"of {len(unhealthy)} unhealthy",
        file=sys.stderr,
    )
    return 0
