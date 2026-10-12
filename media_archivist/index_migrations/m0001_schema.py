# SPDX-License-Identifier: Apache-2.0
"""Schema version 1: the tables documented in :mod:`media_archivist.indexdb`."""
from media_archivist.indexdb import SCHEMA_V1

VERSION = 1
NAME = "schema v1"


def apply(conn, index) -> None:
    for statement in SCHEMA_V1.split(";"):
        if statement.strip():
            conn.execute(statement)
