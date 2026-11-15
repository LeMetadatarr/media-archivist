# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: the key of each placement in progress.

While a ``tutubo`` download places its files, ``download_placements`` holds
a random key for the folder and video. The placement's journal in the folder
carries an HMAC made with that key, so recovery after a crash acts only on a
journal this server wrote for that placement; the key is removed once the
placement is kept or undone.
"""

VERSION = 405
NAME = "download placements"


def apply(conn, index) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS download_placements ("
        " target_dir TEXT NOT NULL,"
        " video_id TEXT NOT NULL,"
        " key TEXT NOT NULL,"
        " PRIMARY KEY (target_dir, video_id))")
