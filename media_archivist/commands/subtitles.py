"""CLI handler for ``subtitles`` — fetch .srt/.vtt sidecar files.

Thin wrapper over :mod:`media_archivist.subtitles`, which builds on the
existing yt-dlp transcript machinery in
:mod:`media_archivist.enrich.transcripts`.
"""
from __future__ import annotations

import sys

from media_archivist import cli_args as _cli_args
from media_archivist.commands._helpers import _index_for, _validated_args
from media_archivist.index import WhereError
from media_archivist.subtitles import fetch_library_subtitles


def cmd_subtitles(args) -> int:
    validated = _validated_args(_cli_args.SubtitlesArgs, args)

    db_path = args.db_file or _index_for(args).path
    langs = [lang.strip() for lang in validated.langs.split(",") if lang.strip()]

    try:
        results = fetch_library_subtitles(
            db_path, validated.output_dir,
            source=validated.source_filter,
            where=validated.where,
            langs=langs,
            auto=validated.auto,
            sub_format=validated.sub_format,
            dry_run=validated.dry_run,
            layout=validated.layout,
            limit=validated.limit,
            max_workers=validated.max_workers,
        )
    except WhereError as e:
        raise SystemExit(f"error: --where: {e}") from None

    counts = {"written": 0, "none": 0, "skipped": 0, "error": 0, "dry-run": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status == "error":
            print(f"  error: {r.entry_id}: {r.error}", file=sys.stderr)

    verb = "would fetch" if validated.dry_run else "fetched"
    print(
        f"{verb} subtitles for {len(results)} entries: "
        f"written={counts.get('written', 0) + counts.get('dry-run', 0)} "
        f"none={counts['none']} skipped={counts['skipped']} error={counts['error']}",
        file=sys.stderr,
    )
    return 0 if counts["error"] == 0 else 1
