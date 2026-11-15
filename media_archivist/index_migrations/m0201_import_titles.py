# SPDX-License-Identifier: Apache-2.0
"""Copy each row's title and picture from the JSON database into the index, once.

Rows the index does not know yet are left to the next listing. A value the
index already holds is kept. When the JSON database cannot be read, nothing
is copied and the step is left to run on the next start.
"""
from media_archivist.index_migrations.m0002_import_json import _read_entries
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source

VERSION = 201
NAME = "import titles"


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def apply(conn, index):
    entries = _read_entries(index.json_path)
    if entries is None:
        return False
    rows = []
    for url, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        try:
            source = Source(raw.get("source"))
        except (TypeError, ValueError):
            continue
        title, thumbnail = _text(raw.get("title")), _text(raw.get("thumbnail"))
        if title or thumbnail:
            rows.append((title, thumbnail, stable_id(source, url)))
    conn.executemany(
        "UPDATE entries_meta SET title = COALESCE(title, ?), thumbnail = COALESCE(thumbnail, ?)"
        " WHERE entry_id = ?", rows)
    return True
