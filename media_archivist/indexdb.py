# SPDX-License-Identifier: Apache-2.0
"""The SQLite working index beside the JSON database.

The JSON database (``<db>.json``) is the archive and the export format: the
raw rows, diff-friendly, published as datasets. The working index
(``<db>.index.sqlite3``) holds what a running server needs to answer
queries and to remember its own state: which subscriptions list which
videos, when each video was first seen, back-off pauses, settings,
download and export records. It can be rebuilt from the JSON database plus
a re-index; nothing in it is the only copy of a video's metadata.

Connections
    One connection per thread (thread-local), in WAL mode with a busy
    timeout, so readers never block the writer. Every write goes through
    :meth:`IndexDB.write`, which takes ``BEGIN IMMEDIATE``: the write lock
    is held from the first statement, so a read-modify-write inside it can
    never lose an update to another thread or process. Inside one process
    writers are also queued on a lock, so they wait without spinning on
    SQLite's busy handler.

Migrations
    Each schema step is a module ``m<NNNN>_<name>.py`` in
    :mod:`media_archivist.index_migrations` with ``VERSION``, ``NAME`` and
    ``apply(conn, index)``. Steps run once each, in ascending ``VERSION``
    order, inside one write transaction per step, and are recorded in the
    ``migrations`` table; a step already recorded is never run again. A
    step whose ``apply`` returns ``False`` could not run yet (its input is
    unreadable): its writes are rolled back, it is left unrecorded, no later
    step runs in that start, and it is tried again on the next start.
    Version numbers are allocated in blocks so separate pieces of work
    never pick the same number:

    ======== ==========================================================
    1-99     foundation (this schema, the JSON import)
    100-199  YouTube client, per-kind back-off, IP family, settings
    200-299  subscriptions, entries index, query API
    300-399  ``.strm`` exports, Jellyfin, signed links
    400-499  downloads
    ======== ==========================================================

Schema version 1
    Times are ISO 8601 UTC text unless noted; flags are 0/1 integers.

    ``entries_meta``
        ``entry_id`` TEXT PK (the opaque entry id,
        :func:`media_archivist.models.canonical.stable_id`), ``key`` TEXT
        UNIQUE (``youtube:<videoId>``; other sources ``<source>:<url>``),
        ``url``, ``first_seen`` (when the index first saw the entry),
        ``duration`` REAL seconds, ``published`` (``YYYY-MM-DD``),
        ``channel_id``, ``channel``, ``vertical`` (1 for a Short, NULL when
        unknown), ``metadata_checked`` and ``network_failed_at`` (the last
        full yt-dlp read and the last read that failed on the network).
    ``subscriptions``
        ``id`` TEXT PK (``youtube:channel:UC…``, ``youtube:playlist:PL…``;
        see :func:`subscription_id_for_url`), ``url``, ``backend``,
        ``kind`` (``channel``, ``playlist`` or ``other``), ``channel_id``,
        ``title``, ``label``, ``interval_hours`` REAL, ``index_playlists``
        (default 1), ``export_automatic`` (default 0), ``export_target``,
        ``export_layout`` (default ``movies``), ``last_feed_at``,
        ``last_listing_at``, ``first_indexed_at``, ``failures`` (default
        0), ``last_error``, ``flagged`` (default 0), ``created_at``.
    ``subscription_holders``
        (``subscription_id``, ``app``) PK, ``added_at``: the apps holding a
        subscription; ``user`` is the Web UI and the CLI.
    ``memberships``
        (``subscription_id``, ``entry_id``) PK, ``position`` INTEGER,
        ``first_seen``: which subscription lists which entry, and when it
        first did. Indexed by (``subscription_id``, ``first_seen``) for the
        newest-first query and by ``entry_id``.
    ``playlists``
        ``playlist_id`` TEXT PK (``youtube:playlist:PL…``),
        ``channel_subscription_id``, ``title``, ``thumbnail``,
        ``video_count`` INTEGER, ``refreshed_at`` (the Playlists tab
        listing), ``videos_at`` (its video ids), ``error``.
    ``playlist_items``
        (``playlist_id``, ``entry_id``) PK, ``position`` INTEGER; indexed by
        (``playlist_id``, ``position``).
    ``backoff``
        ``kind`` TEXT PK, ``retry_at`` REAL, ``wait_s`` REAL, ``at`` REAL
        (epoch seconds): the bot-check pause of one kind of YouTube request
        (see :mod:`media_archivist.backoff`).
    ``settings``
        ``key`` TEXT PK, ``value`` TEXT (JSON), ``updated_at``
        (see :mod:`media_archivist.settings`).
    ``downloads``
        ``id`` TEXT PK, ``app``, ``entry_id``, ``target_dir``,
        ``filename_template``, ``status``, ``progress`` REAL, ``error``,
        ``created_at``, ``updated_at``, ``result_path``; indexed by (``app``,
        ``status``, ``created_at``).
    ``exports``
        ``id`` TEXT PK, ``scope_kind`` (``entry``, ``playlist``,
        ``subscription``), ``scope_id``, ``target_dir``, ``layout``,
        ``written`` TEXT (JSON list of paths), ``created_at``; indexed by
        (``scope_kind``, ``scope_id``, ``target_dir``).

    ``entries_meta`` is also indexed by ``duration`` and ``first_seen``.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import re
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Union

LOG = logging.getLogger("media_archivist.indexdb")

SUFFIX = ".index.sqlite3"
BUSY_TIMEOUT_S = 30.0

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS entries_meta (
    entry_id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    url TEXT,
    first_seen TEXT NOT NULL,
    duration REAL,
    published TEXT,
    channel_id TEXT,
    channel TEXT,
    vertical INTEGER,
    metadata_checked TEXT,
    network_failed_at TEXT
);
CREATE INDEX IF NOT EXISTS entries_meta_duration ON entries_meta (duration);
CREATE INDEX IF NOT EXISTS entries_meta_first_seen ON entries_meta (first_seen);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    backend TEXT NOT NULL DEFAULT 'youtube',
    kind TEXT NOT NULL,
    channel_id TEXT,
    title TEXT,
    label TEXT,
    interval_hours REAL,
    index_playlists INTEGER NOT NULL DEFAULT 1,
    export_automatic INTEGER NOT NULL DEFAULT 0,
    export_target TEXT,
    export_layout TEXT NOT NULL DEFAULT 'movies',
    last_feed_at TEXT,
    last_listing_at TEXT,
    first_indexed_at TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    flagged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription_holders (
    subscription_id TEXT NOT NULL,
    app TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, app)
);

CREATE TABLE IF NOT EXISTS memberships (
    subscription_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    position INTEGER,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (subscription_id, entry_id)
);
CREATE INDEX IF NOT EXISTS memberships_by_first_seen
    ON memberships (subscription_id, first_seen);
CREATE INDEX IF NOT EXISTS memberships_by_entry ON memberships (entry_id);

CREATE TABLE IF NOT EXISTS playlists (
    playlist_id TEXT PRIMARY KEY,
    channel_subscription_id TEXT,
    title TEXT,
    thumbnail TEXT,
    video_count INTEGER,
    refreshed_at TEXT,
    videos_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS playlists_by_channel ON playlists (channel_subscription_id);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    position INTEGER,
    PRIMARY KEY (playlist_id, entry_id)
);
CREATE INDEX IF NOT EXISTS playlist_items_by_position ON playlist_items (playlist_id, position);

CREATE TABLE IF NOT EXISTS backoff (
    kind TEXT PRIMARY KEY,
    retry_at REAL NOT NULL,
    wait_s REAL NOT NULL,
    at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY,
    app TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    target_dir TEXT NOT NULL,
    filename_template TEXT,
    status TEXT NOT NULL,
    progress REAL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    result_path TEXT
);
CREATE INDEX IF NOT EXISTS downloads_by_app ON downloads (app, status, created_at);

CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    target_dir TEXT NOT NULL,
    layout TEXT NOT NULL,
    written TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS exports_by_scope ON exports (scope_kind, scope_id, target_dir);
"""

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

_CHANNEL_ID = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})(?![0-9A-Za-z_-])")
_PLAYLIST_ID = re.compile(r"[?&]list=([0-9A-Za-z_-]+)")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def index_path(db_path: Union[str, Path]) -> Path:
    """``talks.json`` → ``talks.index.sqlite3``, beside the JSON database.

    Only a ``.json`` suffix is replaced; any other name gets the suffix
    appended, so ``talks.v1`` and ``talks.v2`` keep separate indexes.
    """
    p = Path(db_path)
    if p.suffix == ".json":
        return p.with_suffix(SUFFIX)
    return p.with_name(p.name + SUFFIX)


def subscription_id_for_url(url: str, backend: str = "youtube") -> str:
    """The stable subscription id a URL resolves to without a network request.

    A YouTube ``/channel/UC…`` address is ``youtube:channel:UC…`` and any
    address with ``list=`` is ``youtube:playlist:<id>``. Anything else (a
    handle, another backend) is ``<backend>:url:<url>`` until it is resolved.
    """
    url = url.strip()
    if backend == "youtube":
        m = _PLAYLIST_ID.search(url)
        if m:
            return f"youtube:playlist:{m[1]}"
        m = _CHANNEL_ID.search(url)
        if m:
            return f"youtube:channel:{m[1]}"
    return f"{backend}:url:{url}"


def subscription_kind(subscription_id: str) -> str:
    if subscription_id.startswith("youtube:channel:"):
        return "channel"
    if subscription_id.startswith("youtube:playlist:"):
        return "playlist"
    return "other"


class _Connection(sqlite3.Connection):
    """A plain connection that can be weakly referenced."""


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch to WAL, retrying while another connection holds the file.

    Changing the journal mode needs the file to itself and SQLite does not
    run the busy handler for it, so a connection opening a fresh index while
    another one is creating it gets "database is locked" at once.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_S
    delay = 0.01
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() >= deadline:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 0.5)


class _Deferred(Exception):
    """A step's input is not readable yet: undo its writes, run nothing after it."""


class Migration:
    def __init__(self, version: int, name: str, apply) -> None:
        self.version = version
        self.name = name
        self.apply = apply


def load_migrations() -> List[Migration]:
    """Every step in :mod:`media_archivist.index_migrations`, by version."""
    from media_archivist import index_migrations

    steps: Dict[int, Migration] = {}
    for info in pkgutil.iter_modules(index_migrations.__path__):
        if not info.name.startswith("m"):
            continue
        mod = importlib.import_module(f"{index_migrations.__name__}.{info.name}")
        version = int(mod.VERSION)
        if version in steps:
            raise RuntimeError(f"index migration {version} is defined twice "
                               f"({steps[version].name}, {mod.NAME})")
        steps[version] = Migration(version, mod.NAME, mod.apply)
    return [steps[v] for v in sorted(steps)]


class IndexDB:
    """The working index for the JSON database at ``db_path``."""

    def __init__(self, db_path: Union[str, Path], *, path: Optional[Union[str, Path]] = None,
                 migrate: bool = True) -> None:
        self.json_path = Path(db_path)
        self.path = Path(path) if path is not None else index_path(db_path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._conns: "weakref.WeakSet[_Connection]" = weakref.WeakSet()
        self._conns_lock = threading.Lock()
        if migrate:
            self.migrate()

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_S,
                                   isolation_level=None, check_same_thread=False,
                                   factory=_Connection)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
                _enable_wal(conn)
                conn.execute("PRAGMA synchronous = NORMAL")
            except BaseException:
                conn.close()
                raise
            self._local.conn = conn
            with self._conns_lock:
                self._conns.add(conn)
        return conn

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        """Run one statement outside a write transaction (reads)."""
        return self.conn.execute(sql, params)

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, ``BEGIN IMMEDIATE`` to ``COMMIT``.

        Reads made inside it see the state the write is based on, and no
        other writer, in this process or another, can commit in between.
        A nested call on the same thread is a savepoint inside the outer
        transaction: if it raises, its own changes are undone, even when the
        caller catches the exception and the outer transaction commits.
        """
        conn = self.conn
        with self._write_lock:
            if conn.in_transaction:
                depth = getattr(self._local, "depth", 0) + 1
                self._local.depth = depth
                name = f"nested_{depth}"
                conn.execute(f"SAVEPOINT {name}")
                try:
                    yield conn
                except BaseException:
                    # A conflict clause such as OR ROLLBACK may already have
                    # ended the whole transaction; the error is then the news.
                    if conn.in_transaction:
                        conn.execute(f"ROLLBACK TO {name}")
                        conn.execute(f"RELEASE {name}")
                    raise
                else:
                    conn.execute(f"RELEASE {name}")
                finally:
                    self._local.depth = depth - 1
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def migrate(self) -> List[int]:
        """Apply every step not yet recorded; returns the versions applied."""
        with self.write() as conn:
            conn.execute(_MIGRATIONS_TABLE)
        applied = []
        for step in load_migrations():
            try:
                with self.write() as conn:
                    if conn.execute("SELECT 1 FROM migrations WHERE version = ?",
                                    (step.version,)).fetchone():
                        continue
                    if step.apply(conn, self) is False:
                        raise _Deferred
                    conn.execute("INSERT INTO migrations (version, name, applied_at)"
                                 " VALUES (?, ?, ?)", (step.version, step.name, utcnow()))
            except _Deferred:
                LOG.warning("index %s: migration %d (%s) deferred to the next start; "
                            "later steps wait for it", self.path, step.version, step.name)
                break
            LOG.info("index %s: applied migration %d (%s)", self.path, step.version, step.name)
            applied.append(step.version)
        return applied

    def migrations(self) -> List[Dict[str, object]]:
        rows = self.execute("SELECT version, name, applied_at FROM migrations ORDER BY version")
        return [dict(r) for r in rows]

    @property
    def schema_version(self) -> int:
        row = self.execute("SELECT MAX(version) FROM migrations").fetchone()
        return int(row[0] or 0)

    def close(self) -> None:
        """Close every connection this index opened, in any thread."""
        with self._conns_lock:
            conns = list(self._conns)
            self._conns = weakref.WeakSet()
        for conn in conns:
            conn.close()
        self._local = threading.local()


_open: Dict[str, IndexDB] = {}
_open_lock = threading.Lock()


def open_index(db_path: Union[str, Path]) -> IndexDB:
    """The process-wide :class:`IndexDB` for ``db_path``, migrated on first open."""
    key = str(index_path(db_path).resolve())
    with _open_lock:
        index = _open.get(key)
        if index is None:
            index = _open[key] = IndexDB(db_path)
        return index


def close_all() -> None:
    """Close and forget every index :func:`open_index` returned."""
    with _open_lock:
        indexes = list(_open.values())
        _open.clear()
    for index in indexes:
        index.close()
