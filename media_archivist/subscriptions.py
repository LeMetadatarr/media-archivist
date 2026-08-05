# SPDX-License-Identifier: Apache-2.0
"""Channel/playlist subscriptions — auto-index new uploads on sync.

A subscription is just a remembered ``(url, backend)`` pair. Syncing a
subscription re-runs the matching :class:`~media_archivist.base.JsonArchivist`
subclass's ``.archive(url)`` against the DB; the archivist itself dedupes
existing rows, so only genuinely new uploads get added. This module doesn't
reimplement any of that — it just remembers *which* URLs to keep re-archiving
and stamps bookkeeping (last-synced-at, rows-added) around each run.

Stored as a ``<db>.subscriptions.json`` sidecar, mirroring the
``<db>.canonical.json`` / ``<db>.quarantine.json`` / ``<db>.entities.json``
sidecar convention in :mod:`media_archivist.canonicalize` /
:mod:`media_archivist.entities`.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

LOG = logging.getLogger("media_archivist.subscriptions")

Backend = str  # "youtube" | "ia" | "music" | "bandcamp" | "soundcloud"

# Ordered so more specific hosts (music.youtube.com) don't get shadowed by
# a broader substring match earlier in the list.
_URL_BACKEND_HINTS: List[tuple] = [
    ("music.youtube.com", "music"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("archive.org", "ia"),
    ("bandcamp.com", "bandcamp"),
    ("soundcloud.com", "soundcloud"),
]

KNOWN_BACKENDS = {"youtube", "ia", "music", "bandcamp", "soundcloud"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def infer_backend(url: str) -> Optional[str]:
    """Guess the backend from a channel/playlist/collection URL, or ``None``."""
    low = (url or "").lower()
    for needle, backend in _URL_BACKEND_HINTS:
        if needle in low:
            return backend
    return None


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: str
    label: Optional[str] = None
    added_at: str = Field(default_factory=_utcnow)
    last_synced_at: Optional[str] = None
    last_rows_added: int = 0
    last_error: Optional[str] = None


class SubscriptionSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions: List[Subscription] = Field(default_factory=list)


class SyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    backend: str
    ok: bool
    rows_added: int = 0
    error: Optional[str] = None
    dry_run: bool = False


def _subscriptions_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".subscriptions.json")


def load_subscriptions(db_path: str) -> SubscriptionSidecar:
    p = _subscriptions_path(db_path)
    if not p.exists():
        return SubscriptionSidecar()
    return SubscriptionSidecar.model_validate(json.loads(p.read_text()))


def save_subscriptions(db_path: str, sidecar: SubscriptionSidecar) -> Path:
    """Atomically write the sidecar next to ``db_path``."""
    p = _subscriptions_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sidecar.model_dump_json(indent=2))
        os.replace(tmp_path, p)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return p


def add_subscription(db_path: str, url: str, *, backend: Optional[str] = None,
                      label: Optional[str] = None) -> Subscription:
    """Add (or update the label of) a subscription; dedupes by URL."""
    url = url.strip()
    if not url:
        raise ValueError("url must not be empty")
    resolved_backend = backend or infer_backend(url)
    if not resolved_backend:
        raise ValueError(
            f"could not infer backend from url {url!r} — pass backend explicitly"
        )
    if resolved_backend not in KNOWN_BACKENDS:
        raise ValueError(f"unknown backend: {resolved_backend!r}")

    sidecar = load_subscriptions(db_path)
    for sub in sidecar.subscriptions:
        if sub.url == url:
            # Already subscribed — update backend/label in place rather than
            # duplicating, so re-running `subscribe` is idempotent.
            sub.backend = resolved_backend
            if label is not None:
                sub.label = label
            save_subscriptions(db_path, sidecar)
            return sub

    sub = Subscription(url=url, backend=resolved_backend, label=label)
    sidecar.subscriptions.append(sub)
    save_subscriptions(db_path, sidecar)
    return sub


def remove_subscription(db_path: str, url: str) -> bool:
    """Remove the subscription for *url*; return whether one was removed."""
    sidecar = load_subscriptions(db_path)
    before = len(sidecar.subscriptions)
    sidecar.subscriptions = [s for s in sidecar.subscriptions if s.url != url]
    if len(sidecar.subscriptions) == before:
        return False
    save_subscriptions(db_path, sidecar)
    return True


def list_subscriptions(db_path: str) -> List[Subscription]:
    return load_subscriptions(db_path).subscriptions


_BACKEND_TO_CLS_FACTORY = {
    "youtube": lambda: __import__(
        "media_archivist.youtube", fromlist=["YoutubeArchivist"]
    ).YoutubeArchivist,
    "ia": lambda: __import__(
        "media_archivist.ia", fromlist=["IAArchivist"]
    ).IAArchivist,
    "music": lambda: __import__(
        "media_archivist.music", fromlist=["YoutubeMusicArchivist"]
    ).YoutubeMusicArchivist,
    "bandcamp": lambda: __import__(
        "media_archivist.bandcamp", fromlist=["BandcampArchivist"]
    ).BandcampArchivist,
    "soundcloud": lambda: __import__(
        "media_archivist.soundcloud", fromlist=["SoundCloudArchivist"]
    ).SoundCloudArchivist,
}


def _archivist_class(backend: str):
    factory = _BACKEND_TO_CLS_FACTORY.get(backend)
    if factory is None:
        raise ValueError(f"unknown backend: {backend!r}")
    return factory()


def sync_subscription(db_path: str, sub: Subscription, *,
                       dry_run: bool = False) -> SyncResult:
    """Run the matching Archivist's ``.archive(sub.url)`` for one subscription.

    Never raises — errors are captured on the returned :class:`SyncResult`
    (and stamped onto *sub*) so one broken subscription can't abort a
    ``sync_all`` sweep. ``dry_run=True`` reports what backend/url would be
    synced without archiving anything or touching the sidecar.
    """
    if dry_run:
        return SyncResult(url=sub.url, backend=sub.backend, ok=True, dry_run=True)

    try:
        cls = _archivist_class(sub.backend)
    except Exception as e:
        sub.last_error = str(e)
        return SyncResult(url=sub.url, backend=sub.backend, ok=False, error=str(e))

    try:
        archivist = cls(db_path=db_path)
        before = len(archivist.video_urls)
        archivist.archive(sub.url)
        after = len(archivist.video_urls)
        rows_added = max(0, after - before)
    except Exception as e:
        LOG.exception("sync_subscription: %s (%s) failed", sub.url, sub.backend)
        sub.last_error = str(e)
        sub.last_synced_at = _utcnow()
        return SyncResult(url=sub.url, backend=sub.backend, ok=False, error=str(e))

    sub.last_error = None
    sub.last_synced_at = _utcnow()
    sub.last_rows_added = rows_added
    return SyncResult(url=sub.url, backend=sub.backend, ok=True, rows_added=rows_added)


def sync_all(db_path: str, *, dry_run: bool = False) -> List[SyncResult]:
    """Sync every stored subscription; return one :class:`SyncResult` per sub.

    Persists updated ``last_synced_at``/``last_rows_added``/``last_error``
    back to the sidecar (unless ``dry_run``, which touches nothing).
    """
    sidecar = load_subscriptions(db_path)
    results: List[SyncResult] = []
    for sub in sidecar.subscriptions:
        results.append(sync_subscription(db_path, sub, dry_run=dry_run))
    if not dry_run:
        save_subscriptions(db_path, sidecar)
    return results
