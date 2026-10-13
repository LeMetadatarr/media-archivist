# SPDX-License-Identifier: Apache-2.0
"""Channel/playlist subscriptions: the CLI's sidecar and the working index.

The sidecar (``<db>.subscriptions.json``, beside the ``.canonical.json`` /
``.quarantine.json`` / ``.entities.json`` sidecars) is a list of remembered
``(url, backend)`` pairs. Syncing one re-runs the matching
:class:`~media_archivist.base.JsonArchivist` subclass's ``.archive(url)``
against the database; the archivist dedupes existing rows, so only new
uploads are added, and this module stamps the bookkeeping (last-synced-at,
rows-added) around each run.

The working index holds the subscriptions of the app API: a stable id per
channel or playlist, the apps holding it, its export settings and the state
of its indexing (see the second half of this module and
:mod:`media_archivist.ytindex`). A YouTube subscription added to the
sidecar is also held in the index by ``user`` once a server has created the
index, and removing it from the sidecar releases that hold.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

LOG = logging.getLogger("media_archivist.subscriptions")

Backend = str  # "youtube" | "ia" | "music" | "bandcamp" | "soundcloud"

# Ordered so more specific hosts (music.youtube.com) don't get shadowed by
# a broader substring match earlier in the list.
_URL_BACKEND_HINTS: List[tuple] = [
    ("music.youtube.com", "music"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("archive.org", "ia"),
    ("bandcamp.com", "bandcamp"),
    ("soundcloud.com", "soundcloud"),
]

KNOWN_BACKENDS = {"youtube", "ia", "music", "bandcamp", "soundcloud"}

DEFAULT_INTERVAL_HOURS = 6.0
MAX_INTERVAL_HOURS = 24 * 366
_ENV_INTERVAL = "MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS"

# Serialises load-modify-save of the sidecar within one process (the
# server's scheduled syncs and its API handlers).
_sidecar_lock = threading.RLock()

# URLs whose sync is running in a thread right now. A thread cannot be
# stopped, so a sync the scheduler gave up on stays listed until it ends.
_syncing: set = set()
_syncing_lock = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def infer_backend(url: str) -> Optional[str]:
    """Guess the backend from a channel/playlist/collection URL, or ``None``."""
    low = (url or "").lower()
    for needle, backend in _URL_BACKEND_HINTS:
        if needle in low:
            return backend
    return None


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: str
    label: Optional[str] = None
    added_at: str = Field(default_factory=_utcnow)
    last_synced_at: Optional[str] = None
    last_rows_added: int = 0
    last_error: Optional[str] = None
    # Back-compat: absent in older sidecars, which pydantic defaults to
    # False on load — no migration needed.
    auto_download: bool = False
    # Hours between scheduled syncs in ``serve``; None uses the default
    # (MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS, else 6).
    interval_hours: Optional[float] = Field(default=None, gt=0)


class SubscriptionSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions: List[Subscription] = Field(default_factory=list)


class SyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: str
    ok: bool
    rows_added: int = 0
    error: Optional[str] = None
    dry_run: bool = False
    # True when another sync of the same URL was still running.
    skipped: bool = False
    # URLs of the newly-added entries this sync discovered (subset of the
    # archivist's DB keys — see sync_subscription's before/after diff).
    new_urls: List[str] = Field(default_factory=list)
    downloaded: List[str] = Field(default_factory=list)
    download_errors: Dict[str, str] = Field(default_factory=dict)


def _subscriptions_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".subscriptions.json")


def load_subscriptions(db_path: str) -> SubscriptionSidecar:
    p = _subscriptions_path(db_path)
    if not p.exists():
        return SubscriptionSidecar()
    return SubscriptionSidecar.model_validate(json.loads(p.read_text()))


def save_subscriptions(db_path: str, sidecar: SubscriptionSidecar) -> Path:
    """Atomically write the sidecar next to ``db_path``."""
    p = _subscriptions_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sidecar.model_dump_json(indent=2))
        os.replace(tmp_path, p)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return p


def add_subscription(db_path: str, url: str, *, backend: Optional[str] = None,
                      label: Optional[str] = None,
                      auto_download: bool = False,
                      interval_hours: Optional[float] = None) -> Subscription:
    """Add (or update the label of) a subscription; dedupes by URL."""
    url = url.strip()
    if not url:
        raise ValueError("url must not be empty")
    resolved_backend = backend or infer_backend(url)
    if not resolved_backend:
        raise ValueError(
            f"could not infer backend from url {url!r} — pass backend explicitly"
        )
    if resolved_backend not in KNOWN_BACKENDS:
        raise ValueError(f"unknown backend: {resolved_backend!r}")
    if interval_hours is not None and not 0 < interval_hours <= MAX_INTERVAL_HOURS:
        raise ValueError(f"interval_hours must be above 0 and at most {MAX_INTERVAL_HOURS}")

    with _sidecar_lock:
        sidecar = load_subscriptions(db_path)
        for sub in sidecar.subscriptions:
            if sub.url == url:
                # Already subscribed — update backend/label in place rather than
                # duplicating, so re-running `subscribe` is idempotent.
                sub.backend = resolved_backend
                if label is not None:
                    sub.label = label
                if auto_download:
                    sub.auto_download = True
                if interval_hours is not None:
                    sub.interval_hours = interval_hours
                save_subscriptions(db_path, sidecar)
                break
        else:
            sub = Subscription(url=url, backend=resolved_backend, label=label,
                                auto_download=auto_download, interval_hours=interval_hours)
            sidecar.subscriptions.append(sub)
            save_subscriptions(db_path, sidecar)
    _mirror_to_index(db_path, url, resolved_backend, label)
    return sub


def remove_subscription(db_path: str, url: str) -> bool:
    """Remove the subscription for *url*; return whether one was removed."""
    with _sidecar_lock:
        sidecar = load_subscriptions(db_path)
        before = len(sidecar.subscriptions)
        sidecar.subscriptions = [s for s in sidecar.subscriptions if s.url != url]
        if len(sidecar.subscriptions) == before:
            return False
        save_subscriptions(db_path, sidecar)
    _release_from_index(db_path, url)
    return True


def list_subscriptions(db_path: str) -> List[Subscription]:
    return load_subscriptions(db_path).subscriptions


def default_interval_hours() -> float:
    try:
        value = float(os.environ.get(_ENV_INTERVAL, DEFAULT_INTERVAL_HOURS))
    except ValueError:
        return DEFAULT_INTERVAL_HOURS
    return value if 0 < value <= MAX_INTERVAL_HOURS else DEFAULT_INTERVAL_HOURS


def interval_of(sub: Subscription) -> timedelta:
    hours = sub.interval_hours or default_interval_hours()
    return timedelta(hours=min(hours, MAX_INTERVAL_HOURS))


def syncing_urls() -> set:
    """URLs with a sync thread still running."""
    with _syncing_lock:
        return set(_syncing)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def next_sync_at(sub: Subscription) -> Optional[datetime]:
    """When the schedule next syncs *sub*; ``None`` means now (never synced)."""
    last = _parse_ts(sub.last_synced_at)
    return None if last is None else last + interval_of(sub)


def due_subscriptions(db_path: str, now: Optional[datetime] = None) -> List[Subscription]:
    """Subscriptions whose interval has passed since their last sync."""
    now = now or datetime.now(timezone.utc)
    due = []
    for sub in list_subscriptions(db_path):
        nxt = next_sync_at(sub)
        if nxt is None or nxt <= now:
            due.append(sub)
    return due


_BACKEND_TO_CLS_FACTORY = {
    "youtube": lambda: __import__(
        "media_archivist.youtube", fromlist=["YoutubeArchivist"]
    ).YoutubeArchivist,
    "ia": lambda: __import__(
        "media_archivist.ia", fromlist=["IAArchivist"]
    ).IAArchivist,
    "music": lambda: __import__(
        "media_archivist.music", fromlist=["YoutubeMusicArchivist"]
    ).YoutubeMusicArchivist,
    "bandcamp": lambda: __import__(
        "media_archivist.bandcamp", fromlist=["BandcampArchivist"]
    ).BandcampArchivist,
    "soundcloud": lambda: __import__(
        "media_archivist.soundcloud", fromlist=["SoundCloudArchivist"]
    ).SoundCloudArchivist,
}


def _archivist_class(backend: str):
    factory = _BACKEND_TO_CLS_FACTORY.get(backend)
    if factory is None:
        raise ValueError(f"unknown backend: {backend!r}")
    return factory()


def _default_downloader(url: str, dest_dir: str) -> None:
    from media_archivist import streams

    streams.download(url, dest_dir)


def sync_subscription(db_path: str, sub: Subscription, *,
                       dry_run: bool = False, download: bool = False,
                       download_dir: Optional[str] = None,
                       downloader: Optional[Callable[[str, str], None]] = None
                       ) -> SyncResult:
    """Run the matching Archivist's ``.archive(sub.url)`` for one subscription.

    Never raises — errors are captured on the returned :class:`SyncResult`
    (and stamped onto *sub*) so one broken subscription can't abort a
    ``sync_all`` sweep. ``dry_run=True`` reports what backend/url would be
    synced without archiving anything, downloading anything, or touching
    the sidecar.

    When ``download`` (or ``sub.auto_download``) is true, every entry the
    ``archive()`` call newly added — determined by diffing the archivist's
    DB keys before vs after the call, never by re-deriving them from
    ``rows_added`` — gets downloaded via *downloader* (defaults to
    :func:`media_archivist.streams.download`) into *download_dir*
    (defaults to :func:`media_archivist.streams.default_download_dir`).
    Downloading is gated on ``streams.ytdlp_available()``: if yt-dlp isn't
    installed, indexing still happens and a warning is logged instead of
    failing the sync. Each entry downloads independently — one failed
    download is recorded in ``SyncResult.download_errors`` and never aborts
    the sync or the remaining downloads.
    """
    if dry_run:
        return SyncResult(url=sub.url, backend=sub.backend, ok=True, dry_run=True)

    try:
        cls = _archivist_class(sub.backend)
    except Exception as e:
        sub.last_error = str(e)
        sub.last_synced_at = _utcnow()
        return SyncResult(url=sub.url, backend=sub.backend, ok=False, error=str(e))

    try:
        archivist = cls(db_path=db_path)
        before = set(archivist.video_urls)
        archivist.archive(sub.url)
        after = set(archivist.video_urls)
        new_urls = sorted(after - before)
        rows_added = len(new_urls)
    except Exception as e:
        LOG.exception("sync_subscription: %s (%s) failed", sub.url, sub.backend)
        sub.last_error = str(e)
        sub.last_synced_at = _utcnow()
        return SyncResult(url=sub.url, backend=sub.backend, ok=False, error=str(e))

    sub.last_error = None
    sub.last_synced_at = _utcnow()
    sub.last_rows_added = rows_added
    result = SyncResult(url=sub.url, backend=sub.backend, ok=True,
                         rows_added=rows_added, new_urls=new_urls)

    if rows_added > 0:
        try:
            from media_archivist import notify as notify_mod

            label = sub.label or sub.url
            notify_mod.notify(
                "subscription_sync",
                f"{label}: {rows_added} new item(s) archived",
                {"url": sub.url, "backend": sub.backend, "rows_added": rows_added,
                 "new_urls": new_urls},
            )
        except Exception:
            LOG.exception("sync_subscription: notify failed for %s", sub.url)

    should_download = download or sub.auto_download
    if should_download and new_urls:
        from media_archivist import streams

        if not streams.ytdlp_available():
            LOG.warning(
                "sync_subscription: yt-dlp unavailable — skipping download of "
                "%d new item(s) for %s (still indexed)", len(new_urls), sub.url,
            )
        else:
            dest_dir = str(download_dir or streams.default_download_dir())
            do_download = downloader or _default_downloader
            for new_url in new_urls:
                try:
                    do_download(new_url, dest_dir)
                    result.downloaded.append(new_url)
                except Exception as e:
                    LOG.exception(
                        "sync_subscription: download failed for %s (%s)",
                        new_url, sub.url,
                    )
                    result.download_errors[new_url] = str(e)

    return result


def sync_all(db_path: str, *, dry_run: bool = False, download: bool = False,
             download_dir: Optional[str] = None,
             downloader: Optional[Callable[[str, str], None]] = None
             ) -> List[SyncResult]:
    """Sync every stored subscription; return one :class:`SyncResult` per sub.

    ``download`` forces downloading of newly-added entries for every
    subscription this run, on top of any subscription individually flagged
    ``auto_download=True`` (see :func:`sync_subscription`).

    Persists updated ``last_synced_at``/``last_rows_added``/``last_error``
    back to the sidecar (unless ``dry_run``, which touches nothing).
    """
    sidecar = load_subscriptions(db_path)
    results: List[SyncResult] = []
    for sub in sidecar.subscriptions:
        if dry_run:
            results.append(sync_subscription(db_path, sub, dry_run=True))
            continue
        with _syncing_lock:
            busy = sub.url in _syncing
            if not busy:
                _syncing.add(sub.url)
        if busy:
            results.append(SyncResult(url=sub.url, backend=sub.backend, ok=True, skipped=True))
            continue
        try:
            results.append(sync_subscription(
                db_path, sub, download=download,
                download_dir=download_dir, downloader=downloader,
            ))
            _store_run(db_path, sub)
        finally:
            with _syncing_lock:
                _syncing.discard(sub.url)
    return results


def _store_run(db_path: str, synced: Subscription) -> None:
    """Write *synced*'s last-run fields into the current sidecar.

    Re-reads the sidecar so subscriptions added, edited or removed while
    the sync ran are kept as they are now.
    """
    with _sidecar_lock:
        sidecar = load_subscriptions(db_path)
        for sub in sidecar.subscriptions:
            if sub.url == synced.url:
                sub.last_synced_at = synced.last_synced_at
                sub.last_rows_added = synced.last_rows_added
                sub.last_error = synced.last_error
                save_subscriptions(db_path, sidecar)
                return


def sync_one(db_path: str, url: str, *, download: bool = False,
             download_dir: Optional[str] = None,
             downloader: Optional[Callable[[str, str], None]] = None) -> SyncResult:
    """Sync the stored subscription for *url* and persist its last run."""
    sub = next((s for s in list_subscriptions(db_path) if s.url == url), None)
    if sub is None:
        raise ValueError(f"no subscription for {url}")
    with _syncing_lock:
        if url in _syncing:
            return SyncResult(url=sub.url, backend=sub.backend, ok=True, skipped=True)
        _syncing.add(url)
    try:
        result = sync_subscription(db_path, sub, download=download,
                                   download_dir=download_dir, downloader=downloader)
        _store_run(db_path, sub)
    finally:
        with _syncing_lock:
            _syncing.discard(url)
    return result


def watch(db_path: str, *, interval: float, download: bool = False,
          download_dir: Optional[str] = None,
          downloader: Optional[Callable[[str, str], None]] = None,
          stop_event: Optional[threading.Event] = None,
          on_cycle: Optional[Callable[[List[SyncResult]], None]] = None) -> None:
    """Run :func:`sync_all` on a repeating ``interval``, until stopped.

    This is the "periodic scan" story: a curator subscribes once, then a
    long-lived process (foreground CLI daemon, systemd unit, docker
    container — see the ``sync-subscriptions --interval`` CLI command)
    keeps calling this so new uploads keep getting indexed (and optionally
    downloaded) without a human re-running anything.

    Blocking — call it from a dedicated thread (or, for the CLI, the
    foreground) and stop it via *stop_event* (a fresh
    :class:`threading.Event` is created when none is given). Each cycle's
    results are logged; pass *on_cycle* to also observe them
    programmatically (e.g. for tests, or a caller that wants finer-grained
    reporting than the log). Never raises: like :func:`sync_subscription`,
    an exception from one cycle is logged and the loop continues rather
    than dying silently in a background thread.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    event = stop_event if stop_event is not None else threading.Event()
    LOG.info("subscriptions.watch: starting, interval=%ss download=%s", interval, download)
    while not event.is_set():
        try:
            results = sync_all(db_path, download=download, download_dir=download_dir,
                                downloader=downloader)
            for r in results:
                if r.ok:
                    LOG.info("watch: synced %s [%s]: +%d rows, %d downloaded",
                             r.url, r.backend, r.rows_added, len(r.downloaded))
                else:
                    LOG.warning("watch: sync failed %s [%s]: %s", r.url, r.backend, r.error)
            if on_cycle is not None:
                on_cycle(results)
        except Exception:
            LOG.exception("subscriptions.watch: cycle failed")
        # wait() returns True (and we exit) the instant the event is set,
        # rather than sleeping out the full interval after a stop request.
        if event.wait(interval):
            break
    LOG.info("subscriptions.watch: stopped")


# ---------------------------------------------------------------------------
# Subscriptions in the working index: the ``/api/v1`` contract.
#
# A row of the index's ``subscriptions`` table is keyed by a stable id
# (``youtube:channel:UC…``, ``youtube:playlist:PL…``) and held by one or
# more apps. The functions below are the only writers of those rows outside
# the indexer (:mod:`media_archivist.ytindex.engine`).
# ---------------------------------------------------------------------------

FEED_EVERY = timedelta(hours=2)
CHANNEL_LISTING_EVERY = timedelta(days=7)
PLAYLIST_LISTING_EVERY = timedelta(hours=24)
PLAYLISTS_TAB_EVERY = timedelta(hours=24)
PLAYLIST_VIDEOS_EVERY = timedelta(hours=24)
THUMBNAIL_RETRY = timedelta(hours=24)
MAX_FAILURES = 3
EXPORT_LAYOUTS = ("movies", "episodes")
_UNRESOLVED_PREFIX = "youtube:url:"


class SubscriptionNotFound(LookupError):
    """No subscription in the index has that id."""


def youtube_kind(url: str) -> Optional[str]:
    """``channel`` or ``playlist`` for a YouTube URL the index can follow, else ``None``."""
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    host = parsed.netloc.lower()
    if parsed.scheme not in ("http", "https") or not (
            host == "youtube.com" or host.endswith(".youtube.com")) or host.startswith("music."):
        return None
    if "list=" in parsed.query:
        return "playlist"
    parts = [p for p in parsed.path.split("/") if p]
    if parts and (parts[0].startswith("@") or (parts[0] in ("channel", "c", "user") and len(parts) >= 2)):
        return "channel"
    return None


def is_unresolved(subscription_id: str) -> bool:
    return subscription_id.startswith(_UNRESOLVED_PREFIX)


def clean_export(export: Optional[dict], current: Optional[dict] = None) -> Dict[str, object]:
    """An export block with every field, ``export`` merged over ``current``.

    Raises ``ValueError`` when ``automatic`` is on without a ``target_dir``
    or ``layout`` is not one of :data:`EXPORT_LAYOUTS`.
    """
    out = {"automatic": False, "target_dir": None, "layout": "movies"}
    out.update(current or {})
    for key, value in (export or {}).items():
        if key not in out:
            raise ValueError(f"unknown export field {key!r}")
        out[key] = value
    if not isinstance(out["automatic"], bool):
        raise ValueError("export.automatic must be true or false")
    if out["target_dir"] is not None and (not isinstance(out["target_dir"], str)
                                          or not out["target_dir"].strip()):
        raise ValueError("export.target_dir must be a folder path or null")
    if out["layout"] not in EXPORT_LAYOUTS:
        raise ValueError(f"export.layout must be one of {', '.join(EXPORT_LAYOUTS)}")
    if out["automatic"] and not out["target_dir"]:
        raise ValueError("export.target_dir is required when export.automatic is true")
    return out


def _row(conn, subscription_id: str):
    return conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()


def _find_existing(conn, url: str, subscription_id: str):
    return _row(conn, subscription_id) or conn.execute(
        "SELECT * FROM subscriptions WHERE url = ? ORDER BY created_at LIMIT 1", (url,)).fetchone()


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _api_time(value: Optional[str]) -> Optional[str]:
    return _iso(_parse_ts(value))


def next_check_at(row, now: Optional[datetime] = None) -> Optional[datetime]:
    """When the indexer next reads the subscription; ``None`` while it is flagged."""
    if row["flagged"]:
        return None
    now = now or datetime.now(timezone.utc)
    if row["listing_requested"] or not row["first_indexed_at"] or is_unresolved(row["id"]):
        return now
    last_listing = _parse_ts(row["last_listing_at"]) or now
    if row["kind"] == "playlist":
        return last_listing + PLAYLIST_LISTING_EVERY
    nxt = last_listing + CHANNEL_LISTING_EVERY
    if row["channel_id"]:
        last_feed = _parse_ts(row["last_feed_at"])
        nxt = min(nxt, last_feed + FEED_EVERY if last_feed else now)
    return nxt


def _as_api(conn, row) -> Dict[str, object]:
    sid = row["id"]
    holders = [r["app"] for r in conn.execute(
        "SELECT app FROM subscription_holders WHERE subscription_id = ? ORDER BY app", (sid,))]
    count = conn.execute("SELECT COUNT(*) FROM memberships WHERE subscription_id = ?",
                         (sid,)).fetchone()[0]
    return {
        "id": sid,
        "url": row["url"],
        "kind": row["kind"],
        "title": row["title"],
        "label": row["label"],
        "thumbnail": f"/api/v1/subscriptions/{sid}/thumbnail" if row["thumbnail"] else None,
        "holders": holders,
        "index_playlists": bool(row["index_playlists"]) and row["kind"] == "channel",
        "export": {"automatic": bool(row["export_automatic"]),
                   "target_dir": row["export_target"], "layout": row["export_layout"]},
        "entry_count": count,
        "first_indexed_at": _api_time(row["first_indexed_at"]),
        "last_listing_at": _api_time(row["last_listing_at"]),
        "last_feed_at": _api_time(row["last_feed_at"]),
        "next_check_at": _iso(next_check_at(row)),
        "failures": row["failures"],
        "last_error": row["last_error"],
        "flagged": bool(row["flagged"]),
    }


def get_indexed(index, subscription_id: str) -> Dict[str, object]:
    """One subscription as ``GET /api/v1/subscriptions/{id}`` shows it."""
    row = _row(index.conn, subscription_id)
    if row is None:
        raise SubscriptionNotFound(subscription_id)
    return _as_api(index.conn, row)


def list_indexed(index) -> List[Dict[str, object]]:
    rows = index.execute("SELECT * FROM subscriptions ORDER BY created_at, id").fetchall()
    return [_as_api(index.conn, r) for r in rows]


def create_indexed(index, url: str, *, app: str, label: Optional[str] = None,
                   index_playlists: Optional[bool] = None, export: Optional[dict] = None,
                   resolve: Optional[Callable[[str], Optional[str]]] = None) -> tuple:
    """Subscribe ``app`` to a YouTube channel or playlist.

    Returns ``(subscription, created)``. A channel given by handle is
    resolved to its channel id with ``resolve`` (one request; ``None`` or an
    error leaves it to the indexer), so the same channel added by two
    addresses is one subscription. An existing subscription only gains
    ``app`` as a holder; its settings are left as they are.
    """
    from media_archivist.indexdb import subscription_id_for_url, utcnow

    url = (url or "").strip()
    kind = youtube_kind(url)
    if kind is None:
        raise ValueError("url must be a YouTube channel or playlist address")
    clean = clean_export(export)
    sid = subscription_id_for_url(url)
    channel_id = sid.split(":", 2)[2] if sid.startswith("youtube:channel:") else None
    if is_unresolved(sid) and resolve is not None:
        known = _find_existing(index.conn, url, sid)
        if known is None or is_unresolved(known["id"]):
            try:
                channel_id = resolve(url)
            except Exception as e:
                LOG.info("could not resolve %s now (%s); the indexer will", url, e)
                channel_id = None
            if channel_id:
                sid = f"youtube:channel:{channel_id}"
    now = utcnow()
    with index.write() as conn:
        row = _find_existing(conn, url, sid)
        created = row is None
        if created:
            conn.execute(
                "INSERT INTO subscriptions (id, url, backend, kind, channel_id, label,"
                " index_playlists, export_automatic, export_target, export_layout,"
                " listing_requested, created_at) VALUES (?, ?, 'youtube', ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (sid, url, kind, channel_id, label,
                 int(index_playlists is not False and kind == "channel"),
                 int(clean["automatic"]), clean["target_dir"], clean["layout"], now))
        else:
            sid = row["id"]
        conn.execute("INSERT OR IGNORE INTO subscription_holders (subscription_id, app, added_at)"
                     " VALUES (?, ?, ?)", (sid, app, now))
        return _as_api(conn, _row(conn, sid)), created


def update_indexed(index, subscription_id: str, changes: Dict[str, object]) -> Dict[str, object]:
    """Change ``label``, ``index_playlists`` or ``export``; clears ``flagged``."""
    unknown = set(changes) - {"label", "index_playlists", "export"}
    if unknown:
        raise ValueError(f"cannot change {', '.join(sorted(unknown))}")
    with index.write() as conn:
        row = _row(conn, subscription_id)
        if row is None:
            raise SubscriptionNotFound(subscription_id)
        label = changes.get("label", row["label"])
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be text or null")
        index_playlists = changes.get("index_playlists", bool(row["index_playlists"]))
        if not isinstance(index_playlists, bool):
            raise ValueError("index_playlists must be true or false")
        current = {"automatic": bool(row["export_automatic"]), "target_dir": row["export_target"],
                   "layout": row["export_layout"]}
        export = changes.get("export")
        if export is not None and not isinstance(export, dict):
            raise ValueError("export must be an object")
        clean = clean_export(export, current)
        conn.execute(
            "UPDATE subscriptions SET label = ?, index_playlists = ?, export_automatic = ?,"
            " export_target = ?, export_layout = ?, flagged = 0, failures = 0 WHERE id = ?",
            (label, int(index_playlists and row["kind"] == "channel"), int(clean["automatic"]),
             clean["target_dir"], clean["layout"], subscription_id))
        return _as_api(conn, _row(conn, subscription_id))


def release_indexed(index, subscription_id: str, app: str, *, all_holders: bool = False) -> bool:
    """Remove ``app`` (or every app) from the holders; True when the subscription went away.

    A subscription nobody holds stops being indexed. Its entries, the
    record of which of them it listed and its playlists stay in the index,
    so subscribing again later does not show its back catalogue as new.
    """
    with index.write() as conn:
        if _row(conn, subscription_id) is None:
            raise SubscriptionNotFound(subscription_id)
        if all_holders:
            conn.execute("DELETE FROM subscription_holders WHERE subscription_id = ?",
                         (subscription_id,))
        else:
            conn.execute("DELETE FROM subscription_holders WHERE subscription_id = ? AND app = ?",
                         (subscription_id, app))
        left = conn.execute("SELECT COUNT(*) FROM subscription_holders WHERE subscription_id = ?",
                            (subscription_id,)).fetchone()[0]
        if left:
            return False
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
        return True


def request_listing(index, subscription_id: str) -> Dict[str, object]:
    """Ask for a full listing at the next pass; clears ``flagged``."""
    with index.write() as conn:
        cur = conn.execute("UPDATE subscriptions SET listing_requested = 1, flagged = 0,"
                           " failures = 0 WHERE id = ?", (subscription_id,))
        if not cur.rowcount:
            raise SubscriptionNotFound(subscription_id)
        return _as_api(conn, _row(conn, subscription_id))


def rekey_indexed(conn, old_id: str, channel_id: str) -> str:
    """Move a subscription known by its URL to its channel id; returns the new id.

    When the channel is already subscribed under its id, the two become one:
    holders and listed entries are merged and the earlier ``first_seen`` of
    each entry is kept.
    """
    new_id = f"youtube:channel:{channel_id}"
    if new_id == old_id:
        return new_id
    if _row(conn, new_id) is None:
        conn.execute("UPDATE subscriptions SET id = ?, channel_id = ? WHERE id = ?",
                     (new_id, channel_id, old_id))
    else:
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (old_id,))
    conn.execute("INSERT OR IGNORE INTO subscription_holders (subscription_id, app, added_at)"
                 " SELECT ?, app, added_at FROM subscription_holders WHERE subscription_id = ?",
                 (new_id, old_id))
    conn.execute("DELETE FROM subscription_holders WHERE subscription_id = ?", (old_id,))
    conn.execute(
        "INSERT INTO memberships (subscription_id, entry_id, position, first_seen)"
        " SELECT ?, entry_id, position, first_seen FROM memberships WHERE subscription_id = ?"
        " ON CONFLICT(subscription_id, entry_id) DO UPDATE SET"
        " first_seen = MIN(first_seen, excluded.first_seen)", (new_id, old_id))
    conn.execute("DELETE FROM memberships WHERE subscription_id = ?", (old_id,))
    conn.execute("UPDATE playlists SET channel_subscription_id = ? WHERE channel_subscription_id = ?",
                 (new_id, old_id))
    return new_id


def record_failure(conn, subscription_id: str, message: str) -> None:
    """Count a failure other than the bot check; the third in a row flags the subscription."""
    conn.execute(
        "UPDATE subscriptions SET failures = failures + 1, last_error = ?,"
        " flagged = CASE WHEN failures + 1 >= ? THEN 1 ELSE flagged END WHERE id = ?",
        (message, MAX_FAILURES, subscription_id))


def record_success(conn, subscription_id: str) -> None:
    conn.execute("UPDATE subscriptions SET failures = 0, last_error = NULL WHERE id = ?",
                 (subscription_id,))


def _mirror_to_index(db_path: str, url: str, backend: str, label: Optional[str]) -> None:
    """Hold a YouTube sidecar subscription in the working index as ``user``.

    Only when the index exists (a server has opened the database): the CLI
    alone never creates one.
    """
    from media_archivist.indexdb import index_path, open_index

    if backend != "youtube" or youtube_kind(url) is None or not index_path(db_path).is_file():
        return
    try:
        create_indexed(open_index(db_path), url, app="user", label=label)
    except Exception:
        LOG.exception("could not add %s to the working index", url)


def _release_from_index(db_path: str, url: str) -> None:
    from media_archivist.indexdb import index_path, open_index, subscription_id_for_url

    if not index_path(db_path).is_file():
        return
    try:
        index = open_index(db_path)
        row = _find_existing(index.conn, url.strip(), subscription_id_for_url(url))
        if row is not None:
            release_indexed(index, row["id"], "user")
    except Exception:
        LOG.exception("could not remove %s from the working index", url)
