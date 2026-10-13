# SPDX-License-Identifier: Apache-2.0
"""Per-kind pauses after YouTube's bot check, kept in the working index.

YouTube challenges some kinds of request and not others, so each kind
pauses on its own: ``details`` (per-video metadata, stream resolution),
``listing`` (channel and playlist listings, a channel's Playlists tab),
``feed`` (channel feeds), ``thumb`` (pictures), ``search`` and
``download``. The first bot check of a kind pauses it for 30 minutes;
each further check after that pause has run out doubles the wait, up to
24 hours. A check that arrives while the kind is already paused leaves the
pause as it is, and a success (:meth:`Backoff.mark_clear`) ends the pause
and resets the wait.

A :class:`Backoff` built on an :class:`~media_archivist.indexdb.IndexDB`
keeps its state in the ``backoff`` table, so a pause outlives a restart;
one built without an index keeps it in memory. Reading a pause is a plain
read, which in WAL mode never waits for a writer; only recording or ending
one takes the write lock. Times are epoch seconds from the injected clock.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, Optional, Union

KINDS = ("details", "listing", "feed", "thumb", "search", "download")
INITIAL_WAIT_S = 30 * 60.0
MAX_WAIT_S = 24 * 3600.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


class _MemoryRows:
    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, float]] = {}

    def get(self, kind):
        row = self.rows.get(kind)
        return dict(row) if row else None

    def put(self, kind, row) -> None:
        self.rows[kind] = dict(row)

    def delete(self, kind) -> None:
        self.rows.pop(kind, None)


class _SqlRows:
    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self, kind):
        row = self.conn.execute("SELECT retry_at, wait_s, at FROM backoff WHERE kind = ?",
                                (kind,)).fetchone()
        return dict(row) if row else None

    def put(self, kind, row) -> None:
        self.conn.execute(
            "INSERT INTO backoff (kind, retry_at, wait_s, at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(kind) DO UPDATE SET retry_at = excluded.retry_at,"
            " wait_s = excluded.wait_s, at = excluded.at",
            (kind, row["retry_at"], row["wait_s"], row["at"]))

    def delete(self, kind) -> None:
        self.conn.execute("DELETE FROM backoff WHERE kind = ?", (kind,))


class Backoff:
    def __init__(self, index=None, *, initial: float = INITIAL_WAIT_S,
                 maximum: float = MAX_WAIT_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.index = index
        self.initial = initial
        self.maximum = maximum
        self.clock = clock
        self._memory = _MemoryRows() if index is None else None
        self._lock = threading.Lock()

    @contextmanager
    def _rows(self) -> Iterator[Union[_MemoryRows, _SqlRows]]:
        if self.index is None:
            with self._lock:
                yield self._memory
        else:
            with self.index.write() as conn:
                yield _SqlRows(conn)

    def _read(self, kind: str):
        """The stored row of ``kind``; a plain read that never waits for a writer."""
        if self.index is None:
            with self._lock:
                return self._memory.get(kind)
        return _SqlRows(self.index.conn).get(kind)

    @staticmethod
    def _check(kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown back-off kind {kind!r}; expected one of {', '.join(KINDS)}")

    def remaining(self, kind: str) -> float:
        """Seconds left in the pause of ``kind`` (0 when it is not paused)."""
        self._check(kind)
        row = self._read(kind)
        return max(0.0, row["retry_at"] - self.clock()) if row else 0.0

    def blocked_until(self, kind: str) -> Optional[float]:
        """When the pause of ``kind`` ends (epoch seconds), or ``None``."""
        self._check(kind)
        row = self._read(kind)
        if row and row["retry_at"] > self.clock():
            return row["retry_at"]
        return None

    def mark_blocked(self, kind: str) -> float:
        """Record a bot check for ``kind``; returns the seconds the pause lasts."""
        self._check(kind)
        with self._rows() as rows:
            now = self.clock()
            row = rows.get(kind)
            if row and now < row["retry_at"]:
                return row["retry_at"] - now
            wait = min(row["wait_s"] * 2, self.maximum) if row else self.initial
            rows.put(kind, {"retry_at": now + wait, "wait_s": wait, "at": now})
            return wait

    def mark_clear(self, kind: str, *, started_at: Optional[float] = None) -> None:
        """A request of ``kind`` succeeded: end its pause and reset the wait.

        With ``started_at`` (this object's clock when the request began), a
        pause recorded since then stays: the request was sent before the bot
        check that set it and says nothing about it.
        """
        self._check(kind)
        with self._rows() as rows:
            row = rows.get(kind)
            if row is not None and started_at is not None and row.get("at", 0) >= started_at:
                return
            rows.delete(kind)

    def adopt(self, other: "Backoff") -> None:
        """Take over ``other``'s pauses where they run longer than this one's."""
        for kind in KINDS:
            row = other._read(kind)
            if not row:
                continue
            remaining = row["retry_at"] - other.clock()
            with self._rows() as rows:
                now = self.clock()
                mine = rows.get(kind)
                if mine is None or mine["retry_at"] < now + remaining:
                    rows.put(kind, {"retry_at": now + remaining, "wait_s": row["wait_s"],
                                    "at": now - (other.clock() - row["at"])})

    def blocked(self) -> Dict[str, Dict[str, object]]:
        """Every kind's state, shaped as ``GET /api/v1/youtube/backoff`` shows it."""
        now = self.clock()
        out: Dict[str, Dict[str, object]] = {}
        for kind in KINDS:
            row = self._read(kind)
            if row and row["retry_at"] > now:
                out[kind] = {"paused": True, "until": _iso(row["retry_at"]),
                             "wait": int(row["wait_s"]), "since": _iso(row["at"])}
            else:
                out[kind] = {"paused": False}
        return out


def for_db(db_path) -> Backoff:
    """The persisted back-off of the working index beside ``db_path``."""
    from media_archivist.indexdb import open_index

    return Backoff(open_index(db_path))
