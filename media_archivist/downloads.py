# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: one queue per app, one worker lane, yt-dlp as a program.

Every download is a row of the ``downloads`` table in the working index
(see :mod:`media_archivist.indexdb`), so the queue and the history outlive
a restart; a download that was running when the server stopped is queued
again on the next start. The lane is separate from the scheduler that runs
index refreshes, so a download never waits behind one.

The lane starts at most ``download_concurrency`` downloads at once (a
setting, 1 by default); each start takes its turn in the YouTube client's
request spacing (:mod:`media_archivist.ytclient`), shared with every other
YouTube request of the process. It takes the apps in turn: after a
download of one app, the next app (by name) that has something queued goes
first, so a long queue from one app does not hold up another. Plays take
no turn, so they never delay a start. While the ``download`` kind of
:mod:`media_archivist.backoff` is paused, nothing starts and queued
records say what they wait for; a download that meets the bot check part
way through goes back to the queue, and fails once it has met it on
:data:`MAX_ATTEMPTS` runs. A run cut short by a stop or a restart is not a
bot check and is not counted against that limit.

yt-dlp runs as a subprocess so a cancel can stop it, into a staging folder
inside the target folder; only a run that exits cleanly and leaves a video
file there is ``completed``, and only then are its files moved into place.
A cancelled, failed or interrupted run leaves nothing behind. A file is
never replaced: each is created at its name or not at all, so one that
appears there at any moment, as one already in the target folder, makes
the download's files all take the first free `` (2)``, `` (3)`` ... after
the video's name. Hashing and placing hold no lock a cancel needs.
The SHA-256 of each placed file is recorded, and forgetting a download with
its files removes only those still holding that content and listed by no
other download.

Target folders are confined to the download roots: the folders listed in
``MEDIA_ARCHIVIST_DOWNLOAD_ROOTS`` (separated by ``os.pathsep``), or the
server's download folder when the variable is unset. A path is compared
after resolving symbolic links, at queue time and again when the download
starts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from media_archivist import streams, ytclient, ytmeta
from media_archivist.backoff import Backoff
from media_archivist.indexdb import utcnow
from media_archivist.settings import Settings

LOG = logging.getLogger("media_archivist.downloads")

ENV_ROOTS = "MEDIA_ARCHIVIST_DOWNLOAD_ROOTS"
KIND = "download"
STATUSES = ("queued", "running", "completed", "failed", "cancelled")
ACTIVE = ("queued", "running")
FINISHED = ("completed", "failed", "cancelled")
MAX_QUEUED_PER_APP = 1000
MAX_ATTEMPTS = 5
MAX_SUBTITLE_LANGS = 20
STAGING_PREFIX = ".archivist-dl-"
TERMINATE_GRACE_S = 5.0
IDLE_POLL_S = 30.0
_PROGRESS_WRITE_S = 1.0
_YOUTUBE_KEY = re.compile(r"^youtube:([0-9A-Za-z_-]{11})$")
_LANG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,19}$")
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp")


class DownloadRequestError(ValueError):
    """The request cannot be queued as given (HTTP 400)."""


class UnknownDownload(KeyError):
    """No download or entry by that id (HTTP 404)."""


class QueueFull(RuntimeError):
    """The app already has :data:`MAX_QUEUED_PER_APP` downloads queued (HTTP 429)."""


class ToolMissing(RuntimeError):
    """yt-dlp is not installed (HTTP 503)."""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def download_roots() -> List[Path]:
    """The folders downloads may be written under, symbolic links resolved."""
    raw = [p for p in os.environ.get(ENV_ROOTS, "").split(os.pathsep) if p.strip()]
    if not raw:
        raw = [str(streams.default_download_dir())]
    return [Path(os.path.realpath(os.path.expanduser(p.strip()))) for p in raw]


def confine(target_dir: Optional[str]) -> Path:
    """``target_dir`` resolved, when it lies inside a download root; else ``ValueError``.

    Without ``target_dir`` the first root is used.
    """
    roots = download_roots()
    if target_dir is None:
        return roots[0]
    if not isinstance(target_dir, str) or not target_dir.strip() or "\x00" in target_dir:
        raise DownloadRequestError("target_dir must be a folder path")
    expanded = os.path.expanduser(target_dir.strip())
    if not os.path.isabs(expanded):
        raise DownloadRequestError("target_dir must be an absolute path")
    path = Path(os.path.realpath(expanded))
    for root in roots:
        if path == root or root in path.parents:
            return path
    raise DownloadRequestError("target_dir must be inside one of the download roots: "
                               + ", ".join(str(r) for r in roots))


def _inside(path: Path, folder: Path) -> bool:
    real = Path(os.path.realpath(path))
    return Path(os.path.realpath(folder)) in real.parents


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _info_file(video: Path, files: List[Path]) -> Path:
    """The info JSON written for ``video``: its own name, else the one file with that suffix."""
    exact = video.with_name(video.stem + ".info.json")
    if exact in files:
        return exact
    return next((p for p in files if p.name.endswith(".info.json")), exact)


def _numbered(files: List[Path], video: Path, n: int) -> List[str]:
    """The names ``files`` take as the ``n``-th choice: their own, then `` (2)``, `` (3)`` ..."""
    stem = video.stem
    names = []
    for path in files:
        name = path.name
        if n > 1:
            if name.startswith(stem):
                name = f"{stem} ({n}){name[len(stem):]}"
            else:
                name = f"{path.stem} ({n}){path.suffix}"
        names.append(name)
    return names


def _link_into_place(src: Path, dest: Path) -> None:
    """Make ``dest`` hold ``src``'s bytes without ever replacing a file.

    ``FileExistsError`` when something is at ``dest``, however it got there.
    """
    try:
        os.link(src, dest)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
            shutil.copyfileobj(f, out)
    except BaseException:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise


def _place(files: List[Path], target: Path, video: Path) -> List[Path]:
    """Move ``files`` into ``target`` under the first free numbering; returns where they went.

    Each file is created at its name with link-or-fail semantics, so a file
    that appears at one of the names at any time is never overwritten: the
    whole set then starts over with the next number.
    """
    for n in range(1, 10_000):
        placed: List[Path] = []
        try:
            for src, name in zip(files, _numbered(files, video, n)):
                dest = target / name
                _link_into_place(src, dest)
                placed.append(dest)
        except FileExistsError:
            for dest in placed:
                dest.unlink(missing_ok=True)
            continue
        except BaseException:
            for dest in placed:
                dest.unlink(missing_ok=True)
            raise
        for src in files:
            src.unlink(missing_ok=True)
        return placed
    raise OSError(f"no free name for {video.name} in {target}")


def _clean_subtitles(subtitles: Optional[Iterable[str]]) -> List[str]:
    if subtitles is None:
        return []
    langs = list(subtitles)
    if len(langs) > MAX_SUBTITLE_LANGS:
        raise DownloadRequestError(f"at most {MAX_SUBTITLE_LANGS} subtitle languages")
    for lang in langs:
        if not isinstance(lang, str) or not _LANG.match(lang):
            raise DownloadRequestError(f"not a subtitle language code: {lang!r}")
    return list(dict.fromkeys(langs))


def _clean_kinds(kinds: Optional[Iterable[str]]) -> Optional[List[str]]:
    """The caption kinds asked for, or ``None`` for the default (manual and automatic together)."""
    if kinds is None:
        return None
    kinds = list(kinds)
    for kind in kinds:
        if kind not in streams.CAPTION_KINDS:
            raise DownloadRequestError(f"not a caption kind: {kind!r}; expected one of "
                                       + ", ".join(streams.CAPTION_KINDS))
    if not kinds:
        raise DownloadRequestError("subtitles_kinds must name at least one kind")
    return list(dict.fromkeys(kinds))


def _clean_format(fmt: Optional[str]) -> Optional[str]:
    if fmt is None:
        return None
    fmt = fmt.strip() if isinstance(fmt, str) else ""
    if not fmt or len(fmt) > 200 or any(c in fmt for c in "\r\n\x00"):
        raise DownloadRequestError("format must be a yt-dlp format selector of 1 to 200 characters")
    return fmt


def _record(row, waiting_for: Optional[Dict[str, str]]) -> Dict[str, Any]:
    progress = row["progress"]
    return {
        "id": row["id"],
        "app": row["app"],
        "entry": row["entry_key"] or row["entry_id"],
        "entry_id": row["entry_id"],
        "title": row["title"],
        "status": row["status"],
        "progress": None if progress is None else int(progress),
        "bytes": row["bytes"],
        "target_dir": row["target_dir"],
        "filename": row["filename_template"],
        "format": row["format"],
        "subtitles": json.loads(row["subtitles"] or "[]"),
        "subtitles_kinds": json.loads(row["subtitles_kinds"] or "null") or streams.DEFAULT_CAPTION_KINDS,
        "keep_info_json": bool(row["keep_info_json"]),
        "files": json.loads(row["files"] or "[]"),
        "info_json": row["info_json"],
        "captions": json.loads(row["captions"] or "[]"),
        "created": row["created_at"],
        "started": row["started_at"],
        "finished": row["finished_at"],
        "error": row["error"],
        "error_kind": row["error_kind"],
        "attempts": row["attempts"],
        "waiting_for": waiting_for if row["status"] == "queued" else None,
    }


class Downloads:
    """The download queue of one working index and the lane that works it."""

    def __init__(self, index, *, backoff: Optional[Backoff] = None,
                 settings: Optional[Settings] = None) -> None:
        self.index = index
        self.db_path = str(index.json_path)
        self.backoff = backoff or Backoff(index)
        self.settings = settings or Settings(index)
        self._cond = threading.Condition()
        self._jobs: Dict[str, threading.Thread] = {}
        self._procs: Dict[str, subprocess.Popen] = {}
        self._cancelled: set = set()
        self._last_app: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = False

    # -- queue -----------------------------------------------------------

    def queue(self, app: str, entry: str, *, target_dir: Optional[str] = None,
              filename: Optional[str] = None, format: Optional[str] = None,
              subtitles: Optional[Iterable[str]] = None,
              subtitles_kinds: Optional[Iterable[str]] = None,
              keep_info_json: bool = False) -> Tuple[Dict[str, Any], bool]:
        """Queue a download; returns ``(record, created)``.

        An identical request (same entry, same folder) still queued or
        running is returned instead of a new one, with ``created`` false.
        """
        try:
            streams.output_template(filename)
        except ValueError as e:
            raise DownloadRequestError(str(e)) from None
        fmt = _clean_format(format)
        langs = _clean_subtitles(subtitles)
        kinds = _clean_kinds(subtitles_kinds)
        target = confine(target_dir)
        if streams.ytdlp_command() is None:
            raise ToolMissing("yt-dlp is not available on this server; downloads are disabled")
        title = None
        with self.index.write() as conn:
            entry_id, key, _ = self._resolve_entry(conn, entry)
            existing = conn.execute(
                "SELECT * FROM downloads WHERE entry_id = ? AND target_dir = ?"
                " AND status IN ('queued', 'running') ORDER BY seq LIMIT 1",
                (entry_id, str(target))).fetchone()
            if existing is not None:
                return _record(existing, self._waiting_for()), False
            queued = conn.execute("SELECT COUNT(*) FROM downloads WHERE app = ? AND status = 'queued'",
                                  (app,)).fetchone()[0]
            if queued >= MAX_QUEUED_PER_APP:
                raise QueueFull(f"{app} already has {queued} downloads queued")
            title = self._title(entry_id)
            now = utcnow()
            download_id = "dl_" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO downloads (id, seq, app, entry_id, entry_key, title, target_dir,"
                " filename_template, format, subtitles, subtitles_kinds, keep_info_json, status,"
                " created_at, updated_at)"
                " VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM downloads), ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (download_id, app, entry_id, key, title, str(target), filename, fmt,
                 json.dumps(langs), None if kinds is None else json.dumps(kinds),
                 int(bool(keep_info_json)), now, now))
            row = conn.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
        with self._cond:
            self._cond.notify_all()
        return _record(row, self._waiting_for()), True

    def _resolve_entry(self, conn, entry: str) -> Tuple[str, str, str]:
        """``(entry_id, key, url)`` of an entry id or key; a YouTube key is indexed first."""
        entry = (entry or "").strip()
        row = conn.execute("SELECT entry_id, key, url FROM entries_meta WHERE entry_id = ? OR key = ?",
                           (entry, entry)).fetchone()
        if row is not None:
            return row["entry_id"], row["key"], row["url"]
        from media_archivist.index import Index
        from media_archivist.models.canonical import stable_id
        from media_archivist.models.raw import Source

        m = _YOUTUBE_KEY.match(entry)
        if m:
            url = f"https://www.youtube.com/watch?v={m[1]}"
            entry_id, key = stable_id(Source.YOUTUBE, url), entry
        else:
            known = Index(self.db_path).get(entry) if Path(self.db_path).is_file() else None
            if known is None:
                raise UnknownDownload(f"unknown entry {entry!r}")
            video_id = (known.raw or {}).get("videoId")
            url = known.url
            entry_id = known.id
            key = (f"youtube:{video_id}" if known.source is Source.YOUTUBE and isinstance(video_id, str)
                   else f"{known.source.value}:{url}")
        conn.execute("INSERT OR IGNORE INTO entries_meta (entry_id, key, url, first_seen)"
                     " VALUES (?, ?, ?, ?)", (entry_id, key, url, utcnow()))
        return entry_id, key, url

    def _title(self, entry_id: str) -> Optional[str]:
        if not Path(self.db_path).is_file():
            return None
        from media_archivist.index import Index

        try:
            entry = Index(self.db_path).get(entry_id)
        except Exception:
            LOG.exception("cannot read the title of %s", entry_id)
            return None
        return entry.title if entry is not None else None

    # -- reading ---------------------------------------------------------

    def _waiting_for(self) -> Optional[Dict[str, str]]:
        until = self.backoff.blocked_until(KIND)
        return {"reason": "bot check", "until": _iso(until)} if until else None

    def get(self, download_id: str) -> Dict[str, Any]:
        row = self.index.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
        if row is None:
            raise UnknownDownload(f"unknown download {download_id!r}")
        return _record(row, self._waiting_for())

    def list(self, *, app: Optional[str] = None, statuses: Optional[Iterable[str]] = None,
             limit: int = 100, cursor: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Newest first; ``app=None`` is every app. Returns ``(records, next_cursor)``."""
        where, params = [], []
        if app is not None:
            where.append("app = ?")
            params.append(app)
        if statuses:
            statuses = list(statuses)
            unknown = [s for s in statuses if s not in STATUSES]
            if unknown:
                raise DownloadRequestError(f"unknown status {unknown[0]!r}; expected one of "
                                           + ", ".join(STATUSES))
            where.append(f"status IN ({', '.join('?' * len(statuses))})")
            params += statuses
        if cursor is not None:
            try:
                where.append("seq < ?")
                params.append(int(cursor))
            except ValueError:
                raise DownloadRequestError("cursor is not one this server gave") from None
        sql = "SELECT * FROM downloads"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.index.execute(sql + " ORDER BY seq DESC LIMIT ?", (*params, limit + 1)).fetchall()
        waiting = self._waiting_for()
        records = [_record(r, waiting) for r in rows[:limit]]
        next_cursor = str(rows[limit - 1]["seq"]) if len(rows) > limit else None
        return records, next_cursor

    # -- cancel and forget -----------------------------------------------

    def cancel(self, download_id: str, *, delete_files: bool = False) -> Dict[str, Any]:
        """Cancel a queued or running download, or remove a finished one from the history.

        Removing a finished one with ``delete_files`` deletes the files it
        placed, and only those still inside its target folder, still holding
        what was downloaded and listed by no other download; the rest are
        reported in ``kept_files`` with the reason.
        """
        with self._cond:
            with self.index.write() as conn:
                row = conn.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
                if row is None:
                    raise UnknownDownload(f"unknown download {download_id!r}")
                if row["status"] in ACTIVE:
                    now = utcnow()
                    conn.execute("UPDATE downloads SET status = 'cancelled', finished_at = ?,"
                                 " updated_at = ? WHERE id = ?", (now, now, download_id))
                    row = conn.execute("SELECT * FROM downloads WHERE id = ?",
                                       (download_id,)).fetchone()
                    removed = False
                else:
                    conn.execute("DELETE FROM downloads WHERE id = ?", (download_id,))
                    removed = True
                    others = {r["value"] for r in conn.execute(
                        "SELECT j.value FROM downloads, json_each(downloads.files) AS j")}
            if not removed:
                if download_id in self._jobs:
                    self._cancelled.add(download_id)
                proc = self._procs.get(download_id)
                if proc is not None:
                    _terminate(proc)
                return _record(row, None)
        deleted, kept = [], []
        if delete_files:
            folder = Path(row["target_dir"])
            digests = json.loads(row["file_digests"] or "{}")
            for name in json.loads(row["files"] or "[]"):
                path = Path(name)
                if path.is_symlink():
                    reason = "a symbolic link, not the downloaded file"
                elif not path.exists():
                    reason = "no longer there"
                elif not path.is_file():
                    reason = "no longer a regular file"
                elif not _inside(path, folder):
                    reason = "outside the download's folder"
                elif name in others:
                    reason = "listed by another download"
                elif name not in digests:
                    reason = "no record of what was downloaded"
                elif _sha256(path) != digests[name]:
                    reason = "changed since it was downloaded"
                else:
                    path.unlink()
                    deleted.append(name)
                    continue
                kept.append({"path": name, "reason": reason})
        return {"id": download_id, "removed": True, "deleted_files": deleted, "kept_files": kept}

    # -- the lane --------------------------------------------------------

    def start(self) -> None:
        """Start the lane (once); downloads interrupted by a stop are queued again."""
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._recover()
            self._thread = threading.Thread(target=self._loop, name="downloads", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the lane; running downloads are stopped and queued again."""
        with self._cond:
            self._stopping = True
            for proc in list(self._procs.values()):
                _terminate(proc)
            self._cond.notify_all()
            jobs = list(self._jobs.values())
            thread = self._thread
        deadline = time.monotonic() + timeout
        for t in jobs + ([thread] if thread else []):
            t.join(max(0.0, deadline - time.monotonic()))

    def wake(self) -> None:
        """Look at the queue now, for after a pause was ended by hand."""
        with self._cond:
            self._cond.notify_all()

    def idle(self) -> bool:
        """No download running and none that could start now."""
        with self._cond:
            if self._jobs:
                return False
        if self.backoff.blocked_until(KIND):
            return True
        row = self.index.execute("SELECT 1 FROM downloads WHERE status = 'queued' LIMIT 1").fetchone()
        return row is None

    def _recover(self) -> None:
        with self.index.write() as conn:
            rows = [r for r in conn.execute("SELECT id, target_dir FROM downloads"
                                            " WHERE status = 'running'").fetchall()
                    if r["id"] not in self._jobs]
            now = utcnow()
            conn.executemany("UPDATE downloads SET status = 'queued', progress = NULL, bytes = NULL,"
                             " started_at = NULL, updated_at = ? WHERE id = ?",
                             [(now, r["id"]) for r in rows])
        for row in rows:
            shutil.rmtree(Path(row["target_dir"]) / (STAGING_PREFIX + row["id"]), ignore_errors=True)

    def _loop(self) -> None:
        with self._cond:
            while not self._stopping:
                try:
                    delay = self._start_next()
                except Exception:
                    LOG.exception("download lane: cannot start the next download")
                    delay = IDLE_POLL_S
                if delay != 0:
                    self._cond.wait(IDLE_POLL_S if delay is None else min(delay, IDLE_POLL_S))

    def _start_next(self) -> Optional[float]:
        """Start one download if one may start; returns how long to wait (0: try again)."""
        if len(self._jobs) >= int(self.settings.get("download_concurrency")):
            return None
        remaining = self.backoff.remaining(KIND)
        if remaining > 0:
            return remaining
        if self.index.execute("SELECT 1 FROM downloads WHERE status = 'queued' LIMIT 1").fetchone() is None:
            return None
        delay = ytclient.CLIENT.gate.try_start()
        if delay > 0:
            return delay
        row = self._claim_next()
        if row is None:
            return None
        self._last_app = row["app"]
        self._cancelled.discard(row["id"])
        job = threading.Thread(target=self._run, args=(row,), name=f"download-{row['id']}",
                               daemon=True)
        self._jobs[row["id"]] = job
        job.start()
        return 0

    def _claim_next(self):
        with self.index.write() as conn:
            firsts = conn.execute("SELECT app, MIN(seq) AS first FROM downloads"
                                  " WHERE status = 'queued' GROUP BY app ORDER BY first").fetchall()
            if not firsts:
                return None
            apps = sorted(r["app"] for r in firsts)
            if self._last_app is None:
                app = firsts[0]["app"]
            else:
                app = next((a for a in apps if a > self._last_app), apps[0])
            row = conn.execute("SELECT * FROM downloads WHERE app = ? AND status = 'queued'"
                               " ORDER BY seq LIMIT 1", (app,)).fetchone()
            now = utcnow()
            conn.execute("UPDATE downloads SET status = 'running', started_at = ?, finished_at = NULL,"
                         " attempts = attempts + 1, progress = 0, bytes = NULL, error = NULL,"
                         " error_kind = NULL, updated_at = ? WHERE id = ?", (now, now, row["id"]))
            return conn.execute("SELECT * FROM downloads WHERE id = ?", (row["id"],)).fetchone()

    def _run(self, row) -> None:
        download_id = row["id"]
        try:
            self._download(row)
        except Exception as e:
            LOG.exception("download %s failed", download_id)
            self._finish(download_id, status="failed", error=str(e) or type(e).__name__,
                         error_kind="tool")
        finally:
            with self._cond:
                self._jobs.pop(download_id, None)
                self._procs.pop(download_id, None)
                self._cond.notify_all()

    def _download(self, row) -> None:
        download_id = row["id"]
        try:
            target = confine(row["target_dir"])
        except DownloadRequestError as e:
            self._finish(download_id, status="failed", error=str(e), error_kind="permanent")
            return
        command = streams.ytdlp_command()
        if command is None:
            self._finish(download_id, status="failed", error_kind="tool",
                         error="yt-dlp is not available on this server")
            return
        url = self.index.execute("SELECT url FROM entries_meta WHERE entry_id = ?",
                                 (row["entry_id"],)).fetchone()
        url = url["url"] if url is not None else None
        if not url:
            self._finish(download_id, status="failed", error_kind="permanent",
                         error=f"entry {row['entry_id']} has no address")
            return
        staging = target / (STAGING_PREFIX + download_id)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        argv = command + streams.download_argv(
            url, str(staging), filename=row["filename_template"], format=row["format"],
            subtitles=json.loads(row["subtitles"] or "[]"),
            subtitles_kinds=json.loads(row["subtitles_kinds"] or "null"),
            info_json=bool(row["keep_info_json"]),
            ip_family=self.settings.get("youtube_ip_family"))
        try:
            returncode, done, tail = self._execute(download_id, argv)
            with self._cond:
                if download_id in self._cancelled:
                    self._cancelled.discard(download_id)
                    return
                if self._stopping and returncode != 0:
                    self._requeue(download_id)
                    return
            self._settle(row, target, staging, returncode, done, tail)
            with self._cond:
                self._cancelled.discard(download_id)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _execute(self, download_id: str, argv: List[str]):
        with self._cond:
            if download_id in self._cancelled or self._stopping:
                return None, None, []
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace",
                                    start_new_session=True)
            self._procs[download_id] = proc
        done = None
        tail: deque = deque(maxlen=40)
        last_write = 0.0
        shown = (-1, None)
        assert proc.stdout is not None
        for line in proc.stdout:
            parsed = streams.parse_download_line(line)
            if parsed is None:
                if line.strip():
                    tail.append(line.rstrip())
                continue
            if "done" in parsed:
                done = parsed["done"]
                continue
            progress = parsed["progress"]
            percent = progress["percent"]
            now = time.monotonic()
            value = (int(percent) if percent is not None else shown[0], progress["downloaded"])
            if value != shown and now - last_write >= _PROGRESS_WRITE_S:
                self._progress(download_id, *value)
                shown, last_write = value, now
        return proc.wait(), done, list(tail)

    def _progress(self, download_id: str, percent: int, downloaded: Optional[float]) -> None:
        with self.index.write() as conn:
            conn.execute("UPDATE downloads SET progress = MAX(COALESCE(progress, 0), ?),"
                         " bytes = COALESCE(?, bytes), updated_at = ?"
                         " WHERE id = ? AND status = 'running'",
                         (max(0, min(99, percent)), None if downloaded is None else int(downloaded),
                          utcnow(), download_id))

    def _settle(self, row, target: Path, staging: Path, returncode, done, tail) -> None:
        download_id = row["id"]
        if returncode != 0:
            text = "\n".join(tail) or f"yt-dlp exited with status {returncode}"
            kind = ytmeta.failure_kind(RuntimeError(text))
            if kind == "bot":
                wait = self.backoff.mark_blocked(KIND)
                LOG.warning("download %s met the bot check; downloads pause for %ds",
                            download_id, int(wait))
                met = self._count_bot_check(download_id)
                if met >= MAX_ATTEMPTS:
                    self._finish(download_id, status="failed", error_kind="bot",
                                 error=f"met the bot check on {met} runs")
                else:
                    self._requeue(download_id)
                return
            errors = [ln for ln in tail if ln.startswith("ERROR")]
            self._finish(download_id, status="failed", error_kind=kind,
                         error=(errors[-1] if errors else text.splitlines()[-1])[:2000])
            return
        video = self._video_file(staging, done)
        if video is None:
            self._finish(download_id, status="failed", error_kind="tool",
                         error="yt-dlp finished without producing a video file")
            return
        files = [video] + sorted(p for p in staging.iterdir()
                                 if p != video and p.is_file() and not p.name.endswith(_PARTIAL_SUFFIXES))
        info_file = _info_file(video, files)
        files, captions = self._captions(row, video, files)
        digests_of = {path: _sha256(path) for path in files}
        placed_paths = _place(files, target, video)
        placed = [str(p) for p in placed_paths]
        digests = {str(dest): digests_of[src] for src, dest in zip(files, placed_paths)}
        moved = {src: str(dest) for src, dest in zip(files, placed_paths)}
        info_json = moved.get(info_file)
        captions = [{**c, "file": moved[c["file"]]} for c in captions]
        self.backoff.mark_clear(KIND)
        title = done.get("title") if isinstance(done.get("title"), str) else None
        size = sum(p.stat().st_size for p in placed_paths)
        if not self._finish(download_id, status="completed", files=placed, digests=digests,
                            title=title, size=size, info_json=info_json, captions=captions):
            for dest in placed_paths:
                dest.unlink(missing_ok=True)

    @staticmethod
    def _captions(row, video: Path, files: List[Path]):
        """``(files to place, captions)``: caption files described, the unwanted ones left out.

        The info JSON yt-dlp wrote beside the video says what each caption
        file is. It is placed only when the request asked to keep it. When
        the request named caption kinds, a file the info JSON cannot account
        for as one of them is not placed; with the default, every file is
        placed and one it cannot account for is described with a null kind.
        """
        info_file = _info_file(video, files)
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if not isinstance(info, dict):
            info = {}
        asked = json.loads(row["subtitles_kinds"] or "null")
        kinds = asked or streams.DEFAULT_CAPTION_KINDS
        prefix = video.stem + "."
        names = {}
        for path in files[1:]:
            ext = path.suffix.lstrip(".").lower()
            if ext in streams.SUBTITLE_EXTS and path.name.startswith(prefix):
                names[path] = path.name[len(prefix):-len(path.suffix)]
        tracks = streams.classify_captions(
            info, names.values(), manual="manual" in kinds,
            automatic="automatic" in kinds or "automatic_original" in kinds)
        described: Dict[Path, Optional[Dict[str, Any]]] = {}
        for path, track in names.items():
            meta = tracks.get(track)
            if meta is not None:
                described[path] = {**meta, "track": track}
            elif not asked:
                described[path] = {"kind": None, "language": track, "is_translation": None,
                                   "track": track}
            else:
                described[path] = None
        dropped = {p for p, m in described.items() if m is None}
        if asked and "automatic" not in kinds:
            # only the recogniser's original track; where YouTube lists it twice, the "-orig" one
            best: Dict[Tuple[str, str], Path] = {}
            for path, meta in described.items():
                if meta is None:
                    continue
                if meta["kind"] == "automatic" and meta["is_translation"] is not False:
                    dropped.add(path)
                    continue
                key = (meta["language"], meta["kind"])
                if key not in best or meta["track"].endswith("-orig"):
                    best[key] = path
            dropped |= {p for p, m in described.items()
                        if m and p not in dropped and best[(m["language"], m["kind"])] != p}
        if info_file in files and not row["keep_info_json"]:
            dropped.add(info_file)
        kept = [p for p in files if p not in dropped]
        captions = [{"file": p, "language": m["language"], "kind": m["kind"],
                     "is_translation": m["is_translation"]}
                    for p, m in described.items() if m and p not in dropped]
        return kept, captions

    @staticmethod
    def _video_file(staging: Path, done) -> Optional[Path]:
        if not isinstance(done, dict) or not isinstance(done.get("filepath"), str):
            return None
        path = Path(done["filepath"])
        if not path.is_absolute():
            path = staging / path
        if path.parent.resolve() != staging.resolve() or not path.is_file() or path.is_symlink():
            return None
        if path.name.endswith(_PARTIAL_SUFFIXES) or path.suffix.lstrip(".").lower() in streams.SUBTITLE_EXTS:
            return None
        if path.stat().st_size == 0:
            return None
        return path

    def _count_bot_check(self, download_id: str) -> int:
        """Count one bot check against a download; returns how many it has met."""
        with self.index.write() as conn:
            conn.execute("UPDATE downloads SET bot_attempts = bot_attempts + 1 WHERE id = ?",
                         (download_id,))
            return conn.execute("SELECT bot_attempts FROM downloads WHERE id = ?",
                                (download_id,)).fetchone()[0]

    def _requeue(self, download_id: str) -> None:
        with self.index.write() as conn:
            conn.execute("UPDATE downloads SET status = 'queued', progress = NULL, bytes = NULL,"
                         " started_at = NULL, updated_at = ? WHERE id = ? AND status = 'running'",
                         (utcnow(), download_id))

    def _finish(self, download_id: str, *, status: str, error: Optional[str] = None,
                error_kind: Optional[str] = None, files: Optional[List[str]] = None,
                digests: Optional[Dict[str, str]] = None, title: Optional[str] = None,
                size: Optional[int] = None, info_json: Optional[str] = None,
                captions: Optional[List[Dict[str, Any]]] = None) -> bool:
        """Settle a running download; false when it was no longer running (cancelled)."""
        now = utcnow()
        with self.index.write() as conn:
            cur = conn.execute(
                "UPDATE downloads SET status = ?, error = ?, error_kind = ?, files = ?,"
                " file_digests = ?, result_path = ?, info_json = ?, captions = ?, title = COALESCE(?, title), bytes = COALESCE(?, bytes),"
                " finished_at = ?, updated_at = ?,"
                " progress = CASE WHEN ? = 'completed' THEN 100 ELSE progress END"
                " WHERE id = ? AND status = 'running'",
                (status, error, error_kind, json.dumps(files or []), json.dumps(digests or {}),
                 (files or [None])[0], info_json, json.dumps(captions or []), title,
                 size, now, now, status, download_id))
            return cur.rowcount > 0


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a yt-dlp run and the programs it started (its own session)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    def _reap():
        try:
            proc.wait(TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    threading.Thread(target=_reap, name=f"reap-{proc.pid}", daemon=True).start()
