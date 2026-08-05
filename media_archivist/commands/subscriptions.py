# SPDX-License-Identifier: Apache-2.0
"""CLI handlers for channel/playlist subscriptions.

Complements ``sync --rss`` / ``monitor``: those take URLs on the command
line each time, while subscriptions remember a curator's channel/playlist
list on disk (``<db>.subscriptions.json``) so ``sync-subscriptions`` can be
re-run (e.g. from cron) without re-typing every URL.
"""
from __future__ import annotations

import sys

from media_archivist import subscriptions as subs_mod
from media_archivist.commands._helpers import _index_for


def _db_path(args) -> str:
    return args.db_file or _index_for(args).path


def cmd_subscribe(args) -> int:
    db_path = _db_path(args)
    try:
        sub = subs_mod.add_subscription(
            db_path, args.url, backend=args.backend, label=args.label,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    label = f" ({sub.label})" if sub.label else ""
    print(f"subscribed: {sub.url}{label} [{sub.backend}]", file=sys.stderr)
    return 0


def cmd_unsubscribe(args) -> int:
    db_path = _db_path(args)
    ok = subs_mod.remove_subscription(db_path, args.url)
    if not ok:
        print(f"error: no subscription for {args.url}", file=sys.stderr)
        return 1
    print(f"unsubscribed: {args.url}", file=sys.stderr)
    return 0


def cmd_subscriptions(args) -> int:
    db_path = _db_path(args)
    subs = subs_mod.list_subscriptions(db_path)
    if not subs:
        print("no subscriptions", file=sys.stderr)
        return 0
    for sub in subs:
        label = f" ({sub.label})" if sub.label else ""
        synced = sub.last_synced_at or "never"
        print(f"{sub.url}{label}\t[{sub.backend}]\tlast_synced={synced}\t"
              f"last_rows_added={sub.last_rows_added}")
    return 0


def cmd_sync_subscriptions(args) -> int:
    db_path = _db_path(args)
    results = subs_mod.sync_all(db_path, dry_run=args.dry_run)
    if not results:
        print("no subscriptions to sync", file=sys.stderr)
        return 0
    total_added = 0
    failed = 0
    for r in results:
        if args.dry_run:
            print(f"[dry-run] would sync {r.url} [{r.backend}]", file=sys.stderr)
            continue
        if r.ok:
            total_added += r.rows_added
            print(f"synced {r.url} [{r.backend}]: +{r.rows_added} rows", file=sys.stderr)
        else:
            failed += 1
            print(f"error syncing {r.url} [{r.backend}]: {r.error}", file=sys.stderr)
    if not args.dry_run:
        print(f"sync-subscriptions: {total_added} new rows across "
              f"{len(results)} subscriptions ({failed} failed)", file=sys.stderr)
    return 0
