"""Remote-facing CLI command handlers (serve, discover, sync, enrich, ...)."""
from __future__ import annotations

import json
import sys

from media_archivist import cli_args as _cli_args
from media_archivist.commands._helpers import _index_for, _validated_args


def cmd_serve(args) -> int:
    """Run the HTTP server bound to the DB."""
    _validated_args(_cli_args.ServeArgs, args)
    from media_archivist.server import run

    db_path = args.db_file or _index_for(args).path
    run(db_path, host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_discover(args) -> int:
    """Run a content-type-filtered YouTube search and archive results."""
    _validated_args(_cli_args.DiscoverArgs, args)
    from media_archivist.discover import discover

    db_path = args.db_file or _index_for(args).path
    n = discover(db_path, kind=args.kind, query=args.query,
                 max_results=args.max_results,
                 required_kwords=args.require or [],
                 blacklisted_kwords=args.blacklist or [],
                 min_duration=args.min_duration)
    print(f"discovered {n} entries", file=sys.stderr)
    return 0


def cmd_sync(args) -> int:
    """Refresh the DB incrementally — currently RSS-based for YouTube channels."""
    _validated_args(_cli_args.SyncArgs, args)
    from media_archivist.sync import rss_sync

    db_path = args.db_file or _index_for(args).path
    added = rss_sync(db_path,
                     max_per_channel=args.max_per_channel,
                     required_kwords=args.require or [],
                     blacklisted_kwords=args.blacklist or [],
                     min_duration=args.min_duration)
    total = sum(added.values())
    print(f"sync: {total} new rows across {len(added)} channels", file=sys.stderr)
    return 0


def cmd_enrich(args) -> int:
    """Run lyrics / transcripts / content_type enrichers across the DB."""
    _validated_args(_cli_args.EnrichArgs, args)
    from media_archivist.enrich import EnrichKind, enrich

    db_path = args.db_file or _index_for(args).path
    kinds = [EnrichKind(k) for k in args.kinds]
    languages = [s.strip() for s in args.languages.split(",") if s.strip()]
    processed, modified = enrich(db_path, kinds, limit=args.limit,
                                 overwrite=args.overwrite, languages=languages)
    print(f"enriched {modified}/{processed} rows", file=sys.stderr)
    return 0


def cmd_snapshot(args) -> int:
    from media_archivist.snapshot import snapshot
    db_path = args.db_file or _index_for(args).path
    out = snapshot(db_path, label=args.label or "")
    print(out)
    return 0


def cmd_diff(args) -> int:
    _validated_args(_cli_args.DiffArgs, args)
    from media_archivist.snapshot import diff
    result = diff(args.a, args.b)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_hub_publish(args) -> int:
    _validated_args(_cli_args.HubPublishArgs, args)
    from media_archivist.hub import publish
    db_path = args.db_file or _index_for(args).path
    url = publish(db_path, repo=args.repo, jsonl_path=args.jsonl_path,
                  description=args.description, license_id=args.license_id,
                  private=args.private)
    print(url)
    return 0
