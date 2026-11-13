# SPDX-License-Identifier: Apache-2.0
"""CLI handlers for saved collections (smart playlists).

Complements the ad-hoc ``--where``/``--source`` filters accepted by
``list``/``export``/``strm-export``: a collection remembers a curator's
filter under a name (``<db>.collections.json``) so it can be browsed,
re-exported, and subscribed to (via the server's ``/collections/{name}/m3u``
endpoint) without retyping the filter every time.
"""
from __future__ import annotations

import sys

from media_archivist import collections as coll_mod
from media_archivist.commands._helpers import _index_for
from media_archivist.index import WhereError


def _db_path(args) -> str:
    return args.db_file or _index_for(args).path


def cmd_collection_add(args) -> int:
    db_path = _db_path(args)
    try:
        coll = coll_mod.add_collection(
            db_path, args.name, where=args.where, source=args.source_filter,
            grep=args.grep, has_stream=args.has_stream, explicit=args.explicit_filter,
            description=args.description,
        )
    except (ValueError, WhereError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"collection saved: {coll.name}", file=sys.stderr)
    return 0


def cmd_collection_remove(args) -> int:
    db_path = _db_path(args)
    ok = coll_mod.remove_collection(db_path, args.name)
    if not ok:
        print(f"error: no collection named {args.name!r}", file=sys.stderr)
        return 1
    print(f"removed collection: {args.name}", file=sys.stderr)
    return 0


def cmd_collections(args) -> int:
    db_path = _db_path(args)
    colls = coll_mod.list_collections(db_path)
    if not colls:
        print("no collections", file=sys.stderr)
        return 0
    for c in colls:
        try:
            n = coll_mod.collection_count(db_path, c)
        except WhereError as e:
            n = f"error: {e}"
        desc = f" — {c.description}" if c.description else ""
        print(f"{c.name}{desc}\tmatches={n}")
    return 0


def cmd_collection_export(args) -> int:
    db_path = _db_path(args)
    coll = coll_mod.get_collection(db_path, args.name)
    if coll is None:
        print(f"error: no collection named {args.name!r}", file=sys.stderr)
        return 1
    try:
        result = coll_mod.export_collection(
            db_path, coll, args.output_dir, base_url=args.base_url,
            m3u=args.m3u, strm=not args.no_strm, layout=args.layout,
            nfo=args.nfo,
        )
    except WhereError as e:
        print(f"error: --where: {e}", file=sys.stderr)
        return 1
    print(f"wrote {result['strm_written']} .strm files", file=sys.stderr)
    if result["m3u_path"]:
        print(f"wrote m3u playlist: {result['m3u_path']}", file=sys.stderr)
    return 0
