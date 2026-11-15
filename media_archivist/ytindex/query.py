# SPDX-License-Identifier: Apache-2.0
"""Reading the index: the entries query, one entry, channel playlists.

Entries are ordered by the time the index first saw them, newest first,
with the entry id breaking ties. The cursor is the position of the last
entry of a page, so a page never shifts when new entries arrive: they sort
before every page an app is in the middle of reading.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from media_archivist import blockedwords
from media_archivist.ytindex.client import playlist_url

FIELDS = ("id", "key", "url", "title", "duration", "published", "first_seen", "channel",
          "thumbnail", "vertical", "subscriptions", "playlists")
MAX_LIMIT = 1000
_BATCH_MIN = 200


class QueryError(ValueError):
    """The query cannot be read (a bad time, cursor, field or range)."""


class UnknownId(LookupError):
    """A subscription or playlist named in the query does not exist."""


def parse_time(value: str) -> str:
    """An ISO 8601 time or date as the index stores times (UTC, seconds)."""
    text = (value or "").strip()
    try:
        ts = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        raise QueryError(f"not an ISO 8601 time: {value!r}") from None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def api_time(value: Optional[str]) -> Optional[str]:
    """A stored time as the API shows it (``…Z``)."""
    if not value:
        return None
    try:
        return parse_time(value).replace("+00:00", "Z")
    except QueryError:
        return value


def encode_cursor(first_seen: str, entry_id: str) -> str:
    raw = json.dumps({"t": first_seen, "i": entry_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str):
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        if isinstance(data["t"], str) and isinstance(data["i"], str):
            return data["t"], data["i"]
    except (ValueError, KeyError, TypeError):
        pass
    raise QueryError("cursor is not one this server gave out")


@dataclass
class EntryQuery:
    subscriptions: Sequence[str] = ()
    playlists: Sequence[str] = ()
    since: Optional[str] = None
    first_seen_after: Optional[str] = None
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None
    unknown_duration: Optional[str] = None
    exclude: Sequence[str] = ()
    vertical: Optional[bool] = None
    fields: Optional[Sequence[str]] = None
    limit: int = 100
    cursor: Optional[str] = None

    def check(self) -> None:
        if not 1 <= self.limit <= MAX_LIMIT:
            raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
        if self.unknown_duration not in (None, "include", "exclude"):
            raise QueryError("unknown_duration must be include or exclude")
        for name in ("min_duration", "max_duration"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise QueryError(f"{name} must be 0 or more")
        if self.min_duration is not None and self.max_duration is not None \
                and self.min_duration > self.max_duration:
            raise QueryError("min_duration is above max_duration")
        bad = [f for f in self.fields or () if f not in FIELDS]
        if bad:
            raise QueryError(f"unknown field(s) {', '.join(bad)}; known: {', '.join(FIELDS)}")


def _marks(values) -> str:
    return ", ".join("?" for _ in values)


def _check_ids(index, q: EntryQuery) -> None:
    if q.subscriptions:
        found = {r[0] for r in index.execute(
            f"SELECT id FROM subscriptions WHERE id IN ({_marks(q.subscriptions)})",
            list(q.subscriptions))}
        missing = [s for s in q.subscriptions if s not in found]
        if missing:
            raise UnknownId(f"unknown subscription {missing[0]}")
    if q.playlists:
        found = {r[0] for r in index.execute(
            f"SELECT playlist_id FROM playlists WHERE playlist_id IN ({_marks(q.playlists)})",
            list(q.playlists))}
        missing = [p for p in q.playlists if p not in found]
        if missing:
            raise UnknownId(f"unknown playlist {missing[0]}")


def _where(q: EntryQuery) -> tuple:
    clauses: List[str] = []
    params: List[object] = []
    if q.subscriptions:
        clauses.append(f"e.entry_id IN (SELECT entry_id FROM memberships"
                       f" WHERE subscription_id IN ({_marks(q.subscriptions)}))")
        params += list(q.subscriptions)
    if q.playlists:
        clauses.append(f"e.entry_id IN (SELECT entry_id FROM playlist_items"
                       f" WHERE playlist_id IN ({_marks(q.playlists)}))")
        params += list(q.playlists)
    if q.first_seen_after:
        clauses.append("e.first_seen > ?")
        params.append(q.first_seen_after)
    if q.min_duration is not None or q.max_duration is not None:
        window = []
        if q.min_duration is not None:
            window.append("e.duration >= ?")
            params.append(q.min_duration)
        if q.max_duration is not None:
            window.append("e.duration <= ?")
            params.append(q.max_duration)
        cond = " AND ".join(window)
        if q.unknown_duration == "include":
            clauses.append(f"(e.duration IS NULL OR ({cond}))")
        else:
            clauses.append(f"(e.duration IS NOT NULL AND {cond})")
    elif q.unknown_duration == "exclude":
        clauses.append("e.duration IS NOT NULL")
    if q.vertical is True:
        clauses.append("e.vertical = 1")
    elif q.vertical is False:
        clauses.append("(e.vertical IS NULL OR e.vertical = 0)")
    if q.since:
        scope = ""
        scope_params: List[object] = []
        if q.subscriptions:
            scope = f" AND m.subscription_id IN ({_marks(q.subscriptions)})"
            scope_params = list(q.subscriptions)
        clauses.append(
            "(e.published >= ? OR (e.published IS NULL AND EXISTS ("
            "SELECT 1 FROM memberships m JOIN subscriptions s ON s.id = m.subscription_id"
            " WHERE m.entry_id = e.entry_id AND m.first_seen >= ?"
            " AND s.first_indexed_at IS NOT NULL AND m.first_seen > s.first_indexed_at"
            f"{scope})))")
        params += [q.since[:10], q.since] + scope_params
    return clauses, params


def _complete(index, q: EntryQuery) -> bool:
    if q.subscriptions:
        rows = index.execute(
            f"SELECT first_indexed_at FROM subscriptions WHERE id IN ({_marks(q.subscriptions)})",
            list(q.subscriptions)).fetchall()
    elif q.playlists:
        rows = index.execute(
            f"SELECT videos_at AS first_indexed_at FROM playlists"
            f" WHERE playlist_id IN ({_marks(q.playlists)})", list(q.playlists)).fetchall()
    else:
        rows = index.execute("SELECT first_indexed_at FROM subscriptions").fetchall()
    return all(r["first_indexed_at"] for r in rows)


def _hydrate(index, rows, fields: Sequence[str]) -> List[Dict[str, object]]:
    ids = [r["entry_id"] for r in rows]
    subs: Dict[str, List[str]] = {i: [] for i in ids}
    lists: Dict[str, List[Dict[str, object]]] = {i: [] for i in ids}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        if "subscriptions" in fields:
            for r in index.execute(
                    f"SELECT m.entry_id, m.subscription_id FROM memberships m"
                    f" JOIN subscriptions s ON s.id = m.subscription_id"
                    f" WHERE m.entry_id IN ({_marks(chunk)}) ORDER BY m.subscription_id", chunk):
                subs[r["entry_id"]].append(r["subscription_id"])
        if "playlists" in fields:
            for r in index.execute(
                    f"SELECT i.entry_id, p.playlist_id, p.title FROM playlist_items i"
                    f" JOIN playlists p ON p.playlist_id = i.playlist_id"
                    f" WHERE i.entry_id IN ({_marks(chunk)}) ORDER BY p.playlist_id", chunk):
                lists[r["entry_id"]].append({"id": r["playlist_id"], "title": r["title"]})
    out = []
    for r in rows:
        full = {
            "id": r["entry_id"],
            "key": r["key"],
            "url": r["url"],
            "title": r["title"],
            "duration": r["duration"],
            "published": r["published"],
            "first_seen": api_time(r["first_seen"]),
            "channel": ({"id": r["channel_id"], "title": r["channel"]}
                        if r["channel_id"] or r["channel"] else None),
            "thumbnail": r["thumbnail"],
            "vertical": None if r["vertical"] is None else bool(r["vertical"]),
            "subscriptions": subs[r["entry_id"]],
            "playlists": lists[r["entry_id"]],
        }
        out.append({k: full[k] for k in fields})
    return out


def query_entries(index, q: EntryQuery) -> Dict[str, object]:
    """One page of entries; ``{"entries", "next_cursor", "complete"}``."""
    q.check()
    _check_ids(index, q)
    clauses, params = _where(q)
    fields = list(q.fields or FIELDS)
    position = decode_cursor(q.cursor) if q.cursor else None
    batch = max(_BATCH_MIN, q.limit + 1)
    picked = []
    while len(picked) <= q.limit:
        where = list(clauses)
        args = list(params)
        if position:
            where.append("(e.first_seen < ? OR (e.first_seen = ? AND e.entry_id < ?))")
            args += [position[0], position[0], position[1]]
        sql = ("SELECT e.* FROM entries_meta e"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY e.first_seen DESC, e.entry_id DESC LIMIT ?")
        rows = index.execute(sql, args + [batch]).fetchall()
        for r in rows:
            if q.exclude and blockedwords.blocked(r["title"], q.exclude):
                continue
            picked.append(r)
            if len(picked) > q.limit:
                break
        if len(rows) < batch:
            break
        position = (rows[-1]["first_seen"], rows[-1]["entry_id"])
    page = picked[:q.limit]
    more = len(picked) > q.limit
    return {
        "entries": _hydrate(index, page, fields),
        "next_cursor": encode_cursor(page[-1]["first_seen"], page[-1]["entry_id"])
        if more and page else None,
        "complete": _complete(index, q),
    }


def resolve_entry(index, ref: str) -> Optional[str]:
    """The entry id behind an entry id or key (``youtube:<videoId>``)."""
    row = index.execute("SELECT entry_id FROM entries_meta WHERE entry_id = ? OR key = ?",
                        (ref, ref)).fetchone()
    return row["entry_id"] if row else None


def get_entry(index, ref: str) -> Optional[Dict[str, object]]:
    entry_id = resolve_entry(index, ref)
    if entry_id is None:
        return None
    row = index.execute("SELECT * FROM entries_meta WHERE entry_id = ?", (entry_id,)).fetchone()
    return _hydrate(index, [row], FIELDS)[0]


def _playlist_api(index, row) -> Dict[str, object]:
    pid = row["playlist_id"]
    videos = None
    if row["videos_at"]:
        videos = [r["key"] for r in index.execute(
            "SELECT e.key FROM playlist_items i JOIN entries_meta e ON e.entry_id = i.entry_id"
            " WHERE i.playlist_id = ? ORDER BY i.position", (pid,))]
    raw = pid.split(":", 2)[2] if pid.startswith("youtube:playlist:") else pid
    return {
        "id": pid,
        "title": row["title"],
        "url": playlist_url(raw),
        "thumbnail": row["thumbnail"],
        "video_count": row["video_count"],
        "videos": videos,
        "videos_at": api_time(row["videos_at"]),
        "error": row["error"],
    }


def channel_playlists(index, subscription_id: str) -> Optional[Dict[str, object]]:
    """The playlists on a subscribed channel's Playlists tab, in the tab's order."""
    sub = index.execute("SELECT playlists_at FROM subscriptions WHERE id = ?",
                        (subscription_id,)).fetchone()
    if sub is None:
        return None
    rows = index.execute("SELECT * FROM playlists WHERE channel_subscription_id = ?"
                         " ORDER BY position, playlist_id", (subscription_id,)).fetchall()
    return {"playlists": [_playlist_api(index, r) for r in rows],
            "listed_at": api_time(sub["playlists_at"])}


def get_playlist(index, playlist_id: str) -> Optional[Dict[str, object]]:
    row = index.execute("SELECT * FROM playlists WHERE playlist_id = ?", (playlist_id,)).fetchone()
    return _playlist_api(index, row) if row else None
