# SPDX-License-Identifier: Apache-2.0
"""Stream health: flag dead/expired ``.strm`` entries and re-resolve them.

media-archivist entries point Jellyfin/Kodi at either a stored direct
``stream`` URL (which can expire — YouTube CDN links in particular) or,
absent one, the source watch/listing URL that a player's own resolver
handles at play time. This module gives curators a way to proactively
find entries whose *stored* direct URL has gone stale (or dead outright)
before the player 404s on it, and to refresh them via
:func:`media_archivist.streams.resolve_stream`.

Nothing here ever raises out to the caller — a probe failure, a timeout,
an unexpected exception from ``requests`` or ``resolve_stream`` all get
captured into the result dataclasses instead. A health scan or
re-resolve attempt must never crash a CLI run or a server request.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Literal, Optional
from urllib.parse import quote

from media_archivist.index import Index
from media_archivist.models.canonical import MediaEntry
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist import streams as _streams

LOG = logging.getLogger("media_archivist.health")

# Bound on concurrent probes issued by check_library() — polite default,
# matches the spirit of the scheduler's single-flight-at-a-time posture
# without serializing an entire large library one HTTP round trip at a time.
_DEFAULT_MAX_WORKERS = 8
_DEFAULT_TIMEOUT = 10.0

# "gone" is a distinct terminal state from "expired": the *source itself*
# (the YouTube video) no longer exists, so re-resolving is pointless — it
# will just fail again. "expired" means only the *stored direct URL* went
# stale while the source is still there and CAN be re-resolved.
HealthStatus = Literal["ok", "dead", "expired", "no-stream", "gone"]

# Sources whose watch/listing URL is always reachable (the page itself
# 200s even for a deleted video) — a plain HTTP probe on the URL can't
# tell "live" from "deleted" for these, so they need the source-aware
# oEmbed check in _check_youtube_entry() instead of _probe_url().
_YOUTUBE_SOURCES = {"youtube", "youtube_music"}
_YOUTUBE_HOST_RE = None  # set below, after re import


def _is_youtube(source: str, url: str) -> bool:
    """True when ``entry`` needs the oEmbed availability check.

    Gated on the declared source first (cheap, no parsing); falls back to
    sniffing the URL's host so a generic/None-source row that happens to
    point at youtube.com still gets the deleted-video-aware check instead
    of a misleading "the watch page 200s so it's ok".
    """
    if source in _YOUTUBE_SOURCES:
        return True
    import re

    global _YOUTUBE_HOST_RE
    if _YOUTUBE_HOST_RE is None:
        _YOUTUBE_HOST_RE = re.compile(r"^https?://(www\.|music\.)?(youtube\.com|youtu\.be)/", re.I)
    return bool(_YOUTUBE_HOST_RE.match(url or ""))


@dataclass
class HealthResult:
    """Outcome of probing a single entry's playable URL."""

    entry_id: str
    url: str
    source: str
    title: str
    status: HealthStatus
    checked_url: Optional[str] = None
    status_code: Optional[int] = None
    reason: Optional[str] = None


@dataclass
class ReResolveResult:
    """Outcome of attempting to refresh a dead/expired entry's stream."""

    entry_id: str
    ok: bool
    old_stream: Optional[str] = None
    new_stream: Optional[str] = None
    error: Optional[str] = None
    dry_run: bool = False


@dataclass
class LibraryHealthSummary:
    """Aggregate counts alongside the per-entry results."""

    results: List[HealthResult] = field(default_factory=list)

    @property
    def counts(self) -> dict:
        out = {"ok": 0, "dead": 0, "expired": 0, "no-stream": 0, "gone": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    @property
    def unhealthy(self) -> List[HealthResult]:
        """Re-resolvable failures — the source still exists. Excludes "gone"."""
        return [r for r in self.results if r.status in ("dead", "expired")]

    @property
    def gone(self) -> List[HealthResult]:
        """Entries whose source is confirmed deleted/unavailable — never re-resolved."""
        return [r for r in self.results if r.status == "gone"]


def _probe_url(url: str, *, timeout: float) -> tuple[bool, Optional[int], Optional[str]]:
    """HEAD (falling back to a ranged GET) ``url``. Never raises.

    Returns ``(ok, status_code, reason)``. ``ok`` is True for any 2xx/3xx
    response — redirects are how CDN edge URLs commonly resolve, and
    following them is handled by ``requests`` (``allow_redirects=True``).
    """
    import requests

    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        # Some CDNs/edge servers don't implement HEAD correctly (405/501,
        # or a bogus 200 with no real validation) — fall back to a tiny
        # ranged GET so we don't misclassify a perfectly live stream.
        if resp.status_code in (405, 501) or resp.status_code >= 400:
            resp = requests.get(
                url, timeout=timeout, allow_redirects=True, stream=True,
                headers={"Range": "bytes=0-0"},
            )
            resp.close()
        ok = resp.status_code < 400
        return ok, resp.status_code, None if ok else f"HTTP {resp.status_code}"
    except requests.RequestException as e:
        return False, None, str(e)
    except Exception as e:  # pragma: no cover — defensive, must never raise
        return False, None, f"unexpected error: {e}"


def _oembed_probe(watch_url: str, *, timeout: float) -> tuple[Optional[int], Optional[str]]:
    """GET YouTube's oEmbed endpoint for ``watch_url``. Never raises.

    oEmbed is the cheap, no-yt-dlp-required way to distinguish "video
    exists" from "video deleted/private/removed": YouTube returns 404
    for any unavailable video and 200 (with a title/author JSON body)
    for a live one — unlike a plain request against the watch page
    itself, which renders (and 200s) an "unavailable" placeholder page
    rather than erroring. Returns ``(status_code, error)``.
    """
    import requests

    oembed_url = (
        "https://www.youtube.com/oembed?url="
        f"{quote(watch_url, safe='')}&format=json"
    )
    try:
        resp = requests.get(oembed_url, timeout=timeout, allow_redirects=True)
        return resp.status_code, None
    except requests.RequestException as e:
        return None, str(e)
    except Exception as e:  # pragma: no cover — defensive, must never raise
        return None, f"unexpected error: {e}"


def _check_youtube_entry(entry: MediaEntry, source: str, *, timeout: float) -> HealthResult:
    """Source-aware availability check for YouTube-family entries.

    A HEAD/GET on the watch URL is useless here — YouTube 200s the watch
    page even for a deleted video (it just renders an "unavailable"
    message client-side). oEmbed is the signal that actually reflects
    whether the video still exists: 404 means gone, 2xx means live.
    """
    status_code, err = _oembed_probe(entry.url, timeout=timeout)
    if status_code is None:
        return HealthResult(
            entry_id=entry.id, url=entry.url, source=source, title=entry.title,
            status="dead", reason=f"oEmbed check failed: {err}",
        )
    if status_code == 404:
        return HealthResult(
            entry_id=entry.id, url=entry.url, source=source, title=entry.title,
            status="gone", status_code=404,
            reason="video unavailable/deleted/private (oEmbed 404)",
        )
    if status_code < 400:
        return HealthResult(
            entry_id=entry.id, url=entry.url, source=source, title=entry.title,
            status="ok", status_code=status_code,
        )
    return HealthResult(
        entry_id=entry.id, url=entry.url, source=source, title=entry.title,
        status="dead", status_code=status_code,
        reason=f"oEmbed HTTP {status_code}",
    )


def check_entry(entry: MediaEntry, *, timeout: float = _DEFAULT_TIMEOUT) -> HealthResult:
    """Probe whether ``entry``'s source/stream is currently reachable.

    Classification:
      - YouTube-family entries (source youtube/youtube_music, or any
        entry whose url is a youtube.com/youtu.be link) are checked via
        the oEmbed endpoint (see :func:`_check_youtube_entry`) rather
        than an HTTP probe on the URL itself, because the watch page
        200s even for a deleted video:
          - ``gone``: oEmbed 404s — the video was deleted/made
            private/removed. The source itself no longer exists;
            re-resolving is pointless.
          - ``ok`` / ``dead``: oEmbed 2xx / anything else unreachable.
      - Entries with a stored direct ``entry.stream`` (bandcamp,
        soundcloud, internet_archive) are HEAD/GET-probed directly:
          - ``ok``: 2xx/3xx.
          - ``expired``: 401/403/404/410 — the shape a signed CDN URL
            fails with once its window passes; the *source* is still
            there, so this is re-resolvable.
          - ``dead``: anything else (connection errors, 5xx) — also
            re-resolvable, just not an auth/expiry-shaped failure.
      - ``no-stream``: no stored direct URL and not a YouTube-family
        entry — nothing for us to probe (the player's own resolver
        handles it at play time).

    Never raises — every branch captures failures into the result.
    """
    source = getattr(entry.source, "value", str(entry.source))

    if _is_youtube(source, entry.url):
        return _check_youtube_entry(entry, source, timeout=timeout)

    if not entry.stream:
        return HealthResult(
            entry_id=entry.id, url=entry.url, source=source, title=entry.title,
            status="no-stream", reason="no stored direct stream URL",
        )

    ok, status_code, reason = _probe_url(entry.stream, timeout=timeout)
    if ok:
        return HealthResult(
            entry_id=entry.id, url=entry.url, source=source, title=entry.title,
            status="ok", checked_url=entry.stream, status_code=status_code,
        )

    expired_codes = {401, 403, 404, 410}
    status: HealthStatus = "expired" if status_code in expired_codes else "dead"
    return HealthResult(
        entry_id=entry.id, url=entry.url, source=source, title=entry.title,
        status=status, checked_url=entry.stream, status_code=status_code,
        reason=reason,
    )


def check_library(db_path: str, *, source: Optional[str] = None,
                   where: Optional[str] = None, limit: Optional[int] = None,
                   timeout: float = _DEFAULT_TIMEOUT,
                   max_workers: int = _DEFAULT_MAX_WORKERS) -> List[HealthResult]:
    """Probe matching entries concurrently; returns one :class:`HealthResult` each.

    Bounded by ``limit`` (a scan over an entire large library is exactly
    the kind of thing that must never block a request indefinitely — the
    server-side route enforces its own cap on top of this) and by
    ``max_workers`` so we don't hammer whatever CDNs are behind the
    stored stream URLs.
    """
    idx = Index(db_path)
    entries = idx.to_list(source=source, where=where, limit=limit or 0)

    if not entries:
        return []

    results: List[HealthResult] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(check_entry, e, timeout=timeout): e for e in entries}
        for fut in as_completed(futures):
            entry = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # pragma: no cover — check_entry never raises
                LOG.exception("health probe raised unexpectedly for %s", entry.id)
                results.append(HealthResult(
                    entry_id=entry.id, url=entry.url,
                    source=getattr(entry.source, "value", str(entry.source)),
                    title=entry.title, status="dead", reason=f"probe crashed: {e}",
                ))
    # Stable, deterministic ordering for CLI/UI output regardless of which
    # future finished first.
    order = {e.id: i for i, e in enumerate(entries)}
    results.sort(key=lambda r: order.get(r.entry_id, 0))
    return results


def reresolve_entry(db_path: str, entry: MediaEntry, *, dry_run: bool = True,
                     timeout: float = _DEFAULT_TIMEOUT) -> ReResolveResult:
    """Re-resolve a fresh direct stream URL for a dead/expired ``entry``.

    Calls :func:`media_archivist.streams.resolve_stream` against
    ``entry.url`` (the stable source/watch URL, not the stale direct
    one) and, on success, writes the new URL into ``entry.stream`` on
    disk — unless ``dry_run`` is set, in which case this only reports
    what *would* change. The entry itself is never removed, and a
    resolve failure is reported rather than raised.

    Refuses to act on a confirmed-deleted YouTube-family entry: calling
    ``resolve_stream`` on a video that oEmbed already says is gone can
    only fail, and callers filtering their own "unhealthy" list by
    status (dead/expired only, excluding "gone") should never reach
    here for one anyway — this is a defense-in-depth guard, not the
    only place that rule is enforced.
    """
    source = getattr(entry.source, "value", None)
    if _is_youtube(source or "", entry.url):
        status_code, _err = _oembed_probe(entry.url, timeout=timeout)
        if status_code == 404:
            return ReResolveResult(
                entry_id=entry.id, ok=False, old_stream=entry.stream,
                error="refusing to re-resolve: source video is deleted/"
                      "unavailable (oEmbed 404) — remove or replace this "
                      "entry instead",
                dry_run=dry_run,
            )
    try:
        resolved = _streams.resolve_stream(entry.url, source=source)
    except _streams.StreamResolveError as e:
        return ReResolveResult(
            entry_id=entry.id, ok=False, old_stream=entry.stream,
            error=str(e), dry_run=dry_run,
        )
    except Exception as e:  # pragma: no cover — defensive, must never raise
        LOG.exception("reresolve_entry: unexpected error for %s", entry.id)
        return ReResolveResult(
            entry_id=entry.id, ok=False, old_stream=entry.stream,
            error=f"unexpected error: {e}", dry_run=dry_run,
        )

    new_stream = resolved.url
    if dry_run:
        return ReResolveResult(
            entry_id=entry.id, ok=True, old_stream=entry.stream,
            new_stream=new_stream, dry_run=True,
        )

    db = EnvelopeJsonStorage(db_path)
    raw = db.get(entry.url)
    if raw is None:
        # Row vanished between the caller's scan and this write (concurrent
        # prune/import) -- report the resolve succeeded but nothing was
        # persisted, rather than crashing the caller.
        return ReResolveResult(
            entry_id=entry.id, ok=False, old_stream=entry.stream,
            new_stream=new_stream, dry_run=False,
            error="entry no longer present in DB — not updated",
        )
    raw["stream"] = new_stream
    db[entry.url] = raw
    db.store()
    return ReResolveResult(
        entry_id=entry.id, ok=True, old_stream=entry.stream,
        new_stream=new_stream, dry_run=False,
    )


def remove_entry(db_path: str, entry: MediaEntry) -> bool:
    """Drop ``entry`` from the DB outright.

    Destructive and never called automatically by anything in this
    module — reserved for the explicit, opt-in "remove from index"
    action a curator takes after a health scan flags an entry as
    ``gone`` (source confirmed deleted). Returns False (no raise) if the
    entry is already gone from the DB.
    """
    db = EnvelopeJsonStorage(db_path)
    if entry.url not in db:
        return False
    del db[entry.url]
    db.store()
    return True
