"""Canonicalization / dedupe CLI command handlers."""
from __future__ import annotations

import sys

from media_archivist import cli_args as _cli_args
from media_archivist.commands._helpers import _index_for, _validated_args


def cmd_providers(args) -> int:
    """List built-in providers and which are active."""
    import json
    from media_archivist.providers import all_providers
    rows = []
    for name, p in sorted(all_providers().items()):
        rows.append({
            "name": name,
            "active": p.is_available(),
            "media": sorted(m.value for m in p.media),
        })
    json.dump(rows, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_canonicalize(args) -> int:
    """Run providers across the DB and update canonical / quarantine / entities sidecars."""
    _validated_args(_cli_args.CanonicalizeArgs, args)
    from media_archivist.canonicalize import canonicalize

    db_path = args.db_file or _index_for(args).path
    providers = args.providers or None
    canonical, quarantine, entities = canonicalize(
        db_path,
        providers=providers,
        stamp_rows=not args.no_stamp,
    )
    print(
        f"canonical: {len(canonical.records)} records, "
        f"quarantine: {len(quarantine.entries)} entries, "
        f"entities: {len(entities.entities)}",
        file=sys.stderr,
    )
    return 0


def cmd_link(args) -> int:
    """Compute fingerprint groups and write the ``<db>.links.json`` sidecar."""
    _validated_args(_cli_args.LinkArgs, args)
    from media_archivist.dedupe import link as canon_link

    db_path = args.db_file or _index_for(args).path
    links = canon_link(db_path, duration_tolerance=args.duration_tolerance)
    print(f"linked {sum(len(v) for v in links.values())} entries across "
          f"{len(links)} fingerprint groups", file=sys.stderr)
    return 0


def cmd_dedupe(args) -> int:
    """Read view+links and emit a deduped canonical JSONL."""
    _validated_args(_cli_args.DedupeArgs, args)
    from media_archivist.dedupe import dedupe, write_dedupe_jsonl

    db_path = args.db_file or _index_for(args).path
    preference = [s.strip() for s in args.prefer.split(",") if s.strip()]
    deduped = dedupe(db_path, preference=preference,
                     duration_tolerance=args.duration_tolerance)
    n = write_dedupe_jsonl(deduped, args.output)
    print(f"wrote {n} canonical rows to {args.output}", file=sys.stderr)
    return 0
