# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: what each placed file held.

``file_digests`` (JSON object) maps every file a finished download placed
to the SHA-256 of its content, so forgetting the download with its files
removes a file only while it is still the one that was downloaded.
"""

VERSION = 401
NAME = "download file digests"


def apply(conn, index) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    if "file_digests" not in have:
        conn.execute("ALTER TABLE downloads ADD COLUMN file_digests TEXT NOT NULL DEFAULT '{}'")
