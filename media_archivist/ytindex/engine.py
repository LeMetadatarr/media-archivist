# SPDX-License-Identifier: Apache-2.0
"""Keeping every subscription's listing current.

One pass (:meth:`Indexer.run_due`) does whatever is due, one request at a
time through the gate:

``listing``
    A channel's whole Videos tab once a week, a playlist's videos once a
    day, and either at once when the subscription is new or a refresh was
    asked for. A channel's listing also gives its channel id, so a
    subscription added by handle is moved to its permanent id here. An
    empty listing of a channel that listed videos before is a failure:
    tutubo returns nothing when YouTube's page markup changes.
``feed``
    A channel's feed (its latest uploads, one request) every two hours,
    once its channel id is known.
``playlists``
    A channel's Playlists tab once a day, when ``index_playlists`` is on.
``playlist_videos``
    The video ids of those playlists, each once a day, oldest first, at
    most :attr:`Indexer.playlists_per_run` per pass.
``thumbnail``
    A channel's picture when no listing supplied it, retried a day after a
    failure.
``details``
    A few per-video reads for entries that still lack a length (see
    :mod:`media_archivist.ytindex.details`).

Only YouTube channels and playlists are indexed; subscriptions of the other
backends are kept current by the sidecar's syncs. A paused kind is skipped,
and the work waits for a later pass. A bot check
is not the subscription's fault; any other failure of a listing or feed
counts, and the third in a row flags the subscription, which is then left
alone until it is changed or refreshed by hand.

New videos also become rows of the JSON database, and the callables in
:data:`ON_NEW_ENTRIES` are told about the entries a subscription with
automatic export lists for the first time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from media_archivist import subscriptions as subs
from media_archivist import ytgate
from media_archivist.ytindex import archive, details, store
from media_archivist.ytindex.client import TutuboClient, channel_url

LOG = logging.getLogger("media_archivist.ytindex")

PLAYLISTS_PER_RUN = 8
DETAILS_PER_RUN = 5
DETAILS_NETWORK_RETRY = timedelta(hours=24)

def _export_new_entries(index, subscription_id: str, entry_ids: List[str]) -> None:
    from media_archivist import export_hooks

    export_hooks.on_entries_added(subscription_id, entry_ids, db_path=str(index.json_path))


ON_NEW_ENTRIES: List[Callable[[object, str, List[str]], None]] = [_export_new_entries]
"""Called as ``hook(index, subscription_id, entry_ids)`` after a listing or
feed adds entries to a subscription whose ``export_automatic`` is on; the
default hook exports them (:mod:`media_archivist.export_hooks`)."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _older(value: Optional[str], now: datetime, every: timedelta) -> bool:
    ts = subs._parse_ts(value)
    return ts is None or ts + every <= now


def _playlist_raw_id(playlist_id: str) -> str:
    return playlist_id.split(":", 2)[2] if playlist_id.startswith("youtube:playlist:") else playlist_id


class Indexer:
    def __init__(self, index, *, client=None, gate: Optional[ytgate.Gate] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 playlists_per_run: int = PLAYLISTS_PER_RUN,
                 details_per_run: int = DETAILS_PER_RUN,
                 extract=None, archive_json: bool = True) -> None:
        self.index = index
        self.client = client or TutuboClient()
        self.gate = gate or ytgate.get()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.playlists_per_run = playlists_per_run
        self.details_per_run = details_per_run
        self.extract = extract
        self.archive_json = archive_json

    # -- what is due ------------------------------------------------------

    def _subscriptions(self) -> list:
        """The YouTube channels and playlists the index follows, unflagged."""
        return self.index.execute(
            "SELECT * FROM subscriptions WHERE flagged = 0 AND backend = 'youtube'"
            " AND kind IN ('channel', 'playlist') ORDER BY created_at, id").fetchall()

    def _listing_due(self, row, now: datetime) -> bool:
        every = subs.PLAYLIST_LISTING_EVERY if row["kind"] == "playlist" else subs.CHANNEL_LISTING_EVERY
        return bool(row["listing_requested"] or not row["first_indexed_at"]
                    or subs.is_unresolved(row["id"]) or _older(row["last_listing_at"], now, every))

    def _due_playlists(self, now: datetime, limit: int) -> List[str]:
        cutoff = _iso(now - subs.PLAYLIST_VIDEOS_EVERY)
        rows = self.index.execute(
            "SELECT p.playlist_id FROM playlists p JOIN subscriptions s"
            " ON s.id = p.channel_subscription_id"
            " WHERE s.flagged = 0 AND s.index_playlists = 1"
            " AND (p.videos_at IS NULL OR p.videos_at <= ?)"
            " ORDER BY p.videos_at IS NOT NULL, p.videos_at, p.position LIMIT ?",
            (cutoff, limit)).fetchall()
        return [r["playlist_id"] for r in rows]

    def due(self) -> List[Tuple[str, str]]:
        """``(subscription id, job)`` for everything due now; ``*`` for the shared jobs."""
        now = self.clock()
        paused = {k: self.gate.paused(k) for k in ("listing", "feed", "thumb", "details")}
        jobs: List[Tuple[str, str]] = []
        for row in self._subscriptions():
            sid = row["id"]
            listing = self._listing_due(row, now)
            if listing:
                if not paused["listing"]:
                    jobs.append((sid, "listing"))
                continue
            if row["kind"] != "channel":
                continue
            if row["channel_id"] and not paused["feed"] and \
                    _older(row["last_feed_at"], now, subs.FEED_EVERY):
                jobs.append((sid, "feed"))
            if row["index_playlists"] and not paused["listing"] and \
                    _older(row["playlists_at"], now, subs.PLAYLISTS_TAB_EVERY):
                jobs.append((sid, "playlists"))
            if not row["thumbnail"] and not paused["thumb"] and \
                    _older(row["thumbnail_at"], now, subs.THUMBNAIL_RETRY):
                jobs.append((sid, "thumbnail"))
        if not paused["listing"] and self._due_playlists(now, 1):
            jobs.append(("*", "playlist_videos"))
        if not paused["details"] and store.missing_details(
                self.index, 1, network_retry_before=_iso(now - DETAILS_NETWORK_RETRY)):
            jobs.append(("*", "details"))
        return jobs

    # -- running ----------------------------------------------------------

    def run_due(self) -> Dict[str, int]:
        """Run every due job once; returns how many of each ran."""
        done: Dict[str, int] = {}
        for sid, job in self.due():
            outcome = self.run(sid, job)
            done[f"{job}:{outcome}"] = done.get(f"{job}:{outcome}", 0) + 1
        return done

    def run(self, subscription_id: str, job: str) -> str:
        """Run one job; returns ``ok``, ``paused``, ``bot-check``, ``failed``, ``gone``
        or ``unsupported`` (not a YouTube channel or playlist)."""
        if job == "playlist_videos":
            return self.playlist_videos()
        if job == "details":
            details.fill_missing(self.index, limit=self.details_per_run, gate=self.gate,
                                 extract=self.extract,
                                 network_retry_before=_iso(self.clock() - DETAILS_NETWORK_RETRY),
                                 now=_iso(self.clock()))
            return "ok"
        row = self.index.execute("SELECT * FROM subscriptions WHERE id = ?",
                                 (subscription_id,)).fetchone()
        if row is None:
            return "gone"
        if row["backend"] != "youtube" or row["kind"] not in ("channel", "playlist"):
            return "unsupported"
        step = {"listing": self.listing, "feed": self.feed, "playlists": self.playlists,
                "thumbnail": self.thumbnail}[job]
        try:
            step(row)
        except ytgate.Paused:
            return "paused"
        except ytgate.BotCheck as e:
            self._note_error(row["id"], f"bot check: {e}", count=False)
            return "bot-check"
        except ytgate.RequestFailed as e:
            if job == "thumbnail":
                self._note_thumbnail_error(row["id"], str(e))
            else:
                self._note_error(row["id"], str(e), count=job in ("listing", "feed"))
            return "failed"
        return "ok"

    def refresh(self, subscription_id: str) -> str:
        """A full listing now, whatever the schedule says."""
        try:
            subs.request_listing(self.index, subscription_id)
        except subs.SubscriptionNotFound:
            return "gone"
        return self.run(subscription_id, "listing")

    def _note_error(self, sid: str, message: str, *, count: bool) -> None:
        LOG.warning("indexing %s failed: %s", sid, message)
        with self.index.write() as conn:
            if count:
                subs.record_failure(conn, sid, message)
            else:
                conn.execute("UPDATE subscriptions SET last_error = ? WHERE id = ?", (message, sid))

    def _note_thumbnail_error(self, sid: str, message: str) -> None:
        with self.index.write() as conn:
            conn.execute("UPDATE subscriptions SET thumbnail_at = ?, thumbnail_error = ?"
                         " WHERE id = ?", (_iso(self.clock()), message, sid))

    def _after(self, sid: str, added: List[str], new_items: list, *,
               channel: Optional[str] = None, playlist: Optional[str] = None) -> None:
        if self.archive_json and new_items:
            try:
                archive.add_rows(self.index.json_path, new_items, channel=channel, playlist=playlist)
            except Exception:
                LOG.exception("could not add %d new videos to the JSON database", len(new_items))
        if not added or not ON_NEW_ENTRIES:
            return
        row = self.index.execute("SELECT export_automatic FROM subscriptions WHERE id = ?",
                                 (sid,)).fetchone()
        if row is None or not row["export_automatic"]:
            return
        for hook in list(ON_NEW_ENTRIES):
            try:
                hook(self.index, sid, list(added))
            except Exception:
                LOG.exception("new-entry hook %r failed for %s", hook, sid)

    # -- the jobs ---------------------------------------------------------

    def listing(self, row) -> None:
        if row["kind"] == "playlist":
            self._playlist_listing(row)
        else:
            self._channel_listing(row)

    def _channel_listing(self, row) -> None:
        url = channel_url(row["channel_id"]) if row["channel_id"] else row["url"]
        info = self.gate.call("listing", self.client.read_channel, url, videos=True)
        if not info.videos and self.index.execute(
                "SELECT 1 FROM memberships WHERE subscription_id = ? LIMIT 1", (row["id"],)).fetchone():
            raise ytgate.RequestFailed("listing", "the listing returned no videos")
        now = _iso(self.clock())
        with self.index.write() as conn:
            sid = row["id"]
            if subs.is_unresolved(sid) or row["channel_id"] != info.channel_id:
                sid = subs.rekey_indexed(conn, sid, info.channel_id)
            ids, new = store.upsert_videos(conn, info.videos, now, channel_id=info.channel_id,
                                           channel=info.title)
            added = store.record_memberships(conn, sid, ids, now)
            conn.execute(
                "UPDATE subscriptions SET channel_id = ?, title = COALESCE(?, title),"
                " thumbnail = COALESCE(?, thumbnail),"
                " thumbnail_at = CASE WHEN ? IS NULL THEN thumbnail_at ELSE ? END,"
                " last_listing_at = ?, first_indexed_at = COALESCE(first_indexed_at, ?),"
                " listing_requested = 0 WHERE id = ?",
                (info.channel_id, info.title, info.thumbnail, info.thumbnail, now, now, now, sid))
            subs.record_success(conn, sid)
        new_set = set(new)
        self._after(sid, added, [v for v, i in zip(info.videos, ids) if i in new_set],
                    channel=info.title)

    def _playlist_listing(self, row) -> None:
        sid = row["id"]
        raw_id = _playlist_raw_id(sid)
        info = self.gate.call("listing", self.client.read_playlist, raw_id)
        now = _iso(self.clock())
        with self.index.write() as conn:
            ids, new = store.upsert_videos(conn, info.videos, now)
            added = store.record_memberships(conn, sid, ids, now)
            self._store_playlist(conn, sid, info, ids, now, channel_subscription_id=None)
            conn.execute(
                "UPDATE subscriptions SET title = COALESCE(?, title),"
                " thumbnail = COALESCE(?, thumbnail),"
                " thumbnail_at = CASE WHEN ? IS NULL THEN thumbnail_at ELSE ? END,"
                " last_listing_at = ?, first_indexed_at = COALESCE(first_indexed_at, ?),"
                " listing_requested = 0 WHERE id = ?",
                (info.title, info.thumbnail, info.thumbnail, now, now, now, sid))
            subs.record_success(conn, sid)
        new_set = set(new)
        self._after(sid, added, [v for v, i in zip(info.videos, ids) if i in new_set],
                    playlist=info.title)

    @staticmethod
    def _store_playlist(conn, playlist_id: str, info, ids, now: str, *,
                        channel_subscription_id: Optional[str]) -> None:
        conn.execute(
            "INSERT INTO playlists (playlist_id, channel_subscription_id, title, thumbnail,"
            " video_count, refreshed_at, videos_at, error, thumbnail_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)"
            " ON CONFLICT(playlist_id) DO UPDATE SET title = COALESCE(excluded.title, title),"
            " thumbnail = COALESCE(thumbnail, excluded.thumbnail),"
            " video_count = excluded.video_count, videos_at = excluded.videos_at, error = NULL,"
            " channel_subscription_id = COALESCE(channel_subscription_id,"
            " excluded.channel_subscription_id)",
            (playlist_id, channel_subscription_id, info.title, info.thumbnail, len(ids), now, now,
             now if info.thumbnail else None))
        store.replace_playlist_items(conn, playlist_id, ids)

    def feed(self, row) -> None:
        items = self.gate.call("feed", self.client.read_feed, row["channel_id"])
        now = _iso(self.clock())
        sid = row["id"]
        with self.index.write() as conn:
            ids, new = store.upsert_videos(conn, items, now, channel_id=row["channel_id"],
                                           channel=row["title"])
            added = store.record_memberships(conn, sid, ids, now, positions=False)
            conn.execute("UPDATE subscriptions SET last_feed_at = ? WHERE id = ?", (now, sid))
            subs.record_success(conn, sid)
        new_set = set(new)
        self._after(sid, added, [v for v, i in zip(items, ids) if i in new_set],
                    channel=row["title"])

    def playlists(self, row) -> None:
        sid = row["id"]
        raw_ids = self.gate.call("listing", self.client.channel_playlist_ids,
                                 channel_url(row["channel_id"]) if row["channel_id"] else row["url"])
        now = _iso(self.clock())
        ids = [f"youtube:playlist:{p}" for p in raw_ids]
        with self.index.write() as conn:
            for pos, pid in enumerate(ids):
                conn.execute(
                    "INSERT INTO playlists (playlist_id, channel_subscription_id, position,"
                    " refreshed_at) VALUES (?, ?, ?, ?) ON CONFLICT(playlist_id) DO UPDATE SET"
                    " channel_subscription_id = excluded.channel_subscription_id,"
                    " position = excluded.position, refreshed_at = excluded.refreshed_at",
                    (pid, sid, pos, now))
            gone = [r["playlist_id"] for r in conn.execute(
                "SELECT playlist_id FROM playlists WHERE channel_subscription_id = ?", (sid,))
                if r["playlist_id"] not in set(ids)]
            for pid in gone:
                conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
                conn.execute("DELETE FROM playlists WHERE playlist_id = ?", (pid,))
            conn.execute("UPDATE subscriptions SET playlists_at = ? WHERE id = ?", (now, sid))

    def playlist_videos(self) -> str:
        for pid in self._due_playlists(self.clock(), self.playlists_per_run):
            try:
                info = self.gate.call("listing", self.client.read_playlist, _playlist_raw_id(pid))
            except ytgate.Paused:
                return "paused"
            except ytgate.BotCheck:
                return "bot-check"
            except ytgate.RequestFailed as e:
                with self.index.write() as conn:
                    conn.execute("UPDATE playlists SET error = ?, videos_at = ? WHERE playlist_id = ?",
                                 (str(e), _iso(self.clock()), pid))
                continue
            now = _iso(self.clock())
            with self.index.write() as conn:
                if conn.execute("SELECT 1 FROM playlists WHERE playlist_id = ?",
                                (pid,)).fetchone() is None:
                    continue
                ids, new = store.upsert_videos(conn, info.videos, now)
                self._store_playlist(conn, pid, info, ids, now, channel_subscription_id=None)
            new_set = set(new)
            self._after(pid, [], [v for v, i in zip(info.videos, ids) if i in new_set],
                        playlist=info.title)
        return "ok"

    def thumbnail(self, row) -> None:
        url = channel_url(row["channel_id"]) if row["channel_id"] else row["url"]
        info = self.gate.call("thumb", self.client.read_channel, url)
        now = _iso(self.clock())
        with self.index.write() as conn:
            conn.execute(
                "UPDATE subscriptions SET thumbnail = COALESCE(?, thumbnail), thumbnail_at = ?,"
                " thumbnail_error = CASE WHEN ? IS NULL THEN 'no picture' ELSE NULL END,"
                " title = COALESCE(title, ?) WHERE id = ?",
                (info.thumbnail, now, info.thumbnail, info.title, row["id"]))
