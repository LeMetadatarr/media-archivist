# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: which caption kinds were asked for, and what came back.

``subtitles_kinds`` (JSON list, NULL for the default of manual and automatic
tracks together) and ``keep_info_json`` are what was asked of yt-dlp.
``info_json`` is the placed info file when it was kept, and ``captions``
(JSON list) names, for each placed caption file, its language, its kind
(``manual`` or ``automatic``, NULL when unknown) and whether it is a machine
translation.
"""

VERSION = 403
NAME = "download caption kinds"

_COLUMNS = (
    ("subtitles_kinds", "TEXT"),
    ("keep_info_json", "INTEGER NOT NULL DEFAULT 0"),
    ("info_json", "TEXT"),
    ("captions", "TEXT NOT NULL DEFAULT '[]'"),
)


def apply(conn, index) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    for name, decl in _COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {decl}")
