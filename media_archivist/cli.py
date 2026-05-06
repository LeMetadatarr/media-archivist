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
        extra = {
            "bandcamp": "py_bandcamp",
            "soundcloud": "nuvem_de_som",
        }.get(backend)
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


def _index_for(args):
    """Open an :class:`Index` from the args' DB target."""
    from media_archivist.index import Index

    db_path = args.db_file
    if not db_path and args.db:
        # Resolve XDG location.
        from json_database import xdg_data_home
        db_path = f"{xdg_data_home()}/media_archivist/{args.db}.json"
    return Index(db_path)


def _resolve_view(args, *, defaults_grep: bool = True):
    """Apply --where / --canonical / source / has-stream / explicit / grep."""
    from media_archivist.index import WhereError

    idx = _index_for(args)
    try:
        return list(idx.view(
            where=getattr(args, "where", None),
            source=getattr(args, "source_filter", None),
            has_stream=getattr(args, "has_stream", None),
            explicit=getattr(args, "explicit_filter", None),
            grep=getattr(args, "grep", None) if defaults_grep else None,
            limit=getattr(args, "limit", 0) or 0,
        ))
    except WhereError as e:
        raise SystemExit(f"error: --where: {e}")


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
    )
    print(f"{'would write' if args.dry_run else 'wrote'} {n} .strm files",
          file=sys.stderr)
    return 0


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


def cmd_providers(args) -> int:
    """List built-in providers and which are active."""
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


def cmd_entities_list(args) -> int:
    """Dump the entity sidecar as JSON (optionally filtered by --kind)."""
    from media_archivist.entities import load_entities

    db_path = args.db_file or _index_for(args).path
    sidecar = load_entities(db_path)
    rows = list(sidecar.entities.values())
    if args.kind:
        rows = [r for r in rows if r.kind.value == args.kind]
    if args.limit:
        rows = rows[: args.limit]
    json.dump([r.model_dump(mode="json") for r in rows], sys.stdout,
              indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_entities_show(args) -> int:
    """Show one entity by id, plus the works it participates in."""
    from media_archivist.canonicalize import load_canonical
    from media_archivist.entities import load_entities

    db_path = args.db_file or _index_for(args).path
    sidecar = load_entities(db_path)
    entity = sidecar.entities.get(args.entity_id)
    if entity is None:
        print(f"entity {args.entity_id} not found", file=sys.stderr)
        return 1
    canonical = load_canonical(db_path)
    works = [canonical.records[wid] for wid in entity.works
             if wid in canonical.records]
    out = {
        "entity": entity.model_dump(mode="json"),
        "works": [{"canonical_id": w.canonical_id,
                   "title": w.signals.title,
                   "members": w.members} for w in works],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_entities_stats(args) -> int:
    from media_archivist.entities import load_entities

    db_path = args.db_file or _index_for(args).path
    sidecar = load_entities(db_path)
    by_kind: dict[str, int] = {}
    works_per_kind: dict[str, int] = {}
    for rec in sidecar.entities.values():
        by_kind[rec.kind.value] = by_kind.get(rec.kind.value, 0) + 1
        works_per_kind[rec.kind.value] = (
            works_per_kind.get(rec.kind.value, 0) + len(rec.works)
        )
    json.dump({"total": len(sidecar.entities),
               "by_kind": by_kind,
               "works_per_kind": works_per_kind},
              sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


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


def cmd_link(args) -> int:
    """Compute fingerprint groups and write the ``<db>.links.json`` sidecar."""
    _validated_args(_cli_args.LinkArgs, args)
    from media_archivist.canon import link as canon_link

    db_path = args.db_file or _index_for(args).path
    links = canon_link(db_path, duration_tolerance=args.duration_tolerance)
    print(f"linked {sum(len(v) for v in links.values())} entries across "
          f"{len(links)} fingerprint groups", file=sys.stderr)
    return 0


def cmd_dedupe(args) -> int:
    """Read view+links and emit a deduped canonical JSONL."""
    _validated_args(_cli_args.DedupeArgs, args)
    from media_archivist.canon import dedupe, write_dedupe_jsonl

    db_path = args.db_file or _index_for(args).path
    preference = [s.strip() for s in args.prefer.split(",") if s.strip()]
    deduped = dedupe(db_path, preference=preference,
                     duration_tolerance=args.duration_tolerance)
    n = write_dedupe_jsonl(deduped, args.output)
    print(f"wrote {n} canonical rows to {args.output}", file=sys.stderr)
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

    def _add_view_flags(p):
        p.add_argument("--canonical", action="store_true",
                       help="use the canonical MediaEntry view")
        p.add_argument("--where", metavar="EXPR",
                       help="filter expression (e.g. 'duration>180 and source==\"bandcamp\"')")
        p.add_argument("--source", dest="source_filter", metavar="NAME",
                       help="keep only entries from this source")
        p.add_argument("--has-stream", dest="has_stream", action="store_true",
                       default=None, help="keep only entries with a resolved stream URL")
        p.add_argument("--no-stream", dest="has_stream", action="store_false",
                       help="keep only entries without a stream URL")

    p_urls = sub.add_parser("urls", parents=[common],
                            help="print stored URLs (pipe to `yt-dlp -a -`)")
    p_urls.add_argument("--grep", help="filter by substring in title")
    p_urls.add_argument("--limit", type=int, default=0)
    _add_view_flags(p_urls)
    p_urls.set_defaults(func=cmd_urls)

    p_list = sub.add_parser("list", parents=[common], help="list entries (title<TAB>url)")
    p_list.add_argument("--grep", help="filter by substring in title")
    p_list.add_argument("--limit", type=int, default=0)
    p_list.add_argument("--json", dest="json_out", action="store_true",
                        help="emit JSON array")
    _add_view_flags(p_list)
    p_list.add_argument("--explicit", dest="explicit_filter", action="store_true",
                        default=None, help="(canonical view) keep only explicit-flagged tracks")
    p_list.add_argument("--no-explicit", dest="explicit_filter", action="store_false",
                        help="(canonical view) drop explicit-flagged tracks")
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
    p_export.add_argument("--split", metavar="train:0.8,val:0.1,test:0.1",
                          help="deterministic split by fingerprint hash; emits multiple files")
    p_export.add_argument("--split-by", metavar="FIELD",
                          help="split output one file per distinct value of FIELD (e.g. source)")
    _add_view_flags(p_export)
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

    p_strm = sub.add_parser("strm-export", parents=[common],
                            help="write one .strm per entry for Jellyfin / Kodi remote media")
    p_strm.add_argument("--output-dir", dest="output_dir", required=True,
                        help="directory to mount as a Jellyfin library")
    p_strm.add_argument("--base-url", dest="base_url",
                        help="point .strm files at <base_url>/strm/<id> (a "
                             "running media-archivist server); without this, the "
                             ".strm body is the resolved stream / watch URL directly")
    p_strm.add_argument("--source", dest="source_filter")
    p_strm.add_argument("--where")
    p_strm.add_argument("--has-stream", dest="has_stream", action="store_true",
                        default=None)
    p_strm.add_argument("--no-stream", dest="has_stream", action="store_false")
    p_strm.add_argument("--limit", type=int, default=0)
    p_strm.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_strm.set_defaults(func=cmd_strm_export)

    p_serve = sub.add_parser("serve", parents=[common],
                             help="run the HTTP server (FastAPI) over the DB")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true",
                         help="enable uvicorn auto-reload (development)")
    p_serve.set_defaults(func=cmd_serve)

    p_discover = sub.add_parser("discover", parents=[common],
                                help="content-type-filtered YouTube discovery (movies, documentaries, podcasts, …)")
    from media_archivist.discover import supported_kinds
    p_discover.add_argument("--kind", choices=list(supported_kinds()), required=True)
    p_discover.add_argument("--query", required=True)
    p_discover.add_argument("--max-results", dest="max_results", type=int, default=50)
    p_discover.set_defaults(func=cmd_discover)

    p_sync = sub.add_parser("sync", parents=[common],
                            help="incremental refresh; --rss reads YouTube channel RSS feeds")
    p_sync.add_argument("--rss", action="store_true",
                        help="pull each channel's RSS feed, archive entries newer than the latest stored")
    p_sync.add_argument("--max-per-channel", dest="max_per_channel", type=int, default=0,
                        help="cap rows added per channel per run (0 = no cap)")
    p_sync.set_defaults(func=cmd_sync)

    p_enrich = sub.add_parser("enrich", parents=[common],
                              help="add lyrics / transcripts / content_type to rows under _meta.enriched")
    p_enrich.add_argument("--lyrics", dest="kinds", action="append_const",
                          const="lyrics", help="fetch Bandcamp track lyrics")
    p_enrich.add_argument("--transcripts", dest="kinds", action="append_const",
                          const="transcripts",
                          help="fetch YouTube auto-subs via yt-dlp")
    p_enrich.add_argument("--content-type", dest="kinds", action="append_const",
                          const="content_type",
                          help="classify YouTube rows via tutubo.content_type")
    p_enrich.add_argument("--limit", type=int, default=0)
    p_enrich.add_argument("--overwrite", action="store_true",
                          help="re-run enrichment even if a block already exists")
    p_enrich.add_argument("--languages", default="en",
                          help="comma-separated transcript language preference (default: en)")
    p_enrich.set_defaults(func=cmd_enrich, kinds=[])

    p_snap = sub.add_parser("snapshot", parents=[common],
                            help="copy the DB to .snapshots/<timestamp>.json")
    p_snap.add_argument("--label", help="optional suffix on the snapshot filename")
    p_snap.set_defaults(func=cmd_snapshot)

    p_diff = sub.add_parser("diff",
                            help="compare two DB snapshots; print added/removed/changed URLs")
    p_diff.add_argument("a", help="path to the older DB")
    p_diff.add_argument("b", help="path to the newer DB")
    p_diff.set_defaults(func=cmd_diff)

    p_hub = sub.add_parser("hub-publish", parents=[common],
                           help="push a JSONL export + auto-generated dataset card to HuggingFace Hub")
    p_hub.add_argument("--repo", required=True,
                       help="HF repo id (e.g. user/dataset-name)")
    p_hub.add_argument("--jsonl", dest="jsonl_path", required=True,
                       help="path to the JSONL export to upload")
    p_hub.add_argument("--description", default="")
    p_hub.add_argument("--license-id", dest="license_id", default="other")
    p_hub.add_argument("--private", action="store_true")
    p_hub.set_defaults(func=cmd_hub_publish)

    p_providers = sub.add_parser("providers",
                                 help="list built-in metadata providers and their active status")
    p_providers.set_defaults(func=cmd_providers)

    p_canon = sub.add_parser("canonicalize", parents=[common],
                             help="run providers, update canonical/quarantine sidecars")
    p_canon.add_argument("--providers", action="append", default=[], metavar="NAME",
                         help="restrict to this provider (repeatable); default: all active")
    p_canon.add_argument("--no-stamp", action="store_true",
                         help="don't write _meta.canonical_id back to rows")
    p_canon.set_defaults(func=cmd_canonicalize)

    p_qlist = sub.add_parser("quarantine-list", parents=[common],
                             help="dump the quarantine sidecar as JSON")
    p_qlist.set_defaults(func=cmd_quarantine_list)

    p_qres = sub.add_parser("quarantine-resolve", parents=[common],
                            help="accept a quarantined row")
    p_qres.add_argument("--row-id", required=True, dest="row_id")
    p_qres.add_argument("--canonical-id", dest="canonical_id",
                        help="link to this existing canonical_id; default: allocate new from proposed signals")
    p_qres.set_defaults(func=cmd_quarantine_resolve)

    p_qrej = sub.add_parser("quarantine-reject", parents=[common],
                            help="reject a proposal; force a fresh canonical_id")
    p_qrej.add_argument("--row-id", required=True, dest="row_id")
    p_qrej.set_defaults(func=cmd_quarantine_reject)

    p_elist = sub.add_parser("entities-list", parents=[common],
                             help="dump the entity sidecar (optionally filter by --kind)")
    p_elist.add_argument("--kind", help="filter by entity kind (artist / director / album / ...)")
    p_elist.add_argument("--limit", type=int, default=0)
    p_elist.set_defaults(func=cmd_entities_list)

    p_eshow = sub.add_parser("entities-show", parents=[common],
                             help="show a single entity plus the works it appears in")
    p_eshow.add_argument("--entity-id", dest="entity_id", required=True)
    p_eshow.set_defaults(func=cmd_entities_show)

    p_estats = sub.add_parser("entities-stats", parents=[common],
                              help="counts per entity kind plus total works each kind appears in")
    p_estats.set_defaults(func=cmd_entities_stats)

    p_link = sub.add_parser("link", parents=[common],
                            help="fingerprint cross-source matches into <db>.links.json")
    p_link.add_argument("--duration-tolerance", type=float, default=2.0,
                        help="seconds of duration mismatch tolerated within a group")
    p_link.set_defaults(func=cmd_link)

    p_dedupe = sub.add_parser("dedupe", parents=[common],
                              help="emit a deduped canonical JSONL using fingerprint links")
    p_dedupe.add_argument("--output", "-o", metavar="PATH", required=True,
                          help="output JSONL path")
    p_dedupe.add_argument("--prefer", metavar="A,B,C",
                          default="bandcamp,internet_archive,youtube_music,soundcloud,youtube",
                          help="comma-separated source preference order (winners first)")
    p_dedupe.add_argument("--duration-tolerance", type=float, default=2.0)
    p_dedupe.set_defaults(func=cmd_dedupe)

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
