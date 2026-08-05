"""Command-line interface for media_archivist.

Designed for dataset-creation workflows. Pairs naturally with ``yt-dlp``:
this tool indexes stream metadata into a JSON database; download on demand
by piping URLs to ``yt-dlp -a -``.

Two ways to point at a database:

- ``--db-file ./my_dataset.json`` — explicit path (recommended for datasets
  you commit alongside scripts).
- ``--db NAME`` — auto-placed under XDG at
  ``~/.local/share/media_archivist/<NAME>.json``.

``--db-file``/``--db`` are per-subcommand flags — pass them *after* the
subcommand name.

Examples::

    media-archivist add https://www.youtube.com/@SomeChannel --db-file talks.json
    media-archivist urls --db-file talks.json | yt-dlp -a -
    media-archivist export --db-file talks.json --format jsonl > talks.jsonl
    media-archivist export --db-file talks.json --format csv \\
        --fields videoId,title,url,published > talks.csv
"""
from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from media_archivist.version import __version__

from media_archivist.commands.canonical import (
    cmd_canonicalize,
    cmd_dedupe,
    cmd_link,
    cmd_providers,
)
from media_archivist.commands.entities import (
    cmd_entities_list,
    cmd_entities_show,
    cmd_entities_stats,
)
from media_archivist.commands.health import cmd_health
from media_archivist.commands.entries import (
    cmd_add,
    cmd_bootstrap,
    cmd_dump,
    cmd_export,
    cmd_import,
    cmd_list,
    cmd_merge,
    cmd_monitor,
    cmd_prune,
    cmd_stats,
    cmd_strm_export,
    cmd_urls,
)
from media_archivist.commands.quarantine import (
    cmd_quarantine_list,
    cmd_quarantine_reject,
    cmd_quarantine_resolve,
)
from media_archivist.commands.remote import (
    cmd_diff,
    cmd_discover,
    cmd_enrich,
    cmd_hub_publish,
    cmd_serve,
    cmd_snapshot,
    cmd_sync,
)
from media_archivist.commands.streams import (
    cmd_download,
    cmd_resolve,
)
from media_archivist.commands.subscriptions import (
    cmd_subscribe,
    cmd_subscriptions,
    cmd_sync_subscriptions,
    cmd_unsubscribe,
)
from media_archivist.commands.collections import (
    cmd_collection_add,
    cmd_collection_export,
    cmd_collection_remove,
    cmd_collections,
)


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
    p_strm.add_argument("--layout", choices=["by-source-artist", "flat",
                                             "by-source", "by-artist"],
                        default="by-source-artist",
                        help="folder layout under --output-dir; "
                             "by-source-artist (default) matches classic "
                             "output, flat puts every .strm in one "
                             "directory, by-source/by-artist group "
                             "accordingly (default: %(default)s)")
    p_strm.add_argument("--nfo", action="store_true",
                        help="write a Kodi/Jellyfin .nfo sidecar next to "
                             "each .strm with real title/artist/tags/"
                             "artwork so Jellyfin doesn't have to guess "
                             "from the filename")
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

    p_resolve = sub.add_parser("resolve",
                               help="resolve a fresh direct stream URL for a "
                                    "URL or stored entry id")
    p_resolve.add_argument("url_or_id", help="http(s) URL, or a stored entry id")
    p_resolve.add_argument("--db", metavar="NAME",
                           help="DB name, used when url_or_id is an entry id")
    p_resolve.add_argument("--db-file", metavar="PATH",
                           help="explicit DB path, used when url_or_id is an entry id")
    p_resolve.add_argument("--format", default="best",
                           help="format selector: best/bestaudio/worst (default: best)")
    p_resolve.add_argument("--json", dest="json_out", action="store_true",
                           help="print the full ResolvedStream as JSON")
    p_resolve.set_defaults(func=cmd_resolve)

    p_download = sub.add_parser("download",
                                help="download matching entries (or a single "
                                     "--url) to a local directory")
    p_download.add_argument("--db", metavar="NAME")
    p_download.add_argument("--db-file", metavar="PATH")
    p_download.add_argument("--url", help="download this single URL directly")
    p_download.add_argument("--id", help="download this single stored entry id")
    p_download.add_argument("--where", metavar="EXPR",
                            help="filter expression, e.g. 'duration>180'")
    p_download.add_argument("--source", dest="source_filter", metavar="NAME",
                            help="keep only entries from this source")
    p_download.add_argument("--output-dir", dest="output_dir", required=True,
                            help="directory downloads are written into")
    p_download.add_argument("--format", default="best",
                            help="format selector passed to yt-dlp (default: best)")
    p_download.set_defaults(func=cmd_download)

    p_health = sub.add_parser("health", parents=[common],
                              help="probe stored stream URLs; flag dead/expired "
                                   "entries and optionally re-resolve them")
    p_health.add_argument("--where", metavar="EXPR")
    p_health.add_argument("--source", dest="source_filter", metavar="NAME",
                          help="keep only entries from this source")
    p_health.add_argument("--limit", type=int, default=0,
                          help="cap the number of entries probed (0 = no cap)")
    p_health.add_argument("--reresolve", action="store_true",
                          help="attempt to refresh dead/expired stream URLs via "
                               "yt-dlp / native resolvers")
    p_health.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="with --reresolve or --remove-gone, preview "
                               "changes without writing them to the DB")
    p_health.add_argument("--remove-gone", dest="remove_gone", action="store_true",
                          help="drop entries whose source is confirmed deleted/"
                               "unavailable (status=gone) from the DB — "
                               "destructive, explicit opt-in only")
    p_health.set_defaults(func=cmd_health)

    p_mon = sub.add_parser("monitor", parents=[common],
                           help="background-poll one-off YouTube URLs given on the "
                                "command line (not the subscriptions store — for "
                                "that, periodic re-syncing of everything you've "
                                "`subscribe`d to, use `sync-subscriptions --interval`)")
    p_mon.add_argument("urls", nargs="+")
    p_mon.add_argument("--interval", type=int, default=120,
                       help="seconds between syncs (default: 120)")
    p_mon.set_defaults(func=cmd_monitor)

    p_sub = sub.add_parser("subscribe",
                           help="remember a channel/playlist/collection URL "
                                "so sync-subscriptions keeps auto-indexing "
                                "new uploads")
    p_sub.add_argument("url")
    p_sub.add_argument("--db", metavar="NAME")
    p_sub.add_argument("--db-file", metavar="PATH")
    p_sub.add_argument("--backend",
                       choices=["youtube", "ia", "music", "bandcamp", "soundcloud"],
                       help="explicit backend; inferred from the url when omitted")
    p_sub.add_argument("--label", help="optional human-readable label")
    p_sub.add_argument("--download", action="store_true",
                       help="flag this subscription to always download "
                            "newly-indexed items on sync (auto_download=true)")
    p_sub.set_defaults(func=cmd_subscribe)

    p_unsub = sub.add_parser("unsubscribe", help="remove a subscription by URL")
    p_unsub.add_argument("url")
    p_unsub.add_argument("--db", metavar="NAME")
    p_unsub.add_argument("--db-file", metavar="PATH")
    p_unsub.set_defaults(func=cmd_unsubscribe)

    p_subs = sub.add_parser("subscriptions", help="list stored subscriptions")
    p_subs.add_argument("--db", metavar="NAME")
    p_subs.add_argument("--db-file", metavar="PATH")
    p_subs.set_defaults(func=cmd_subscriptions)

    p_syncsub = sub.add_parser("sync-subscriptions",
                               help="archive() every subscribed channel/playlist; "
                                    "the archivist dedupes so only new uploads are added. "
                                    "With --interval, runs forever as a periodic scan "
                                    "(foreground daemon — Ctrl-C to stop; run it under "
                                    "systemd/docker for unattended operation)")
    p_syncsub.add_argument("--db", metavar="NAME")
    p_syncsub.add_argument("--db-file", metavar="PATH")
    p_syncsub.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="report what would sync without archiving anything")
    p_syncsub.add_argument("--interval", type=int, metavar="SECONDS",
                           help="repeat the sync every SECONDS, forever, until "
                                "stopped (Ctrl-C); omit for a single-shot run")
    p_syncsub.add_argument("--download", action="store_true",
                           help="download newly-indexed items this run, on top "
                                "of any subscription's own auto_download flag")
    p_syncsub.set_defaults(func=cmd_sync_subscriptions)

    p_ca = sub.add_parser("collection-add", help="save a named filter as a collection "
                                                  "(updates it in place if the name exists)")
    p_ca.add_argument("name")
    p_ca.add_argument("--db", metavar="NAME")
    p_ca.add_argument("--db-file", metavar="PATH")
    p_ca.add_argument("--where", metavar="EXPR")
    p_ca.add_argument("--source", dest="source_filter", metavar="NAME")
    p_ca.add_argument("--grep", help="filter by substring in title")
    p_ca.add_argument("--has-stream", dest="has_stream", action="store_true", default=None)
    p_ca.add_argument("--no-stream", dest="has_stream", action="store_false")
    p_ca.add_argument("--explicit", dest="explicit_filter", action="store_true", default=None)
    p_ca.add_argument("--no-explicit", dest="explicit_filter", action="store_false")
    p_ca.add_argument("--description", help="optional human-readable description")
    p_ca.set_defaults(func=cmd_collection_add)

    p_cr = sub.add_parser("collection-remove", help="remove a saved collection by name")
    p_cr.add_argument("name")
    p_cr.add_argument("--db", metavar="NAME")
    p_cr.add_argument("--db-file", metavar="PATH")
    p_cr.set_defaults(func=cmd_collection_remove)

    p_ce = sub.add_parser("collection-export", help="materialize a collection: .strm files "
                                                     "(and optionally an .m3u playlist)")
    p_ce.add_argument("name")
    p_ce.add_argument("--db", metavar="NAME")
    p_ce.add_argument("--db-file", metavar="PATH")
    p_ce.add_argument("--output-dir", dest="output_dir", required=True)
    p_ce.add_argument("--base-url", dest="base_url",
                      help="point .strm files at <base_url>/strm/<id>")
    p_ce.add_argument("--m3u", action="store_true",
                      help="also write <output-dir>/<name>.m3u")
    p_ce.add_argument("--no-strm", dest="no_strm", action="store_true",
                      help="skip .strm export (use with --m3u for playlist-only)")
    p_ce.add_argument("--layout", choices=["by-source-artist", "flat",
                                           "by-source", "by-artist"],
                      default="by-source-artist")
    p_ce.add_argument("--nfo", action="store_true")
    p_ce.set_defaults(func=cmd_collection_export)

    p_colls = sub.add_parser("collections", help="list saved collections with match counts")
    p_colls.add_argument("--db", metavar="NAME")
    p_colls.add_argument("--db-file", metavar="PATH")
    p_colls.set_defaults(func=cmd_collections)

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
