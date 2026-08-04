"""``media-archivist tag-library`` — local media-folder tagger CLI handler."""
from __future__ import annotations

import sys

from pydantic import ValidationError

from media_archivist import cli_args as _cli_args


def cmd_tag_library(args) -> int:
    """Scan a folder, resolve each file via metadatarr, write .nfo sidecars.

    NON-DESTRUCTIVE: only ever writes ``<file-stem>.nfo`` next to the media
    file. The media file itself is never opened for writing.
    """
    try:
        validated = _cli_args.TagLibraryArgs(
            path=args.path,
            media=args.media,
            nfo=args.nfo,
            dry_run=args.dry_run,
            index=args.index,
            min_confidence=args.min_confidence,
        )
    except ValidationError as e:
        msg = "; ".join(err.get("msg", "invalid") for err in e.errors())
        raise SystemExit(f"error: {msg}") from None
    from media_archivist.library import tag_library

    if validated.dry_run:
        print("DRY RUN — no files will be written", file=sys.stderr)

    results = tag_library(
        validated.path,
        media=validated.media,
        write_nfo=validated.nfo,
        dry_run=validated.dry_run,
        index_db=validated.index,
        min_confidence=validated.min_confidence,
    )

    n_scanned = len(results)
    n_matched = sum(1 for r in results if r.matched)
    n_wrote = sum(1 for r in results if r.action == "wrote")
    n_would = sum(1 for r in results if r.action == "would-write")
    n_skipped = sum(1 for r in results if r.action == "skipped")
    n_error = sum(1 for r in results if r.action == "error")

    for r in results:
        ids = ",".join(f"{k}={v}" for k, v in (r.external_ids or {}).items())
        print(f"[{r.action}] {r.path}"
              + (f" — {ids}" if ids else "")
              + (f" ({r.note})" if r.note else ""))

    print(
        f"scanned {n_scanned}, matched {n_matched}, "
        f"nfo written {n_wrote}, would-write {n_would}, "
        f"skipped {n_skipped}, errors {n_error}",
        file=sys.stderr,
    )
    return 0
