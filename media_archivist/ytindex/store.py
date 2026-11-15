# SPDX-License-Identifier: Apache-2.0
"""Writing what a listing found into the working index.

Every function takes the connection of an open
:meth:`~media_archivist.indexdb.IndexDB.write` transaction, so one listing
lands in one transaction. A value once known is never replaced by a source
that lacks it: a listing without lengths keeps the lengths an earlier one
gave, a feed without a channel name keeps the name.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.ytindex.client import VideoItem, watch_url


def entry_key(video_id: str) -> str:
    return f"youtube:{video_id}"


def entry_id_for(video_id: str) -> str:
    """The opaque id of a YouTube video, the one the JSON database's row has."""
    return stable_id(Source.YOUTUBE, watch_url(video_id))


def thumbnail_for(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def upsert_videos(conn, items: Iterable[VideoItem], now: str, *,
                  channel_id: Optional[str] = None,
                  channel: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """Make sure every video has an ``entries_meta`` row and fold in what is new.

    Returns the entry ids in the order given and the ids of the rows that
    did not exist before.
    """
    ids: List[str] = []
    new: List[str] = []
    for item in items:
        key = entry_key(item.video_id)
        row = conn.execute("SELECT entry_id FROM entries_meta WHERE key = ?", (key,)).fetchone()
        vertical = None if item.vertical is None else int(item.vertical)
        if row is None:
            entry_id = entry_id_for(item.video_id)
            conn.execute(
                "INSERT INTO entries_meta (entry_id, key, url, first_seen, duration, published,"
                " channel_id, channel, vertical, title, thumbnail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, key, watch_url(item.video_id), now, item.duration, item.published,
                 channel_id, channel, vertical, item.title,
                 item.thumbnail or thumbnail_for(item.video_id)))
            new.append(entry_id)
        else:
            entry_id = row["entry_id"]
            conn.execute(
                "UPDATE entries_meta SET title = COALESCE(?, title),"
                " duration = COALESCE(?, duration), published = COALESCE(published, ?),"
                " channel_id = COALESCE(channel_id, ?), channel = COALESCE(channel, ?),"
                " vertical = COALESCE(?, vertical),"
                " thumbnail = COALESCE(thumbnail, ?) WHERE entry_id = ?",
                (item.title, item.duration, item.published, channel_id, channel, vertical,
                 item.thumbnail or thumbnail_for(item.video_id), entry_id))
        ids.append(entry_id)
    return ids, new


def record_memberships(conn, subscription_id: str, entry_ids: Sequence[str], now: str, *,
                       positions: bool = True) -> List[str]:
    """List ``entry_ids`` under the subscription; returns those it did not list before.

    With ``positions`` the order given is the listing's order and replaces
    the stored one; a feed passes ``positions=False`` and leaves it alone.
    ``first_seen`` is written once, when the subscription first lists the entry.
    """
    added = []
    for pos, entry_id in enumerate(entry_ids):
        cur = conn.execute(
            "INSERT OR IGNORE INTO memberships (subscription_id, entry_id, position, first_seen)"
            " VALUES (?, ?, ?, ?)", (subscription_id, entry_id, pos if positions else None, now))
        if cur.rowcount:
            added.append(entry_id)
        elif positions:
            conn.execute("UPDATE memberships SET position = ? WHERE subscription_id = ?"
                         " AND entry_id = ?", (pos, subscription_id, entry_id))
    return added


def replace_playlist_items(conn, playlist_id: str, entry_ids: Sequence[str]) -> None:
    """The playlist's video ids, in playlist order, replacing what was stored."""
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    seen = set()
    rows = []
    for entry_id in entry_ids:
        if entry_id not in seen:
            seen.add(entry_id)
            rows.append((playlist_id, entry_id, len(rows)))
    conn.executemany("INSERT INTO playlist_items (playlist_id, entry_id, position)"
                     " VALUES (?, ?, ?)", rows)


def learn_details(conn, entry_id: str, fields: Dict[str, object], now: str) -> None:
    """Fold a per-video read (length, upload date, channel) into the entry.

    Stamps ``metadata_checked`` so the background fill does not ask again.
    """
    conn.execute(
        "UPDATE entries_meta SET duration = COALESCE(?, duration),"
        " published = COALESCE(?, published), channel = COALESCE(channel, ?),"
        " channel_id = COALESCE(channel_id, ?), title = COALESCE(title, ?),"
        " metadata_checked = ? WHERE entry_id = ?",
        (fields.get("duration"), fields.get("published") or None, fields.get("author"),
         fields.get("channel_id"), fields.get("title"), now, entry_id))


def missing_details(index, limit: int, *,
                    network_retry_before: Optional[str] = None) -> List[Dict[str, object]]:
    """Entries a subscription or playlist lists that still lack a length.

    Only entries never read in full, newest first. A missing upload date
    alone is not a reason to ask: listings never carry one, and the query
    falls back to the time the entry was first seen. An entry whose last read
    failed on the network waits until ``network_retry_before`` has passed
    that failure, and comes after the rest.
    """
    rows = index.execute(
        "SELECT e.entry_id, e.key FROM entries_meta e"
        " WHERE e.key LIKE 'youtube:%' AND e.metadata_checked IS NULL"
        " AND e.duration IS NULL"
        " AND (EXISTS (SELECT 1 FROM memberships m WHERE m.entry_id = e.entry_id)"
        "      OR EXISTS (SELECT 1 FROM playlist_items p WHERE p.entry_id = e.entry_id))"
        " AND (e.network_failed_at IS NULL OR e.network_failed_at <= ?)"
        " ORDER BY e.network_failed_at IS NOT NULL, e.first_seen DESC, e.entry_id DESC"
        " LIMIT ?", (network_retry_before or "9999", limit))
    return [dict(r) for r in rows]
