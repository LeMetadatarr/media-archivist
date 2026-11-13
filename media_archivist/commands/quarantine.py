"""Quarantine sidecar CLI command handlers."""
from __future__ import annotations

import json
import sys

from media_archivist import cli_args as _cli_args
from media_archivist.commands._helpers import _index_for, _validated_args


def cmd_quarantine_list(args) -> int:
    from media_archivist.canonicalize import load_quarantine
    db_path = args.db_file or _index_for(args).path
    sidecar = load_quarantine(db_path)
    json.dump(sidecar.model_dump(mode="json"), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_quarantine_resolve(args) -> int:
    _validated_args(_cli_args.QuarantineResolveArgs, args)
    from media_archivist.canonicalize import quarantine_resolve
    db_path = args.db_file or _index_for(args).path
    ok = quarantine_resolve(db_path, args.row_id, args.canonical_id)
    if not ok:
        print(f"row_id {args.row_id} not in quarantine", file=sys.stderr)
        return 1
    print(f"resolved {args.row_id}", file=sys.stderr)
    return 0


def cmd_quarantine_reject(args) -> int:
    _validated_args(_cli_args.QuarantineRejectArgs, args)
    from media_archivist.canonicalize import quarantine_reject
    db_path = args.db_file or _index_for(args).path
    ok = quarantine_reject(db_path, args.row_id)
    if not ok:
        print(f"row_id {args.row_id} not in quarantine", file=sys.stderr)
        return 1
    print(f"rejected {args.row_id} → new canonical_id allocated", file=sys.stderr)
    return 0
