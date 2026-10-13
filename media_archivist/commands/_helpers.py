"""Shared helpers used by the media-archivist CLI command modules."""
from __future__ import annotations

from typing import Iterable, List, Optional

from media_archivist.ia import IAArchivist
from media_archivist.music import YoutubeMusicArchivist
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
        raise SystemExit(f"error: {msg}") from None


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
        raise SystemExit(f"error: --where: {e}") from None
