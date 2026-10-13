# SPDX-License-Identifier: Apache-2.0
"""Per-video details (length, upload date, channel) from one yt-dlp read.

A listing does not always say how long a video is and a feed never does.
What is still missing after the listings is read here, one video per
request, through the gate's ``details`` kind. Only one details read runs
at a time in a process; a caller that does not want to wait passes
``wait=False`` and gets ``None`` back when another read is running.

What a read learns goes into the index and into the video's row of the
JSON database, so the JSON database's own metadata fill does not ask
YouTube for the same video again.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional

from media_archivist import ytgate
from media_archivist.indexdb import utcnow
from media_archivist.ytindex import store

LOG = logging.getLogger("media_archivist.ytindex")

_running = threading.Lock()


def _extract(video_id: str) -> Dict[str, object]:
    from media_archivist import ytmeta

    return ytmeta.extract_video(video_id)


EXTRACT: Callable[[str], Dict[str, object]] = _extract


def _write_json(index, video_id: str, fields: Dict[str, object], stamp: str) -> None:
    from media_archivist import ytmeta
    from media_archivist.ytindex.client import watch_url

    def _apply(row):
        ytmeta.sanitize_row(row)
        ytmeta.merge_fields(row, fields)
        row["metadata_checked"] = stamp
        row.pop(ytmeta.NETWORK_FAILED_KEY, None)
        return True

    if index.json_path.is_file():
        ytmeta._write_row(str(index.json_path), watch_url(video_id), _apply)


def fill(index, entry_id: str, *, gate: Optional[ytgate.Gate] = None,
         extract: Optional[Callable[[str], Dict[str, object]]] = None,
         wait: bool = True, now: Optional[str] = None) -> Optional[str]:
    """Read one YouTube entry's details; returns what happened.

    ``filled``, ``checked`` (read in full before; not asked again),
    ``paused`` (the kind is paused, nothing was asked),
    ``bot-check``, ``network`` (tried again later), ``unavailable`` (not
    asked again), or ``None`` when ``wait`` is false and another read is
    running. Never raises for a failed read. ``now`` is the time stamped on
    the entry (the current time by default).
    """
    from media_archivist import ytmeta

    row = index.execute("SELECT key, metadata_checked FROM entries_meta WHERE entry_id = ?",
                        (entry_id,)).fetchone()
    if row is None or not row["key"].startswith("youtube:"):
        return None
    if row["metadata_checked"]:
        return "checked"
    video_id = row["key"].split(":", 1)[1]
    gate = gate or ytgate.get()
    if not _running.acquire(blocking=wait):
        return None
    try:
        try:
            info = gate.call("details", extract or EXTRACT, video_id)
        except ytgate.Paused:
            return "paused"
        except ytgate.BotCheck:
            return "bot-check"
        except ytgate.RequestFailed as e:
            now = now or utcnow()
            with index.write() as conn:
                column = "metadata_checked" if e.permanent else "network_failed_at"
                conn.execute(f"UPDATE entries_meta SET {column} = ? WHERE entry_id = ?",
                             (now, entry_id))
            return "unavailable" if e.permanent else "network"
    finally:
        _running.release()
    fields = ytmeta.fields_from_info(info)
    fields["channel_id"] = info.get("channel_id") or None
    fields["title"] = info.get("title") or None
    now = now or utcnow()
    with index.write() as conn:
        store.learn_details(conn, entry_id, fields, now)
    try:
        _write_json(index, video_id, fields, now)
    except Exception:
        LOG.exception("could not write the details of %s to the JSON database", video_id)
    return "filled"


def fill_missing(index, *, limit: int = 5, gate: Optional[ytgate.Gate] = None,
                 extract: Optional[Callable[[str], Dict[str, object]]] = None,
                 network_retry_before: Optional[str] = None,
                 now: Optional[str] = None) -> Dict[str, int]:
    """Fill up to ``limit`` entries that still lack a length.

    Stops at the first pause or bot check: the rest wait for the next pass.
    """
    outcome: Dict[str, int] = {}
    for row in store.missing_details(index, limit, network_retry_before=network_retry_before):
        result = fill(index, row["entry_id"], gate=gate, extract=extract, now=now)
        outcome[result or "busy"] = outcome.get(result or "busy", 0) + 1
        if result in ("paused", "bot-check", None):
            break
    return outcome
