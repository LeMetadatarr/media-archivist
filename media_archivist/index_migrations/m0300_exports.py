# SPDX-License-Identifier: Apache-2.0
"""Export records, Jellyfin collections, a subscription's link kind.

``exports`` gains what an export needs to be shown and undone: ``app``
(the caller's label), ``links`` (``plain`` or ``signed``), ``files`` (JSON
object, each written path to the SHA-256 of what was written there),
``dirs`` (JSON list of the folders the export created), ``entries`` (JSON
list of the exported entry ids), ``skipped`` (JSON list of
``{"key", "reason"}``), ``posters`` (JSON object ``pending``, ``written``,
``failed``), ``jellyfin`` (JSON object ``refresh``, ``collection``),
``automatic`` (1 for the record automatic exports of a subscription add
to) and ``updated_at``. ``written`` stays the list of paths.

``jellyfin_collections`` holds the Jellyfin collections media-archivist
made, one per playlist: ``playlist_id`` TEXT PK, ``jellyfin_id``, ``name``,
``created_at``, ``updated_at``. A collection not listed here is never
changed.

``subscriptions`` gains ``export_links`` (default ``plain``): the kind of
link an automatic export writes.
"""

VERSION = 300
NAME = "exports, jellyfin collections, export links"

_EXPORT_COLUMNS = (
    "app TEXT",
    "links TEXT NOT NULL DEFAULT 'plain'",
    "files TEXT NOT NULL DEFAULT '{}'",
    "dirs TEXT NOT NULL DEFAULT '[]'",
    "entries TEXT NOT NULL DEFAULT '[]'",
    "skipped TEXT NOT NULL DEFAULT '[]'",
    "posters TEXT NOT NULL DEFAULT '{}'",
    "jellyfin TEXT NOT NULL DEFAULT '{}'",
    "automatic INTEGER NOT NULL DEFAULT 0",
    "updated_at TEXT",
)


def apply(conn, index) -> None:
    for column in _EXPORT_COLUMNS:
        conn.execute(f"ALTER TABLE exports ADD COLUMN {column}")
    conn.execute("ALTER TABLE subscriptions ADD COLUMN export_links TEXT NOT NULL DEFAULT 'plain'")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jellyfin_collections (
            playlist_id TEXT PRIMARY KEY,
            jellyfin_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
