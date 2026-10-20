# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: the columns the queue and its history need.

``seq`` orders the queue (ascending, never reused), ``entry_key`` is the
entry's readable key, ``title`` is shown until the download finishes and
names it afterwards, ``format`` and ``subtitles`` (JSON list) are what was
asked of yt-dlp, ``bytes`` is how much has been fetched, ``files`` (JSON
list) is every file the finished download left in ``target_dir``,
``started_at`` and ``finished_at`` bound the last run, ``error_kind`` is
``permanent``, ``network``, ``tool`` or ``bot`` beside ``error``, and ``attempts``
counts the runs, a bot check included.
"""

VERSION = 400
NAME = "downloads queue"

_COLUMNS = (
    ("seq", "INTEGER"),
    ("entry_key", "TEXT"),
    ("title", "TEXT"),
    ("format", "TEXT"),
    ("subtitles", "TEXT NOT NULL DEFAULT '[]'"),
    ("bytes", "INTEGER"),
    ("files", "TEXT NOT NULL DEFAULT '[]'"),
    ("started_at", "TEXT"),
    ("finished_at", "TEXT"),
    ("error_kind", "TEXT"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
)


def apply(conn, index) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    for name, decl in _COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {decl}")
    conn.execute("UPDATE downloads SET seq = rowid WHERE seq IS NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS downloads_by_seq ON downloads (seq)")
    conn.execute("CREATE INDEX IF NOT EXISTS downloads_by_status ON downloads (status, seq)")
