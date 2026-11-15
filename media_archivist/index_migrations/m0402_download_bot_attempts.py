# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: how many runs met the bot check.

``bot_attempts`` counts only the runs of a download that were answered with
YouTube's bot check; a run cut short by a stop or a restart is not one.
``attempts`` keeps counting every run started.
"""

VERSION = 402
NAME = "download bot check count"


def apply(conn, index) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    if "bot_attempts" not in have:
        conn.execute("ALTER TABLE downloads ADD COLUMN bot_attempts INTEGER NOT NULL DEFAULT 0")
