"""Jellyfin / Kodi ``.strm`` export.

A ``.strm`` file is a one-line text file whose body is a URL. Jellyfin
and Kodi treat them as remote-media stubs: the player follows the URL
and streams the content directly. This is the simplest way to surface a
``media_archivist`` index inside Jellyfin without having Jellyfin
download anything.

Layout, by default::

    <output_dir>/
    ├── youtube/
    │   └── <safe artist or _unknown>/
    │       └── <safe title>.strm
    ├── bandcamp/...
    └── ...

Each ``.strm`` body points at the server's ``/strm/{entry_id}`` endpoint
when ``base_url`` is given; otherwise the entry's resolved
``stream`` field (or the watch URL as fallback) is written directly.

Exports
-------

The app API's exports (``POST /api/v1/exports``, see ``docs/app-api.md``)
write into a library folder Jellyfin watches, in one of
:data:`EXPORT_LAYOUTS`, and every file they write is recorded in the
working index's ``exports`` table so the export can be undone:

``movies``
    ``Title (Year) [videoid]/Title (Year) [videoid].strm`` with
    ``movie.nfo`` and ``poster.<ext>`` beside it.
``episodes``
    ``Channel/Season 2024/Channel - S2024E0503 - Title [videoid].strm``
    with the episode's ``.nfo``, the channel's ``tvshow.nfo`` and the
    year's ``season.nfo``.

The target folder must be inside one of the comma-separated folders in
``MEDIA_ARCHIVIST_LIBRARY_ROOTS`` once symbolic links are resolved; without
that variable nothing is exported. No file is written through a symbolic
link. Exporting an entry again rewrites its files in place. A
``tvshow.nfo`` or ``season.nfo`` that is already there with other content
is left alone, and an episode whose ``.strm`` or ``.nfo`` is already there
with other content that no export wrote is skipped. Undoing an export removes a file only when it still holds
what the export wrote and no other export wrote it too, and removes the
folders the export created once they are empty.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Collection, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from media_archivist import links
from media_archivist._atomic import atomic_write_text
from media_archivist.index import Index
from media_archivist.indexdb import utcnow
from media_archivist.models.canonical import MediaEntry, stable_id
from media_archivist.models.raw import Source

LOG = logging.getLogger("media_archivist.strm")

_SAFE = re.compile(r"[^\w\-. ]+")
_MAX_LEN = 120

#: Layouts available to :func:`export_strm`.
#:
#: ``by-source-artist`` (the default) reproduces the original,
#: pre-``layout`` behaviour: ``<output_dir>/<source>/<artist>/<title>.strm``.
#: It stays the default for back-compat.
LAYOUTS = ("by-source-artist", "flat", "by-source", "by-artist")


def _safe(name: str, default: str = "_unknown") -> str:
    name = (name or "").strip()
    if not name:
        return default
    name = _SAFE.sub("_", name)
    name = name.strip(" ._")
    return name[:_MAX_LEN] or default


def _strm_body(entry: MediaEntry, base_url: Optional[str]) -> str:
    if base_url:
        return f"{base_url.rstrip('/')}/strm/{entry.id}\n"
    return (entry.stream or entry.url) + "\n"


def _target_dir(out_root: Path, entry: MediaEntry, layout: str) -> Path:
    artist = _safe(entry.artist or "")
    if layout == "flat":
        return out_root
    if layout == "by-source":
        return out_root / entry.source.value
    if layout == "by-artist":
        return out_root / artist
    # "by-source-artist" (default / back-compat)
    return out_root / entry.source.value / artist


def _unique_target(directory: Path, basename: str, suffix: str,
                    seen: set) -> Path:
    """Return ``directory/basename.suffix``, disambiguated on collision.

    Two entries with the same sanitized title (e.g. same title from two
    different artists collapsed by a coarser layout) would otherwise
    clobber each other; append a short suffix derived from a counter to
    keep every file. ``seen`` tracks basenames already claimed under
    ``directory`` across the whole export call.
    """
    key = (str(directory), basename.lower())
    candidate = basename
    n = 1
    while (str(directory), candidate.lower()) in seen:
        n += 1
        candidate = f"{basename}-{n}"
    seen.add((str(directory), candidate.lower()))
    return directory / f"{candidate}{suffix}"


def export_strm(db_path: str, output_dir: str, *,
                base_url: Optional[str] = None,
                source: Optional[str] = None,
                where: Optional[str] = None,
                has_stream: Optional[bool] = None,
                limit: int = 0,
                dry_run: bool = False,
                layout: str = "by-source-artist",
                nfo: bool = False) -> int:
    """Walk the canonical view; write one ``.strm`` per matching entry.

    ``layout`` controls the folder structure (see :data:`LAYOUTS`); the
    default, ``by-source-artist``, matches pre-``layout`` behaviour so
    existing callers see unchanged output.

    When ``nfo`` is true, a ``.nfo`` sidecar (see
    :mod:`media_archivist.nfo`) is written next to each ``.strm`` with a
    matching basename, so Jellyfin/Kodi show real titles, artwork and
    tags instead of scraping the filename.

    Returns the number of ``.strm`` files that were (or would be) written.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    nfo_xml = None
    if nfo:
        from media_archivist.nfo import nfo_xml as _nfo_xml
        nfo_xml = _nfo_xml

    idx = Index(db_path)
    written = 0
    seen: set = set()
    for entry in idx.view(source=source, where=where,
                          has_stream=has_stream, limit=limit):
        if not (entry.stream or base_url or entry.url):
            continue
        title = _safe(entry.title or entry.url)
        directory = _target_dir(out_root, entry, layout)
        target = _unique_target(directory, title, ".strm", seen)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_strm_body(entry, base_url))
            if nfo_xml is not None:
                target.with_suffix(".nfo").write_text(nfo_xml(entry))
        written += 1
    return written


ENV_LIBRARY_ROOTS = "MEDIA_ARCHIVIST_LIBRARY_ROOTS"
EXPORT_LAYOUTS = ("movies", "episodes")
EXPORT_SCOPES = ("entry", "playlist", "subscription")
LINK_KINDS = ("plain", "signed")


class ExportRefused(ValueError):
    """The export's target folder is not inside a library root."""


class ExportNotFound(LookupError):
    """The entry, playlist, subscription or export does not exist."""


class _Refused(Exception):
    """One entry cannot be written; the message is the reason."""


def library_roots() -> List[Path]:
    """The folders exports may write into, symbolic links resolved."""
    spec = os.environ.get(ENV_LIBRARY_ROOTS, "")
    return [Path(os.path.realpath(p.strip())) for p in spec.split(",") if p.strip()]


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def confine(target_dir: str, roots: Optional[Sequence[Path]] = None) -> Tuple[Path, Path]:
    """``(real target, its library root)``; raises :class:`ExportRefused`."""
    roots = library_roots() if roots is None else roots
    if not roots:
        raise ExportRefused(f"no library roots are configured; set {ENV_LIBRARY_ROOTS}")
    if not target_dir or not os.path.isabs(target_dir):
        raise ExportRefused("target_dir must be an absolute path")
    real = Path(os.path.realpath(target_dir))
    for root in roots:
        if _under(real, root):
            return real, root
    raise ExportRefused(f"{target_dir} is not inside a library root")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Written:
    """What writing one entry produced."""

    files: Dict[str, str] = field(default_factory=dict)
    dirs: List[str] = field(default_factory=list)
    poster_dir: Optional[Path] = None
    skipped: Optional[str] = None


def _make_dirs(path: Path, base: Path, out: Written) -> None:
    """Create ``path`` below ``base`` one folder at a time, never through a link."""
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise _Refused(f"{current} is a symbolic link")
        try:
            current.mkdir()
            out.dirs.append(str(current))
        except FileExistsError:
            if not current.is_dir():
                raise _Refused(f"{current} is not a folder") from None


def _write(path: Path, text: str, out: Written, *, shared: bool = False) -> None:
    """Write ``text`` to ``path`` and record it.

    A file already holding ``text`` is recorded without being rewritten. A
    ``shared`` file (a show's or season's NFO) with other content is left
    alone and not recorded.
    """
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _Refused(f"{path} is not a regular file")
    data = text.encode("utf-8")
    if path.exists():
        current = path.read_bytes()
        if current != data and shared:
            return
        if current == data:
            out.files[str(path)] = _digest(data)
            return
    atomic_write_text(str(path), text)
    out.files[str(path)] = _digest(data)


def _video_tag(entry: MediaEntry) -> str:
    return _safe(str(entry.raw.get("videoId") or entry.id))


def entry_key(entry: MediaEntry) -> str:
    """``youtube:<videoId>`` for a YouTube entry, ``<source>:<url>`` otherwise."""
    video_id = entry.raw.get("videoId")
    if entry.source is Source.YOUTUBE and isinstance(video_id, str) and video_id:
        return f"youtube:{video_id}"
    return f"{entry.source.value}:{entry.url}"


def _is_own_link(text: str, entry: MediaEntry) -> bool:
    """Whether ``text`` is a ``.strm`` body this server writes for ``entry``.

    One line, a link to ``/api/v1/stream/<entry id>`` on any base, signed or
    not, expiring or not.
    """
    lines = text.strip().splitlines()
    if len(lines) != 1:
        return False
    return re.fullmatch(r"https?://\S*" + re.escape(links.STREAM_PREFIX)
                        + re.escape(quote(entry.id, safe=":")) + r"(\?\S*)?", lines[0]) is not None


def _claim(paths: Iterable[Path], texts: Iterable[str], owned: Collection[str],
           entry: Optional[MediaEntry] = None) -> None:
    """Refuse when one of ``paths`` holds other content and is not in ``owned``.

    A ``.strm`` holding a link this server writes for ``entry`` is its own,
    whichever export or base address wrote it.
    """
    for path, text in zip(paths, texts):
        if path.is_file() and not path.is_symlink() and str(path) not in owned \
                and path.read_bytes() != text.encode("utf-8"):
            if entry is not None and path.suffix == ".strm" \
                    and _is_own_link(path.read_text("utf-8", errors="replace"), entry):
                continue
            raise _Refused(f"{path} exists and no export wrote it")


def write_movie(entry: MediaEntry, target: Path, body: str, owned: Collection[str] = ()) -> Written:
    """File ``entry`` as ``Title (Year) [videoid]/`` with its ``.strm`` and ``movie.nfo``.

    The folder is written only when it is missing, empty or already this
    entry's, so ``owned`` is not consulted.
    """
    from media_archivist import movie_layout

    out = Written()
    if not movie_layout.applies_to(entry):
        out.skipped = "music entries are not exported as movies"
        return out
    name = movie_layout.movie_name(entry, tag=_video_tag(entry), ext=".strm")
    folder = target / name
    try:
        if folder.exists() and not folder.is_symlink() and not movie_layout._is_ours(folder, entry):
            raise _Refused("folder belongs to another entry")
        _make_dirs(folder, target, out)
        strm_file = folder / f"{name}.strm"
        _write(strm_file, body, out)
        _write(folder / "movie.nfo", movie_layout._nfo_with_file(entry, strm_file.name), out)
        out.poster_dir = folder
    except _Refused as e:
        out.skipped = str(e)
    return out


def write_episode(entry: MediaEntry, target: Path, body: str, owned: Collection[str] = ()) -> Written:
    """File ``entry`` as an episode of its channel, the upload year as the season.

    An episode's ``.strm`` or ``.nfo`` already there with other content is
    rewritten only when its path is in ``owned`` (the files exports wrote);
    otherwise the entry is skipped and nothing of it is written.
    """
    from media_archivist import movie_layout, nfo

    out = Written()
    year, premiered = nfo._year_and_premiered(entry.published)
    if not premiered or len(premiered) < 10:
        out.skipped = "upload date unknown"
        return out
    if not entry.artist:
        out.skipped = "channel unknown"
        return out
    channel = _safe(entry.artist)
    month_day = premiered[5:7] + premiered[8:10]
    if not month_day.isdigit():
        out.skipped = "upload date unknown"
        return out
    prefix = f"{channel} - S{year}E{month_day} - "
    tail = f" [{_video_tag(entry)}]"
    room = 255 - len((prefix + tail).encode("utf-8")) - len(".strm")
    title = movie_layout._truncate_bytes(_safe(entry.title or entry.url), room).strip(" ._")
    stem = prefix + (title or _safe("")) + tail
    show = target / channel
    season_dir = show / f"Season {year}"
    strm_file, nfo_file = season_dir / f"{stem}.strm", season_dir / f"{stem}.nfo"
    episode_nfo = nfo.episode_nfo_xml(entry, int(year), int(month_day))
    try:
        _claim((strm_file, nfo_file), (body, episode_nfo), owned, entry)
        _make_dirs(season_dir, target, out)
        _write(show / "tvshow.nfo", nfo.tvshow_nfo_xml(entry.artist, entry.raw.get("channelId")),
               out, shared=True)
        _write(season_dir / "season.nfo", nfo.season_nfo_xml(int(year)), out, shared=True)
        _write(strm_file, body, out)
        _write(nfo_file, episode_nfo, out)
    except _Refused as e:
        out.skipped = str(e)
    return out


WRITERS = {"movies": write_movie, "episodes": write_episode}


def find_entry(db_path: str, index, ident: str, idx: Optional[Index] = None) -> Optional[MediaEntry]:
    """The entry an opaque id or a readable key (``youtube:<videoId>``) names."""
    idx = idx or Index(db_path)
    entry = idx.get(ident)
    if entry is not None:
        return entry
    row = index.execute("SELECT entry_id FROM entries_meta WHERE key = ?", (ident,)).fetchone()
    if row is not None:
        entry = idx.get(row["entry_id"])
        if entry is not None:
            return entry
    if ident.startswith("youtube:"):
        video_id = ident[len("youtube:"):]
        for raw in idx.raw_entries():
            if raw.get("source") == Source.YOUTUBE.value and raw.get("videoId") == video_id \
                    and isinstance(raw.get("url"), str):
                return idx.get(stable_id(Source.YOUTUBE, raw["url"]))
    return None


def scope_entry_ids(index, scope: str, scope_id: str) -> List[str]:
    """The entry ids a playlist or subscription lists, in its order.

    A playlist is any playlist the index knows, from a channel's Playlists
    tab or subscribed to itself. Raises :class:`ExportNotFound` when the
    index knows no such playlist or subscription.
    """
    ids: List[str] = []
    known = False
    if scope == "playlist":
        known = index.execute("SELECT 1 FROM playlists WHERE playlist_id = ?", (scope_id,)).fetchone()
        ids += [r["entry_id"] for r in index.execute(
            "SELECT entry_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (scope_id,))]
    if scope in ("playlist", "subscription"):
        known = known or index.execute("SELECT 1 FROM subscriptions WHERE id = ?", (scope_id,)).fetchone()
        ids += [r["entry_id"] for r in index.execute(
            "SELECT entry_id FROM memberships WHERE subscription_id = ?"
            " ORDER BY position IS NULL, position, first_seen", (scope_id,))]
    if not known and not ids:
        raise ExportNotFound(f"unknown {scope} {scope_id}")
    return list(dict.fromkeys(ids))


def scope_title(index, scope: str, scope_id: str) -> str:
    """The name a playlist's collection takes."""
    row = index.execute("SELECT title FROM playlists WHERE playlist_id = ?", (scope_id,)).fetchone()
    if row and row["title"]:
        return row["title"]
    row = index.execute("SELECT title, label FROM subscriptions WHERE id = ?", (scope_id,)).fetchone()
    if row and (row["title"] or row["label"]):
        return row["title"] or row["label"]
    return scope_id


_JSON_COLUMNS = ("written", "files", "dirs", "entries", "skipped", "posters", "jellyfin")


def _row(row) -> dict:
    rec = dict(row)
    for col in _JSON_COLUMNS:
        rec[col] = json.loads(rec[col])
    return rec


def export_view(rec: dict) -> dict:
    """An export record as the API shows it."""
    posters = rec["posters"]
    return {
        "id": rec["id"], "app": rec["app"], "scope": rec["scope_kind"], "scope_id": rec["scope_id"],
        "target_dir": rec["target_dir"], "layout": rec["layout"], "links": rec["links"],
        "automatic": bool(rec["automatic"]), "written": rec["written"], "skipped": rec["skipped"],
        "posters_pending": posters.get("pending", 0), "posters_failed": posters.get("failed", 0),
        "jellyfin": rec["jellyfin"], "created": rec["created_at"], "updated": rec["updated_at"],
    }


def get_export(index, export_id: str) -> dict:
    row = index.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise ExportNotFound(f"unknown export {export_id}")
    return _row(row)


@dataclass
class ExportJob:
    """What an export leaves for after its response: posters, then Jellyfin."""

    export_id: str
    posters: List[Tuple[MediaEntry, Path]]
    refresh: bool
    collection: Optional[Tuple[str, str, List[str]]]


def run_export(db_path: str, index, *, scope: str, scope_id: str, target_dir: str,
               body_for, layout: str = "movies", links: str = "plain", app: str = "user",
               entry_ids: Optional[Iterable[str]] = None, automatic: bool = False,
               refresh_jellyfin: bool = False, collection: bool = False,
               jellyfin_configured: bool = False) -> Tuple[dict, ExportJob]:
    """Write the ``.strm`` files of a scope and record them; returns the record and its job.

    ``body_for(entry)`` is the text of an entry's ``.strm`` file. Without
    ``entry_ids`` the scope's entries are exported; with them, only those
    (an automatic export of new entries). An automatic export adds to the
    automatic record of its subscription and folder when there is one.
    """
    if layout not in EXPORT_LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    target, root = confine(target_dir)
    idx = Index(db_path)
    if scope == "entry":
        entry = find_entry(db_path, index, scope_id, idx)
        if entry is None:
            raise ExportNotFound(f"unknown entry {scope_id}")
        entries: List[Tuple[str, Optional[MediaEntry]]] = [(entry.id, entry)]
        scope_id = entry_key(entry)
    else:
        ids = list(entry_ids) if entry_ids is not None else scope_entry_ids(index, scope, scope_id)
        entries = [(i, idx.get(i)) for i in ids]

    out = Written()
    try:
        _make_dirs(target, root, out)
    except _Refused as e:
        raise ExportRefused(str(e)) from None
    skipped, exported, exported_keys, posters, video_ids = [], [], set(), [], []
    owned = set()
    for row in index.execute("SELECT files FROM exports"):
        owned.update(json.loads(row["files"]))
    write = WRITERS[layout]
    for entry_id, entry in entries:
        if entry is None:
            skipped.append({"key": entry_id, "reason": "not in the database"})
            continue
        result = write(entry, target, body_for(entry), owned)
        out.files.update(result.files)
        out.dirs += result.dirs
        if result.skipped:
            skipped.append({"key": entry_key(entry), "reason": result.skipped})
            continue
        exported.append(entry.id)
        exported_keys.add(entry_key(entry))
        if entry.raw.get("videoId"):
            video_ids.append(str(entry.raw["videoId"]))
        if result.poster_dir is not None and entry.thumbnail:
            posters.append((entry, result.poster_dir))

    if not refresh_jellyfin:
        refresh = "not requested"
    else:
        refresh = "requested" if jellyfin_configured else "not configured"
    if not collection:
        coll = "not requested"
    elif not jellyfin_configured:
        coll = "not configured"
    elif scope != "playlist":
        coll = "not a playlist export"
    else:
        coll = "pending"

    now = utcnow()
    with index.write() as conn:
        row = None
        if automatic:
            row = conn.execute("SELECT * FROM exports WHERE automatic = 1 AND scope_kind = ?"
                               " AND scope_id = ? AND target_dir = ?",
                               (scope, scope_id, str(target))).fetchone()
        if row is None:
            rec = {"id": f"exp_{secrets.token_hex(8)}", "files": {}, "dirs": [], "entries": [],
                   "skipped": [], "posters": {"pending": 0, "written": 0, "failed": 0}}
            conn.execute("INSERT INTO exports (id, scope_kind, scope_id, target_dir, layout, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?)", (rec["id"], scope, scope_id, str(target), layout, now))
        else:
            rec = _row(row)
        rec["files"].update(out.files)
        rec["dirs"] += [d for d in out.dirs if d not in rec["dirs"]]
        rec["entries"] += [e for e in exported if e not in rec["entries"]]
        rec["skipped"] = [s for s in rec["skipped"] if s["key"] not in exported_keys] + skipped
        rec["posters"]["pending"] = rec["posters"].get("pending", 0) + len(posters)
        conn.execute(
            "UPDATE exports SET app = ?, links = ?, layout = ?, written = ?, files = ?, dirs = ?,"
            " entries = ?, skipped = ?, posters = ?, jellyfin = ?, automatic = ?, updated_at = ?"
            " WHERE id = ?",
            (app, links, layout, json.dumps(list(rec["files"])), json.dumps(rec["files"]),
             json.dumps(rec["dirs"]), json.dumps(rec["entries"]), json.dumps(rec["skipped"]),
             json.dumps(rec["posters"]), json.dumps({"refresh": refresh, "collection": coll}),
             int(automatic), now, rec["id"]))
    job = ExportJob(rec["id"], posters, refresh == "requested",
                    (scope_id, scope_title(index, scope, scope_id), video_ids) if coll == "pending" else None)
    return get_export(index, rec["id"]), job


def _update(index, export_id: str, change) -> None:
    with index.write() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
        if row is None:
            return
        rec = _row(row)
        change(rec)
        conn.execute("UPDATE exports SET written = ?, files = ?, posters = ?, jellyfin = ?,"
                     " updated_at = ? WHERE id = ?",
                     (json.dumps(list(rec["files"])), json.dumps(rec["files"]),
                      json.dumps(rec["posters"]), json.dumps(rec["jellyfin"]), utcnow(), export_id))


def finish_export(index, job: ExportJob) -> None:
    """Fetch the posters, then ask Jellyfin to refresh and make the collection.

    A YouTube entry's poster is one ``thumb`` request of the YouTube client,
    skipped while that kind is paused. Every outcome is recorded on the
    export; nothing here raises.
    """
    from media_archivist import jellyfin, movie_layout, ytclient

    for entry, folder in job.posters:
        path = None
        try:
            if entry.source is Source.YOUTUBE:
                path = ytclient.CLIENT.call("thumb", movie_layout._fetch_poster, entry, folder)
            else:
                path = movie_layout._fetch_poster(entry, folder)
        except ytclient.PausedError:
            pass
        except Exception as e:
            LOG.warning("poster for %s failed: %s", entry.id, e)

        def done(rec, path=path):
            posters = rec["posters"]
            posters["pending"] = max(0, posters.get("pending", 0) - 1)
            if path is None:
                posters["failed"] = posters.get("failed", 0) + 1
            else:
                posters["written"] = posters.get("written", 0) + 1
                rec["files"][str(path)] = _digest(path.read_bytes())

        _update(index, job.export_id, done)

    if not job.refresh and job.collection is None:
        return
    client = jellyfin.Jellyfin.from_env()
    if client is None:
        status = {"refresh": "not configured"} if job.refresh else {}
        if job.collection is not None:
            status["collection"] = "not configured"
        _update(index, job.export_id, lambda rec: rec["jellyfin"].update(status))
        return
    status = {}
    if job.refresh:
        try:
            client.refresh_library()
        except jellyfin.JellyfinError as e:
            status["refresh"] = f"failed: {e}"
    if job.collection is not None:
        playlist_id, name, video_ids = job.collection
        try:
            status["collection"] = jellyfin.sync_collection(index, client, playlist_id, name, video_ids)
        except (jellyfin.JellyfinError, KeyError) as e:
            status["collection"] = f"failed: {e}"
    _update(index, job.export_id, lambda rec: rec["jellyfin"].update(status))


def _removable(path: Path, digest: str, target: Path, others: set) -> Optional[str]:
    """Why ``path`` must stay, or ``None`` when it may be removed."""
    if str(path) in others:
        return "written by another export"
    if not _under(path.parent, target) or os.path.realpath(path.parent) != str(path.parent):
        return "its folder is not where it was written"
    if path.is_symlink() or not path.is_file():
        return "not a regular file"
    if _digest(path.read_bytes()) != digest:
        return "changed since it was written"
    return None


def delete_export(index, export_id: str) -> dict:
    """Remove the files an export wrote and its record; returns what was removed and kept."""
    removed, kept = [], []
    with index.write() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
        if row is None:
            raise ExportNotFound(f"unknown export {export_id}")
        rec = _row(row)
        others = set()
        for other in conn.execute("SELECT files FROM exports WHERE id != ?", (export_id,)):
            others.update(json.loads(other["files"]))
        target = Path(rec["target_dir"])
        handed = {}
        for path, digest in rec["files"].items():
            p = Path(path)
            if not p.exists() and not p.is_symlink():
                continue
            reason = _removable(p, digest, target, set())
            if path in others:
                # another export lists it too: leave it, and when it still holds
                # what this one wrote, make the other the one that owns that
                # content so its own deletion removes the file
                if reason is None:
                    handed[path] = digest
                kept.append({"path": path, "reason": "written by another export"})
                continue
            if reason:
                kept.append({"path": path, "reason": reason})
                continue
            p.unlink()
            removed.append(path)
        left = []
        for d in sorted(rec["dirs"], key=len, reverse=True):
            p = Path(d)
            if p.is_symlink() or os.path.realpath(p) != d:
                continue
            try:
                p.rmdir()
            except OSError:
                if p.is_dir():
                    left.append(d)
        conn.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        if handed or left:
            for other in conn.execute("SELECT id, files, dirs FROM exports").fetchall():
                files, dirs = json.loads(other["files"]), json.loads(other["dirs"])
                changed = {k: v for k, v in handed.items() if k in files and files[k] != v}
                # a folder this export made and others still use goes to the one
                # that holds a file in it, so the last deletion removes it
                adopted = [d for d in left if d not in dirs
                           and any(f.startswith(d + os.sep) for f in files)]
                if changed or adopted:
                    files.update(changed)
                    conn.execute("UPDATE exports SET files = ?, dirs = ?, updated_at = ? WHERE id = ?",
                                 (json.dumps(files), json.dumps(dirs + adopted), utcnow(), other["id"]))
    return {"id": export_id, "removed": removed, "kept": kept}


def find_exports(db_path: str, index, scope: str, scope_id: str, target_dir: str) -> List[str]:
    """The ids of the exports of a scope into a folder."""
    if scope == "entry":
        entry = find_entry(db_path, index, scope_id)
        scope_id = entry_key(entry) if entry is not None else scope_id
    rows = index.execute("SELECT id FROM exports WHERE scope_kind = ? AND scope_id = ? AND target_dir = ?"
                         " ORDER BY created_at", (scope, scope_id, os.path.realpath(target_dir)))
    return [r["id"] for r in rows]
