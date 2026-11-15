# SPDX-License-Identifier: Apache-2.0
"""What the subscription index and the entries query need beyond schema v1.

``entries_meta`` gains ``title`` and ``thumbnail``, so a query answers from
the index alone, and an index on (``first_seen``, ``entry_id``), the order
the query pages in. ``subscriptions`` gains ``thumbnail`` with
``thumbnail_at`` and ``thumbnail_error`` (the last attempt to read the
picture and why it failed), ``playlists_at`` (the last read of a channel's
Playlists tab) and ``listing_requested`` (a full listing asked for by hand).
``playlists`` gains ``position`` (its place on the channel's Playlists tab)
and ``thumbnail_at``. A subscription whose id is a channel id gets that id
in ``channel_id`` too, so its feed can be read before its next listing, and
a YouTube subscription imported under its URL (a handle) gets the kind its
URL names, ``channel`` or ``playlist``, so the indexer resolves it.
"""

VERSION = 200
NAME = "subscription index columns"

STATEMENTS = (
    "ALTER TABLE entries_meta ADD COLUMN title TEXT",
    "ALTER TABLE entries_meta ADD COLUMN thumbnail TEXT",
    "CREATE INDEX IF NOT EXISTS entries_meta_page ON entries_meta (first_seen, entry_id)",
    "ALTER TABLE subscriptions ADD COLUMN thumbnail TEXT",
    "ALTER TABLE subscriptions ADD COLUMN thumbnail_at TEXT",
    "ALTER TABLE subscriptions ADD COLUMN thumbnail_error TEXT",
    "ALTER TABLE subscriptions ADD COLUMN playlists_at TEXT",
    "ALTER TABLE subscriptions ADD COLUMN listing_requested INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE playlists ADD COLUMN position INTEGER",
    "ALTER TABLE playlists ADD COLUMN thumbnail_at TEXT",
    "UPDATE subscriptions SET channel_id = substr(id, 17)"
    " WHERE id LIKE 'youtube:channel:%' AND channel_id IS NULL",
)


def apply(conn, index) -> None:
    from media_archivist.subscriptions import youtube_kind

    for statement in STATEMENTS:
        conn.execute(statement)
    rows = conn.execute("SELECT id, url FROM subscriptions WHERE backend = 'youtube'"
                        " AND kind = 'other'").fetchall()
    for row in rows:
        kind = youtube_kind(row["url"])
        if kind:
            conn.execute("UPDATE subscriptions SET kind = ? WHERE id = ?", (kind, row["id"]))
