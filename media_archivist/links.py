# SPDX-License-Identifier: Apache-2.0
"""Stream links, plain and signed.

A ``.strm`` file and a player point at ``/api/v1/stream/{id}``. A signed
link carries ``sig`` (and ``exp`` when it expires), so a player outside the
exempt networks plays that one entry without the API key, and the key is
never written into a file.

The signature is HMAC-SHA256 over the entry id exactly as it appears in the
link's path and the expiry (epoch seconds, empty when the link does not
expire), keyed with ``MEDIA_ARCHIVIST_LINK_SECRET``. Without that variable,
a random secret is generated on first use and kept in the index's
``settings`` table under ``link_secret``, which the settings API does not
show. Changing the secret revokes every signed link.

``MEDIA_ARCHIVIST_PUBLIC_URL`` is the address players reach the server at;
links and exports use it in place of the address a request arrived on, and
automatic exports, which have no request, need it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Mapping, Optional
from urllib.parse import quote, urlencode

from media_archivist.indexdb import utcnow

ENV_SECRET = "MEDIA_ARCHIVIST_LINK_SECRET"
ENV_PUBLIC_URL = "MEDIA_ARCHIVIST_PUBLIC_URL"
SETTING_KEY = "link_secret"
STREAM_PREFIX = "/api/v1/stream/"


def _now() -> int:
    return int(time.time())


def secret(index) -> bytes:
    """The signing key: the environment's, else the one stored in ``index``."""
    env = os.environ.get(ENV_SECRET, "")
    if env:
        return env.encode()
    row = index.execute("SELECT value FROM settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    if row is None:
        with index.write() as conn:
            conn.execute("INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                         (SETTING_KEY, json.dumps(secrets.token_urlsafe(32)), utcnow()))
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    return json.loads(row["value"]).encode()


def signature(key: bytes, entry_id: str, exp: Optional[int]) -> str:
    message = f"stream\n{entry_id}\n{'' if exp is None else int(exp)}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify(key: bytes, entry_id: str, sig: Optional[str], exp: Optional[int], *,
           now: Optional[int] = None) -> bool:
    """True when ``sig`` signs ``entry_id`` with ``exp`` and ``exp`` has not passed."""
    if not sig:
        return False
    if exp is not None and (now if now is not None else _now()) > exp:
        return False
    return hmac.compare_digest(sig.encode(), signature(key, entry_id, exp).encode())


def verify_query(index, entry_id: str, query: Mapping[str, str]) -> bool:
    """Whether a request's ``sig`` and ``exp`` parameters open ``entry_id``."""
    sig = query.get("sig")
    raw_exp = query.get("exp")
    if not sig:
        return False
    exp = None
    if raw_exp:
        if not raw_exp.isdigit():
            return False
        exp = int(raw_exp)
    return verify(secret(index), entry_id, sig, exp)


def public_base(fallback: str) -> str:
    """``MEDIA_ARCHIVIST_PUBLIC_URL`` when set, else ``fallback``; no trailing slash."""
    return (os.environ.get(ENV_PUBLIC_URL, "").strip() or fallback).rstrip("/")


def stream_url(base_url: str, entry_id: str, *, key: Optional[bytes] = None,
               expires_at: Optional[int] = None) -> str:
    """The stream link of ``entry_id``; signed when ``key`` is given."""
    url = base_url.rstrip("/") + STREAM_PREFIX + quote(entry_id, safe=":")
    if key is None:
        return url
    params = {"sig": signature(key, entry_id, expires_at)}
    if expires_at is not None:
        params["exp"] = str(int(expires_at))
    return url + "?" + urlencode(params)
