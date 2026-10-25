"""Jellyfin / Kodi movie layout for downloaded files.

A downloaded file is filed as::

    <library>/
    └── <Title> (<Year>)/
        ├── <Title> (<Year>).<ext>
        ├── movie.nfo
        └── poster.<ext>

``movie.nfo`` comes from :func:`media_archivist.nfo.nfo_xml`; the poster is
the entry's thumbnail. The year is the entry's publication year, and is
left out of the names when the index does not know it. Music sources keep
the flat layout: their NFO is a ``<musicvideo>``, which a movie library
does not read.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import threading
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from media_archivist.models.canonical import MediaEntry
from media_archivist.nfo import (
    _MUSIC_SOURCES, _year_and_premiered, nfo_xml)
from media_archivist.strm import _safe

LOG = logging.getLogger("media_archivist.movie_layout")

ENV_VAR = "MEDIA_ARCHIVIST_MOVIE_LAYOUT"
_TRUTHY = {"1", "true", "yes", "on"}
_POSTER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_POSTER_MAX_BYTES = 10 * 1024 * 1024
_POSTER_DEADLINE_S = 20
_POSTER_CHUNK = 64 * 1024
_NAME_MAX_BYTES = 255
_EXT_RESERVE = len(".webm")
ENTRY_ID_SCHEME = "media-archivist"


def enabled_by_env() -> bool:
    """True when ``MEDIA_ARCHIVIST_MOVIE_LAYOUT`` is set to a truthy value."""
    return os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY


def applies_to(entry: MediaEntry) -> bool:
    return entry.source not in _MUSIC_SOURCES


def _truncate_bytes(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` UTF-8 bytes, on a character boundary."""
    return text.encode("utf-8")[:max(limit, 0)].decode("utf-8", errors="ignore")


def movie_name(entry: MediaEntry, *, tag: str = "", ext: str = "") -> str:
    """``Title (Year)``, or ``Title`` when the publication year is unknown.

    ``tag`` is appended as `` [tag]``. The title is cut so that the name plus
    an extension of up to ``len(".webm")`` bytes (or ``ext``, when longer)
    stays within the 255-byte file name limit; the cut does not depend on
    the extension, so one entry keeps one folder whatever it is filed as.
    """
    year, _ = _year_and_premiered(entry.published)
    tail = (f" ({year})" if year else "") + (f" [{tag}]" if tag else "")
    room = _NAME_MAX_BYTES - len(tail.encode("utf-8")) - max(len(ext.encode("utf-8")), _EXT_RESERVE)
    title = _truncate_bytes(_safe(entry.title or entry.url), room).strip(" ._")
    return (title or _safe("")) + tail


def _entry_tag(entry: MediaEntry) -> str:
    """A short stable identifier for telling two entries of one name apart."""
    return _safe(str(entry.raw.get("videoId") or entry.id))


def _is_ours(folder: Path, entry: MediaEntry) -> bool:
    """True when ``folder`` may take ``entry``: it is missing, empty, or its
    ``movie.nfo`` was written for this entry.

    The NFO names the entry by its ``media-archivist`` unique id. A folder
    that holds files but no NFO carrying that id for this entry belongs to
    somebody else, whatever provider ids a ``movie.nfo`` there shares with
    the entry.
    """
    if not folder.exists():
        return True
    if not folder.is_dir():
        return False
    nfo = folder / "movie.nfo"
    if not nfo.is_file():
        return not any(folder.iterdir())
    try:
        found = [(u.get("type"), (u.text or "").strip())
                 for u in ET.parse(nfo).getroot().findall("uniqueid")]
    except (ET.ParseError, OSError):
        return False
    return entry.id in [v for t, v in found if t == ENTRY_ID_SCHEME]


def _poster_ext(url: str, content_type: str) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in _POSTER_EXTS:
        return ext
    return {"image/png": ".png", "image/webp": ".webp"}.get(
        content_type.split(";")[0].strip().lower(), ".jpg")


def _abort(resp: requests.Response) -> None:
    """Cut the connection under ``resp`` so a read blocked on it returns."""
    try:
        resp.raw._fp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass
    resp.close()


def _download_poster(url: str, abandoned: threading.Event, out: dict) -> None:
    """Worker: fetch ``url`` into ``out`` (``body``, ``content_type`` or ``error``).

    It never touches the file system, so a worker the caller gave up on
    cannot leave a file behind.
    """
    resp = None
    try:
        resp = requests.get(url, timeout=(5, 5), stream=True)
        out["resp"] = resp
        if abandoned.is_set():
            _abort(resp)
            return
        resp.raise_for_status()
        body = bytearray()
        for chunk in resp.iter_content(_POSTER_CHUNK):
            body += chunk
            if len(body) > _POSTER_MAX_BYTES or abandoned.is_set():
                break
        out["content_type"] = resp.headers.get("Content-Type", "")
        out["body"] = bytes(body)
    except Exception as e:
        out["error"] = e
    finally:
        if resp is not None:
            resp.close()


def _fetch_poster(entry: MediaEntry, folder: Path) -> Optional[Path]:
    """Download the thumbnail into ``folder``; ``None`` when it cannot be had.

    The whole fetch, DNS and connect and headers and body, is bounded by
    ``_POSTER_DEADLINE_S``: it runs in a daemon thread that the caller stops
    waiting for when the deadline passes. The thread only collects bytes;
    this function writes the file, and only on success.
    """
    url = entry.thumbnail
    if not url or urlparse(url).scheme not in ("http", "https"):
        return None
    out: dict = {}
    abandoned = threading.Event()
    worker = threading.Thread(
        target=_download_poster, args=(url, abandoned, out),
        name="poster-fetch", daemon=True)
    worker.start()
    worker.join(_POSTER_DEADLINE_S)
    if worker.is_alive():
        abandoned.set()
        resp = out.get("resp")
        if resp is not None:
            _abort(resp)
        LOG.warning("poster download for %s passed its %s s deadline",
                    entry.id, _POSTER_DEADLINE_S)
        return None
    if "error" in out:
        LOG.warning("poster download failed for %s: %s", entry.id, out["error"])
        return None
    body = out.get("body", b"")
    if not body or len(body) > _POSTER_MAX_BYTES:
        LOG.warning("poster for %s is empty or over %d bytes", entry.id, _POSTER_MAX_BYTES)
        return None
    target = folder / f"poster{_poster_ext(url, out.get('content_type', ''))}"
    target.write_bytes(body)
    return target


def _claim_folder(library: Path, name: str, entry: MediaEntry) -> Optional[Path]:
    """Create or take over ``library/name``; ``None`` when it is somebody else's.

    ``mkdir`` is the claim, so two filings racing for a new name cannot both
    win it.
    """
    folder = library / name
    try:
        folder.mkdir(parents=True)
        return folder
    except FileExistsError:
        pass
    return folder if _is_ours(folder, entry) else None


_FILE_ELEMENT = "media_archivist_file"


def _nfo_with_file(entry: MediaEntry, file_name: str) -> str:
    """``nfo_xml`` plus a custom element recording the video's file name."""
    xml = nfo_xml(entry)
    close = xml.rindex("</movie>")
    return (xml[:close] + f"  <{_FILE_ELEMENT}>{escape(file_name)}</{_FILE_ELEMENT}>\n"
            + xml[close:])


def _recorded_video(folder: Path) -> Optional[Path]:
    """The video file name ``movie.nfo`` records as filed by this module."""
    try:
        text = ET.parse(folder / "movie.nfo").getroot().findtext(_FILE_ELEMENT)
    except (ET.ParseError, OSError):
        return None
    name = (text or "").strip()
    if not name or Path(name).name != name:
        return None
    path = folder / name
    return path if path.is_file() and not path.is_symlink() else None


def file_as_movie(downloaded: Path, entry: MediaEntry, library: Path) -> Path:
    """Move ``downloaded`` into ``library/<Title (Year)>/`` and write its sidecars.

    Returns the file's new path. A music entry is returned unchanged. A folder
    is used only when it is new, empty, or already holds this entry's
    ``movie.nfo``; otherwise the folder and file are named
    ``Title (Year) [id]`` instead. When the entry is filed again with another
    extension, the video recorded in its ``movie.nfo`` is replaced; any other
    file in the folder is left alone. ``movie.nfo`` is written before the move,
    so a filing that dies half way leaves a folder that is recognised as this
    entry's on the next try. Nothing is overwritten: when the file already
    exists, or both folder names are taken, ``FileExistsError`` is raised and
    ``downloaded`` stays where it was. The poster is best effort.
    """
    if not applies_to(entry):
        return downloaded
    library = Path(library)
    ext = downloaded.suffix
    name = movie_name(entry, ext=ext)
    folder = _claim_folder(library, name, entry)
    if folder is None:
        name = movie_name(entry, tag=_entry_tag(entry), ext=ext)
        folder = _claim_folder(library, name, entry)
    if folder is None:
        raise FileExistsError(f"{library / name} belongs to another entry")
    target = folder / f"{name}{ext}"
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{target} already exists")
    previous = _recorded_video(folder)
    (folder / "movie.nfo").write_text(_nfo_with_file(entry, target.name), encoding="utf-8")
    shutil.move(str(downloaded), str(target))
    if previous is not None and previous != target:
        previous.unlink()
    try:
        _fetch_poster(entry, folder)
    except Exception as e:
        LOG.warning("poster step failed for %s: %s", entry.id, e)
    return target
