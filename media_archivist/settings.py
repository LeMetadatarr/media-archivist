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

import copy
import json
import math
from typing import Any, Callable, Dict, Mapping

from media_archivist.indexdb import utcnow
from media_archivist import plugins
from media_archivist.plugins import BUILTIN_NAMES

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


def _concurrency(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
        raise ValueError("download_concurrency must be a whole number from 1 to 8")
    return value


STATUS_CARDS = ("on", "off")


def _status_cards(value: Any) -> str:
    if value not in STATUS_CARDS:
        raise ValueError(f"status_cards must be one of {', '.join(STATUS_CARDS)}")
    return value


def _provider_names(value: Any) -> list:
    """A list of installed provider names; the empty list enables none."""
    if not isinstance(value, list) or not all(
            isinstance(v, str) and plugins.NAME.fullmatch(v) for v in value):
        raise ValueError("expected a list of provider names (lowercase letters, digits, - and _)")
    unknown = sorted(set(value) - set(plugins.known_names()))
    if unknown:
        raise ValueError(f"no installed provider named {', '.join(unknown)}")
    return list(dict.fromkeys(value))


DEFAULTS: Dict[str, Any] = {
    "youtube_ip_family": "ipv4",
    "youtube_request_spacing": 10,
    "download_concurrency": 1,
    "status_cards": "on",
    "enabled_providers": list(BUILTIN_NAMES),
    "adult_export_providers": [],
}

VALIDATORS: Dict[str, Callable[[Any], Any]] = {
    "youtube_ip_family": _ip_family,
    "youtube_request_spacing": _seconds,
    "download_concurrency": _concurrency,
    "status_cards": _status_cards,
    "enabled_providers": _provider_names,
    "adult_export_providers": _provider_names,
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
        return json.loads(row["value"]) if row else copy.deepcopy(DEFAULTS[key])

    def all(self) -> Dict[str, Any]:
        stored = {r["key"]: json.loads(r["value"])
                  for r in self.index.execute("SELECT key, value FROM settings")}
        return {key: stored.get(key, copy.deepcopy(default)) for key, default in DEFAULTS.items()}

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


class _DefaultSettings:
    """Settings of a working directory that has no index yet: all defaults."""

    def get(self, key: str) -> Any:
        _known(key)
        return copy.deepcopy(DEFAULTS[key])


def existing(db_path):
    """The settings of ``db_path``'s index, without creating the index."""
    from media_archivist.indexdb import index_path

    return for_db(db_path) if index_path(db_path).is_file() else _DefaultSettings()


def for_db(db_path) -> Settings:
    from media_archivist.indexdb import open_index

    return Settings(open_index(db_path))
