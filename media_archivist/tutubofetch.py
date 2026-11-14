# SPDX-License-Identifier: Apache-2.0
"""Downloads for datasets: tutubo's fetch program, its files checked, hashed and placed.

A download with ``tool`` set to ``tutubo`` runs ``python -m tutubo.fetch``
(or the program named by ``MEDIA_ARCHIVIST_TUTUBO_FETCH``) for one YouTube
video. The program writes, under the folder it is given::

    audio/<id>.<ext>                      the audio track as YouTube stores it, the original one where marked
    captions/<id>.<lang>.<kind>.<format>  each caption track of the kinds asked, per format
    meta/<id>.json                        the video's details and what was fetched

It runs in a staging folder named after the video inside the target folder,
which outlives a stop or a bot check so a partial audio file resumes. Every
file the program reports is checked to be a regular file inside the staging
folder with a name of that layout, then hashed and placed under the same
names in the target folder. A file already at a name is kept when it holds
the same bytes and replaced only when this server placed it (it is listed
in the video's ``meta/<id>.files.json``) and it still holds what was placed;
anything else is a conflict and nothing is written. ``meta/<id>.files.json``
is written last: the request, whether the fetch is complete, what is
missing, and each file with its size and SHA-256; it keeps listing the files
an earlier fetch with other options placed there while they stay unchanged.
Until the download is recorded as completed, every change can be undone: a
cancel or an error while the files are placed leaves the target as it was,
and a journal, sealed with a key the server keeps in its index, lets the
next fetch of the video finish or undo a placement a crash interrupted.

tutubo keeps its own bot-check pause in a file every tutubo program of the
user shares (``tutubo.backoff``). The lane reads it before a start, the
program is never told to ignore it, and its exit status 75 means YouTube
asked for a check and the run stopped.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENV_COMMAND = "MEDIA_ARCHIVIST_TUTUBO_FETCH"
EXIT_PAUSED = 75
CAPTION_KINDS = ("manual", "asr")
DEFAULT_CAPTION_KINDS = ["manual", "asr"]
DEFAULT_CAPTION_FORMATS = ["json3", "vtt"]
CAPTION_FORMATS = ("json3", "vtt", "srv1", "srv2", "srv3", "ttml")
PAUSE_KINDS = ("download", "details")
STAGING_PREFIX = ".archivist-fetch-"
RECORD_SUFFIX = ".files.json"
JOURNAL_SUFFIX = ".archivist-journal.json"
SET_ASIDE = ".archivist-old-"
FAMILIES = {"ipv4": "ipv4", "ipv6": "ipv6", "auto": "any"}
_VIDEO_ID = re.compile(r"[0-9A-Za-z_-]{11}")
_NAME = re.compile(r"[0-9A-Za-z_.-]+")
_LAYOUT = ("audio", "captions", "meta")


class ToolOutputError(RuntimeError):
    """The program reported files this server will not place."""


class Conflict(RuntimeError):
    """A file other than one this server placed is in the way."""


def command() -> Optional[List[str]]:
    """How to run the fetch program, or ``None`` when it cannot run here.

    ``MEDIA_ARCHIVIST_TUTUBO_FETCH`` names a program to run instead of
    ``python -m tutubo.fetch``, such as one in a separate environment with a
    fresher yt-dlp. The default needs yt-dlp beside this interpreter or on
    ``PATH``, where tutubo looks for it.
    """
    given = os.environ.get(ENV_COMMAND, "").strip()
    if given:
        return [given] if os.path.isfile(given) and os.access(given, os.X_OK) else None
    if importlib.util.find_spec("tutubo.fetch") is None:
        return None
    beside = Path(sys.executable).with_name("yt-dlp")
    if not beside.is_file() and shutil.which("yt-dlp") is None:
        return None
    return [sys.executable, "-m", "tutubo.fetch"]


def request(audio: bool, captions: bool, kinds: Iterable[str], formats: Iterable[str]) -> Dict[str, Any]:
    """What is asked of the program, in the shape its ``meta/<id>.json`` records it."""
    return {"audio": bool(audio), "captions": bool(captions), "kinds": list(kinds),
            "formats": list(formats)}


def clean_names(values: Optional[Iterable[str]], allowed: Tuple[str, ...], field: str,
                default: List[str]) -> List[str]:
    """``values`` without repeats, each one of ``allowed``; ``ValueError`` otherwise."""
    if values is None:
        return list(default)
    values = list(values)
    if not values:
        raise ValueError(f"{field} must name at least one of " + ", ".join(allowed))
    for value in values:
        if value not in allowed:
            raise ValueError(f"not one of the {field}: {value!r}; expected one of " + ", ".join(allowed))
    return list(dict.fromkeys(values))


def argv(video_id: str, staging: Path, req: Dict[str, Any], *, gap: float, family: str) -> List[str]:
    """The program's arguments for one video; the id goes after ``--``, so one starting with ``-`` is an id."""
    out = ["--dest", str(staging), "--kinds", ",".join(req["kinds"]),
           "--formats", ",".join(req["formats"]), "--gap", f"{float(gap):g}",
           "--family", FAMILIES.get(family, "ipv4")]
    if not req["audio"]:
        out.append("--no-audio")
    if not req["captions"]:
        out.append("--no-captions")
    return out + ["--", video_id]


def child_env() -> Dict[str, str]:
    """The program's environment: this one, without the switch that turns tutubo's pause off."""
    env = dict(os.environ)
    env.pop("TUTUBO_BACKOFF", None)
    return env


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """One line of the program's output: ``{"progress": …}``, ``{"event": …}`` or ``None``."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
        return None
    if event["event"] == "progress":
        done, total = event.get("bytes"), event.get("total")
        if not isinstance(done, int) or isinstance(done, bool) or done < 0:
            return None
        percent = min(100.0, done * 100 / total) if isinstance(total, int) and total > 0 else None
        return {"progress": {"downloaded": float(done), "percent": percent}}
    return {"event": event}


def shared_pause() -> Tuple[Optional[float], Optional[str]]:
    """``(until, reason)`` of tutubo's shared pause over :data:`PAUSE_KINDS`, or ``(None, None)``.

    A shared file that exists and cannot be read may hide a pause, so it
    counts as one for a short while, with the reason saying so.
    """
    from tutubo import backoff

    shared = backoff.shared()
    try:
        until = max((shared.blocked_until(kind) or 0.0) for kind in PAUSE_KINDS)
    except backoff.StateUnreadable as e:
        return time.time() + 30.0, str(e)
    return (until, "bot check") if until else (None, None)


def shared_state() -> Dict[str, Any]:
    """tutubo's shared pause of every kind, or the reason it cannot be read."""
    from tutubo import backoff

    try:
        return {"path": str(backoff.shared().path), "kinds": backoff.shared().state()}
    except backoff.StateUnreadable as e:
        return {"path": str(backoff.shared().path), "error": str(e)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, folder: Path) -> bool:
    return Path(os.path.realpath(folder)) in Path(os.path.realpath(path)).parents


def _layout_name(rel: str, video_id: str) -> bool:
    parts = rel.split("/")
    return (len(parts) == 2 and parts[0] in _LAYOUT and bool(_NAME.fullmatch(parts[1]))
            and parts[1].startswith(video_id + ".") and not parts[1].endswith(RECORD_SUFFIX))


def _regular(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def read_meta(staging: Path, video_id: str, req: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The program's record of ``video_id`` and the files it names, checked.

    Each file is ``{"path", "role", ...}`` with ``path`` relative to the
    staging folder, ``meta/<id>.json`` last. Raises :class:`ToolOutputError`
    for a record that is not this video's or this request's, or a file that
    is missing, not a regular file, outside the staging folder or not named
    after the layout.
    """
    meta_rel = f"meta/{video_id}.json"
    meta_path = staging / meta_rel
    if not _regular(meta_path) or not _inside(meta_path, staging):
        raise ToolOutputError(f"the program left no record at {meta_rel}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolOutputError(f"the program's record is not JSON: {e}") from None
    if not isinstance(meta, dict) or meta.get("video_id") != video_id:
        raise ToolOutputError("the program's record is not this video's")
    if meta.get("request") != req:
        raise ToolOutputError("the program's record is not for this request")
    files: List[Dict[str, Any]] = []
    for caption in meta.get("captions") or ():
        if isinstance(caption, dict) and caption.get("file"):
            files.append({"path": caption["file"], "role": "caption",
                          "language": caption.get("language"), "kind": caption.get("kind"),
                          "format": caption.get("format"), "reported_bytes": caption.get("bytes")})
    audio = meta.get("audio")
    if isinstance(audio, dict) and audio.get("file"):
        files.append({"path": audio["file"], "role": "audio", "codec": audio.get("codec"),
                      "ext": audio.get("ext"), "original": audio.get("original"),
                      "reported_bytes": audio.get("bytes")})
    files.append({"path": meta_rel, "role": "meta"})
    seen = set()
    for item in files:
        rel = item["path"]
        if not isinstance(rel, str) or not _layout_name(rel, video_id) or rel in seen:
            raise ToolOutputError(f"the program named a file outside its layout: {rel!r}")
        seen.add(rel)
        path = staging / rel
        if not _regular(path) or not _inside(path, staging):
            raise ToolOutputError(f"the program named a file that is not a regular file in its folder: {rel}")
    return meta, files


def incomplete(meta: Dict[str, Any], placement: Optional["Placement"] = None) -> List[str]:
    """What is missing or wrong, one line each: from the program's record and, with ``placement``, the placed files.

    Audio counts as written only when the record names its file. A file
    whose size differs from the size the program reported, and an audio
    track the record calls a dub (``original`` false), are listed too.
    """
    out = []
    if meta.get("skipped"):
        out.append(f"skipped: {meta['skipped']}")
    for caption in meta.get("captions") or ():
        if isinstance(caption, dict) and caption.get("error"):
            out.append(f"caption {caption.get('language')}.{caption.get('kind')}.{caption.get('format')}: "
                       f"{caption['error']}")
    audio = meta.get("audio")
    written = isinstance(audio, dict) and bool(audio.get("file"))
    if (meta.get("request") or {}).get("audio") and not written and not meta.get("skipped"):
        error = audio.get("error") if isinstance(audio, dict) else None
        out.append(f"audio: not written{f' ({error})' if error else ''}")
    if written and audio.get("original") is False:
        out.append("audio: the track written is not the original one")
    for rel, reported, held in (placement.mismatches if placement is not None else ()):
        out.append(f"{rel}: the program reported {reported} bytes and the file holds {held}")
    return out


def record_path(target: Path, video_id: str) -> Path:
    return target / "meta" / f"{video_id}{RECORD_SUFFIX}"


def read_record(target: Path, video_id: str) -> Optional[Dict[str, Any]]:
    """This server's earlier record of ``video_id`` in ``target``, or ``None``."""
    path = record_path(target, video_id)
    if not _regular(path):
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("video_id") != video_id \
            or not isinstance(record.get("files"), list):
        return None
    return record


def latest_files(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The entries of ``record`` that its last fetch placed (all of them in a record without ``latest``)."""
    latest = record.get("latest")
    files = [item for item in record.get("files", ()) if isinstance(item, dict)]
    if not isinstance(latest, list):
        return files
    return [item for item in files if item.get("path") in latest]


def _unchanged(target: Path, video_id: str, item: Any) -> bool:
    """Whether the file an entry of a record names is still there with its size and SHA-256."""
    rel = item.get("path") if isinstance(item, dict) else None
    if not isinstance(rel, str) or not _layout_name(rel, video_id):
        return False
    path = target / rel
    return (_regular(path) and _inside(path, target) and path.stat().st_size == item.get("bytes")
            and sha256(path) == item.get("sha256"))


def already_complete(target: Path, video_id: str, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The earlier record when its last fetch was complete for ``req`` and its files still hold their size and SHA-256."""
    record = read_record(target, video_id)
    if record is None or record.get("request") != req or record.get("complete") is not True:
        return None
    if not all(_unchanged(target, video_id, item) for item in latest_files(record)):
        return None
    return record


def _link_or_fail(src: Path, dest: Path) -> None:
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
        dest.unlink(missing_ok=True)
        raise


def journal_path(target: Path, video_id: str) -> Path:
    return target / "meta" / f".{video_id}{JOURNAL_SUFFIX}"


def new_key() -> str:
    """A key for one placement's journal; the caller keeps it out of the folder."""
    return secrets.token_hex(32)


def _seal(body: Dict[str, Any], key: str) -> str:
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(bytes.fromhex(key), text.encode("utf-8"), hashlib.sha256).hexdigest()


def _write_json(path: Path, data: Any, scratch: Path) -> None:
    handle, tmp = tempfile.mkstemp(dir=scratch, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Placement:
    """What :func:`place` and :func:`write_record` change in a target, so it can be kept or undone.

    A file created there is removed on :meth:`undo`. A file replaced there
    is first moved to a new name beside it that nothing else holds
    (``.<name>.archivist-old-<random>``), dropped on :meth:`keep` and put
    back on :meth:`undo`; the folders it made are removed again when they are
    empty. Files it writes are written in ``scratch`` (the staging folder)
    first, so a crash leaves no half-written file in the target. Every
    change, a set-aside name included, is listed in a journal
    (``meta/.<id>.archivist-journal.json``, sealed with ``key``) before it
    is made, so :func:`recover` can finish or undo it after a crash.
    """

    def __init__(self, target: Path, video_id: str, scratch: Path, key: str) -> None:
        self.target, self.video_id, self.scratch, self.key = target, video_id, scratch, key
        self.placed: List[Dict[str, Any]] = []
        self.mismatches: List[Tuple[str, int, int]] = []
        self.linked: List[Tuple[Path, str]] = []
        self.set_aside: List[Dict[str, Any]] = []
        self.created: List[Path] = []
        self.journal = journal_path(target, video_id)

    def _rel(self, path: Path) -> str:
        return os.path.relpath(path, self.target)

    def _save(self) -> None:
        self.mkdir(self.journal.parent)
        body = {
            "video_id": self.video_id,
            "linked": [{"path": self._rel(dest), "sha256": digest} for dest, digest in self.linked],
            "set_aside": [{"aside": self._rel(e["aside"]), "dest": self._rel(e["dest"]),
                           "sha256": e["sha256"], "placed": e["placed"]} for e in self.set_aside]}
        _write_json(self.journal, {**body, "seal": _seal(body, self.key)}, self.scratch)

    def mkdir(self, folder: Path) -> None:
        missing = []
        walk = folder
        while walk != self.target and self.target in walk.parents and not walk.exists():
            missing.append(walk)
            walk = walk.parent
        folder.mkdir(parents=True, exist_ok=True)
        self.created.extend(reversed(missing))

    def link(self, src: Path, dest: Path, digest: str) -> None:
        self.linked.append((dest, digest))
        self._save()
        try:
            _link_or_fail(src, dest)
        except BaseException:
            self.linked.pop()
            raise

    def replace(self, src: Path, dest: Path, held: str, placed: str) -> None:
        aside = dest.parent / f".{dest.name}{SET_ASIDE}{secrets.token_hex(8)}"
        entry = {"aside": aside, "dest": dest, "sha256": held, "placed": placed, "made": False,
                 "moved": False}
        self.set_aside.append(entry)
        self._save()
        os.close(os.open(aside, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        entry["made"] = True
        os.replace(dest, aside)
        entry["moved"] = True
        os.replace(src, dest)

    def _finish(self) -> None:
        self.journal.unlink(missing_ok=True)
        for folder in reversed(self.created):
            try:
                folder.rmdir()
            except OSError:
                pass
        self.linked, self.set_aside, self.created = [], [], []

    def keep(self) -> None:
        for entry in self.set_aside:
            if entry["made"]:
                entry["aside"].unlink(missing_ok=True)
        self.created = []
        self._finish()

    def undo(self) -> None:
        for dest, _ in self.linked:
            dest.unlink(missing_ok=True)
        for entry in reversed(self.set_aside):
            if entry["moved"]:
                os.replace(entry["aside"], entry["dest"])
            elif entry["made"]:
                entry["aside"].unlink(missing_ok=True)
        self._finish()


def recover(target: Path, video_id: str, key: Optional[str]) -> None:
    """Finish or undo the placement a crash interrupted in ``target``, from its journal.

    ``key`` is the one the placement was given (None when the server holds
    none for this folder and video). A journal whose seal does not match it
    is a :class:`Conflict` and nothing is touched. Otherwise the completeness
    record decides: a file it describes as it now is stays, and its set-aside
    copy is dropped; otherwise the set-aside copy goes back and a file the run
    created is removed. Only files the journal names, with the content it
    records, are moved or removed: a set-aside copy goes back only over the
    bytes the run put there or an empty name. A file missing meanwhile is
    passed over.
    """
    path = journal_path(target, video_id)
    if not path.exists() and not path.is_symlink():
        return
    try:
        journal = (json.loads(path.read_text(encoding="utf-8"))
                   if _regular(path) and _inside(path, target) else None)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        journal = None
    if key is None:
        raise Conflict(f"meta/{path.name}: a journal is there and no key is stored for this folder and video,"
                       " so it is not acted on")
    if not isinstance(journal, dict) or journal.get("video_id") != video_id \
            or not hmac.compare_digest(str(journal.get("seal")),
                                       _seal({k: v for k, v in journal.items() if k != "seal"}, key)):
        raise Conflict(f"meta/{path.name}: a journal this server did not write for this placement is there")
    record = read_record(target, video_id)
    described = {item.get("path"): item.get("sha256") for item in (record or {}).get("files", ())
                 if isinstance(item, dict)}
    own_record = os.path.relpath(record_path(target, video_id), target)

    def ours(rel: Any) -> Optional[Path]:
        if not isinstance(rel, str):
            return None
        found = target / rel
        return found if _regular(found) and _inside(found, target) else None

    for entry in reversed([e for e in journal.get("set_aside", ()) if isinstance(e, dict)]):
        aside, dest_rel = ours(entry.get("aside")), entry.get("dest")
        if aside is None or not isinstance(dest_rel, str) \
                or not (dest_rel == own_record or _layout_name(dest_rel, video_id)) \
                or aside.parent != (target / dest_rel).parent \
                or not aside.name.startswith(f".{Path(dest_rel).name}{SET_ASIDE}"):
            continue
        dest = target / dest_rel
        try:
            free = not dest.exists() and not dest.is_symlink()
            if dest_rel == own_record:
                committed = record is not None
            elif _regular(dest):
                now = sha256(dest)
                committed = described.get(dest_rel) == now
                free = now == entry.get("placed")
            else:
                committed = False
            if not committed and free and sha256(aside) == entry.get("sha256"):
                os.replace(aside, dest)
            else:
                aside.unlink()
        except FileNotFoundError:
            continue
    for entry in journal.get("linked", ()):
        made = ours(entry.get("path")) if isinstance(entry, dict) else None
        if made is None:
            continue
        try:
            if entry["path"] == own_record:
                committed = record is not None
            else:
                committed = (not _layout_name(entry["path"], video_id)
                             or described.get(entry["path"]) == sha256(made))
            if not committed and sha256(made) == entry.get("sha256"):
                made.unlink()
        except FileNotFoundError:
            continue
    path.unlink(missing_ok=True)


def place(staging: Path, target: Path, video_id: str, files: List[Dict[str, Any]], key: str) -> Placement:
    """Put ``files`` (from :func:`read_meta`) at the same names in ``target``; each gains ``bytes`` and ``sha256``.

    Every name, the completeness record's included, is checked before
    anything is written: a :class:`Conflict` leaves the target as it was.
    """
    record = record_path(target, video_id)
    previous = {item.get("path"): item.get("sha256")
                for item in (read_record(target, video_id) or {}).get("files", ())
                if isinstance(item, dict)}
    placement = Placement(target, video_id, staging, key)
    plan = []
    conflicts = []
    if (record.is_symlink() or record.exists()) and read_record(target, video_id) is None:
        conflicts.append(f"meta/{record.name}: a file this server did not write is there")
    for item in files:
        src = staging / item["path"]
        dest = target / item["path"]
        digest = sha256(src)
        size = src.stat().st_size
        placed = {k: v for k, v in item.items() if k != "reported_bytes"}
        placed.update(bytes=size, sha256=digest)
        reported = item.get("reported_bytes")
        if isinstance(reported, int) and not isinstance(reported, bool) and reported != size:
            placement.mismatches.append((item["path"], reported, size))
        if dest.parent.exists() and (dest.parent.is_symlink() or not _inside(dest, target)):
            conflicts.append(f"{item['path']}: its folder is not a plain folder of the target")
            continue
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            conflicts.append(f"{item['path']}: not a regular file")
        elif not dest.exists():
            plan.append(("new", src, dest, placed, None))
        else:
            held = sha256(dest)
            if held == digest:
                plan.append(("same", src, dest, placed, held))
            elif previous.get(item["path"]) == held:
                plan.append(("replace", src, dest, placed, held))
            else:
                conflicts.append(f"{item['path']}: a file this server did not place is there")
    if conflicts:
        raise Conflict("; ".join(conflicts))
    try:
        for action, src, dest, placed, _ in plan:
            if action == "new":
                placement.mkdir(dest.parent)
                if not _inside(dest, target):
                    raise Conflict(f"{dest.parent} is not inside the target")
                placement.link(src, dest, placed["sha256"])
        for action, src, dest, placed, held in plan:
            if action == "replace":
                placement.replace(src, dest, held, placed["sha256"])
    except FileExistsError as e:
        placement.undo()
        raise Conflict(f"a file appeared at {e.filename}") from None
    except BaseException:
        placement.undo()
        raise
    placement.placed = [placed for _, _, _, placed, _ in plan]
    return placement


def write_record(target: Path, video_id: str, req: Dict[str, Any], complete: bool,
                 missing: List[str], placement: Placement,
                 tools: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Write ``meta/<id>.files.json`` as part of ``placement`` and return it.

    The record lists every file this server placed for the video in the
    folder: this fetch's (``latest`` names them) and, from the earlier
    record, each file this fetch did not place that is still there unchanged.
    It replaces only an earlier record of this server; anything else at the
    name is a :class:`Conflict`.
    """
    path = record_path(target, video_id)
    earlier = path.is_symlink() or path.exists()
    previous = read_record(target, video_id)
    if earlier and previous is None:
        raise Conflict(f"meta/{path.name}: a file this server did not write is there")
    files = [{k: v for k, v in p.items() if v is not None} for p in placement.placed]
    fetched = {item["path"] for item in files}
    carried = [item for item in (previous or {}).get("files", ())
               if isinstance(item, dict) and item.get("path") not in fetched
               and _unchanged(target, video_id, item)]
    record = {"video_id": video_id, "request": req, "complete": complete, "incomplete": missing,
              "files": files + carried, "latest": sorted(fetched), "tools": tools}
    placement.mkdir(path.parent)
    handle, tmp = tempfile.mkstemp(dir=placement.scratch, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if earlier:
            placement.replace(Path(tmp), path, sha256(path), sha256(Path(tmp)))
        else:
            try:
                placement.link(Path(tmp), path, sha256(Path(tmp)))
            except FileExistsError:
                raise Conflict(f"meta/{path.name}: a file appeared there") from None
    finally:
        Path(tmp).unlink(missing_ok=True)
    return record


def video_id_of(entry_key: Optional[str]) -> Optional[str]:
    if isinstance(entry_key, str) and entry_key.startswith("youtube:"):
        vid = entry_key[len("youtube:"):]
        if _VIDEO_ID.fullmatch(vid):
            return vid
    return None
