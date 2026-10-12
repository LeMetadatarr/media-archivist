# SPDX-License-Identifier: Apache-2.0
"""Server settings an operator or an app changes at run time.

Values live in the ``settings`` table of the working index as JSON, so a
change outlives a restart. A key that was never set reads as its default.
Only the keys in :data:`DEFAULTS` exist; each has a validator that returns
the value to store or raises ``ValueError``.

Environment settings (library and download roots, the Jellyfin connection,
the link secret) are not stored here.
"""
from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, Mapping

from media_archivist.indexdb import utcnow

IP_FAMILIES = ("ipv4", "ipv6", "auto")


def _ip_family(value: Any) -> str:
    if value not in IP_FAMILIES:
        raise ValueError(f"youtube_ip_family must be one of {', '.join(IP_FAMILIES)}")
    return value


def _seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ValueError("expected a number of seconds, 0 or more")
    return value


DEFAULTS: Dict[str, Any] = {
    "youtube_ip_family": "ipv4",
    "youtube_request_spacing": 10,
}

VALIDATORS: Dict[str, Callable[[Any], Any]] = {
    "youtube_ip_family": _ip_family,
    "youtube_request_spacing": _seconds,
}


def _known(key: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")


class Settings:
    def __init__(self, index) -> None:
        self.index = index

    def get(self, key: str) -> Any:
        _known(key)
        row = self.index.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else DEFAULTS[key]

    def all(self) -> Dict[str, Any]:
        stored = {r["key"]: json.loads(r["value"])
                  for r in self.index.execute("SELECT key, value FROM settings")}
        return {key: stored.get(key, default) for key, default in DEFAULTS.items()}

    def set(self, key: str, value: Any) -> Any:
        return self.update({key: value})[key]

    def update(self, values: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate every value first, then store them together."""
        clean = {}
        for key, value in values.items():
            _known(key)
            clean[key] = VALIDATORS[key](value)
        now = utcnow()
        with self.index.write() as conn:
            conn.executemany(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                [(k, json.dumps(v), now) for k, v in clean.items()])
        return self.all()

    def reset(self, key: str) -> None:
        """Forget a stored value so the key reads as its default again."""
        _known(key)
        with self.index.write() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def for_db(db_path) -> Settings:
    from media_archivist.indexdb import open_index

    return Settings(open_index(db_path))
