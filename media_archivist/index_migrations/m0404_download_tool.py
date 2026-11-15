# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: which program fetches, the root, and what a fetch for a dataset wrote.

``tool`` is ``yt-dlp`` (every earlier row) or ``tutubo``. ``root_dir`` is the
download root the target folder was chosen in. ``fetch_options`` (JSON) is
what a ``tutubo`` download asks for: audio, captions, caption kinds and
formats. ``fetch_record`` (JSON) is what it placed: whether it is complete,
what is missing, and each file with its role, size and SHA-256.
"""

VERSION = 404
NAME = "download tool"

_COLUMNS = (
    ("tool", "TEXT NOT NULL DEFAULT 'yt-dlp'"),
    ("root_dir", "TEXT"),
    ("fetch_options", "TEXT"),
    ("fetch_record", "TEXT"),
)


def apply(conn, index) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    for name, decl in _COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {decl}")
