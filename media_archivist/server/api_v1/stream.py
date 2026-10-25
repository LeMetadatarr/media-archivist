# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/stream/{id}`` and signed links to it.

A play resolves a fresh stream and redirects to it, or with ``mode=proxy``
passes its bytes through with the player's ``Range``. It never answers with
an error for a known entry: when resolution fails for a reason of the
video or the source, it plays a short status card that says why, because a
``.strm`` that errors breaks the item in Jellyfin and a redirect to the
watch page plays nothing. A card is an MPEG transport stream sent chunked,
with no ``Content-Length``, no ``Accept-Ranges`` and any ``Range`` ignored,
so a library scan that reads it, ffprobe included, finds no duration and
never records the card's runtime for a film. A ``HEAD`` during a failure
is answered ``503``. When the play only could not get its turn, because
every resolution slot stayed taken for :data:`PLAY_WAIT_S`, too many plays
already wait, or the resolver timed out, the answer is ``503`` with
``Retry-After`` and no card, since the failure says nothing about the
video. With the ``status_cards`` setting ``off``, a failure with a card
redirects to the watch page.

A play is a request a person waits on, so it takes no turn in the request
spacing that holds back background work, and background work does not hold
it back; a bot check met while resolving still pauses the ``details``
kind, for YouTube Music as for YouTube. At most :data:`PLAY_CONCURRENCY`
plays resolve at once, a further one waits up to :data:`PLAY_WAIT_S` for a
slot, and when :data:`PLAY_MAX_WAITING` plays already wait it is answered
``503`` at once, so waiting plays cannot take the server's worker threads.
A resolved address is kept for :data:`PLAY_CACHE_TTL_S`, with plays of one
entry sharing one resolution, so a burst of plays on one link, such as a
Jellyfin scan, makes one request to YouTube and keeps the server's other
routes fast.
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from datetime import datetime, timezone
from importlib import resources
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from media_archivist import links, streams, strm, ytclient
from media_archivist.models.raw import Source
from media_archivist.server.api_v1 import deps
from media_archivist.settings import Settings

LOG = logging.getLogger("media_archivist.server.api_v1.stream")

router = APIRouter()
deps.FEATURES.update({"stream", "links", "status_cards"})

CARD_REASONS = ("rate-limited", "unavailable", "age-restricted", "could-not-play")
BUSY = "busy"
RETRY_AFTER_S = 60
_AGE_GATE = ("age-restricted", "confirm your age")
_CARD_CHUNK = 16 * 188

_PROXY_HEADERS = ("Content-Type", "Content-Range", "Accept-Ranges", "Content-Length")
_YOUTUBE_SOURCES = (Source.YOUTUBE, Source.YOUTUBE_MUSIC)
PLAY_CONCURRENCY = 4
PLAY_MAX_WAITING = 8
PLAY_WAIT_S = 3.0
PLAY_CACHE_TTL_S = 300.0
_CACHE_MAX = 1024
_STATE = threading.Lock()
_PLAYS = threading.BoundedSemaphore(PLAY_CONCURRENCY)
_CACHE: Dict[Tuple[str, str], Tuple[float, str]] = {}
_INFLIGHT: Dict[Tuple[str, str], threading.Event] = {}
_WAITING = 0


def _entry(request: Request, entry_id: str):
    entry = strm.find_entry(deps.get_db_path(request), deps.get_index(request), entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return entry


def _reason(error: BaseException) -> str:
    """The status card a failed resolution is explained by."""
    kind = ytclient.failure_kind(error)
    if kind == "bot":
        return "rate-limited"
    text = ytclient.error_text(error)
    if kind == "permanent":
        return "age-restricted" if any(m in text for m in _AGE_GATE) else "unavailable"
    if "timed out" in text:
        return BUSY
    return "could-not-play"


def _attempt(entry) -> Tuple[Optional[str], Optional[str]]:
    """``(address, None)``, or ``(None, card reason)`` when there is none to be had now."""
    def resolve():
        return streams.resolve_stream(entry.stream or entry.url, source=entry.source.value).url

    try:
        if entry.source in _YOUTUBE_SOURCES:
            return ytclient.CLIENT.call_unspaced("details", resolve), None
        return resolve(), None
    except ytclient.PausedError:
        return None, "rate-limited"
    except ytclient.BusyError:
        return None, BUSY
    except Exception as e:
        LOG.warning("stream resolution failed for %s: %s", entry.id, e)
        return None, _reason(e)


def _resolve(index, entry) -> Optional[str]:
    """A fresh stream address, or ``None`` when there is none to be had now."""
    return _attempt(entry)[0]


def reset_play_state() -> None:
    """Forget cached addresses and release the resolution slots."""
    global _PLAYS, _WAITING
    with _STATE:
        _CACHE.clear()
        _INFLIGHT.clear()
        _WAITING = 0
        _PLAYS = threading.BoundedSemaphore(PLAY_CONCURRENCY)


class _Flight(threading.Event):
    """One resolution in progress; carries why it failed to the plays waiting on it."""

    reason: Optional[str] = None


def _begin_wait() -> bool:
    """Count a play as waiting for something; false when too many already do."""
    global _WAITING
    with _STATE:
        if _WAITING >= PLAY_MAX_WAITING:
            return False
        _WAITING += 1
        return True


def _end_wait() -> None:
    global _WAITING
    with _STATE:
        _WAITING = max(0, _WAITING - 1)


def _play_url(index, entry) -> Optional[str]:
    """The address a play redirects to: cached, shared with a play already resolving, or fresh."""
    return _play(index, entry)[0]


def _play(index, entry) -> Tuple[Optional[str], Optional[str]]:
    """``(address, None)`` for a play, or ``(None, card reason)``. A failure is never cached.

    At most :data:`PLAY_MAX_WAITING` plays wait for a slot or for another
    play's resolution; one more is answered ``busy`` at once, which is a
    ``503``, so waiting plays cannot take the server's worker threads.
    """
    key = (entry.id, entry.stream or entry.url)
    deadline = time.monotonic() + PLAY_WAIT_S
    waited = False
    reason = BUSY
    while True:
        with _STATE:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] > time.monotonic():
                return cached[1], None
            if waited:
                return None, reason
            event = _INFLIGHT.get(key)
            if event is None:
                event = _INFLIGHT[key] = _Flight()
                owner = True
            else:
                owner = False
        if not owner:
            if not _begin_wait():
                return None, BUSY
            try:
                if not event.wait(max(0.0, deadline - time.monotonic())):
                    return None, BUSY
            finally:
                _end_wait()
            waited = True
            reason = event.reason or BUSY
            continue
        slots = _PLAYS
        url = None
        try:
            got = slots.acquire(blocking=False)
            if not got and _begin_wait():
                try:
                    got = slots.acquire(timeout=max(0.0, deadline - time.monotonic()))
                finally:
                    _end_wait()
            if got:
                try:
                    url, event.reason = _attempt(entry)
                finally:
                    slots.release()
            else:
                event.reason = BUSY
            if url is not None:
                with _STATE:
                    _CACHE[key] = (time.monotonic() + PLAY_CACHE_TTL_S, url)
                    if len(_CACHE) > _CACHE_MAX:
                        now = time.monotonic()
                        for k in [k for k, v in _CACHE.items() if v[0] <= now] or [next(iter(_CACHE))]:
                            _CACHE.pop(k, None)
            return url, event.reason
        finally:
            with _STATE:
                if _INFLIGHT.get(key) is event:
                    del _INFLIGHT[key]
            event.set()


@functools.lru_cache(maxsize=len(CARD_REASONS))
def _card(reason: str) -> bytes:
    return (resources.files(__package__) / "cards" / f"{reason}.ts").read_bytes()


def _status_card(request: Request, entry, reason: str):
    """The answer to a play that could not be resolved."""
    headers = {"Cache-Control": "no-store"}
    unavailable = Response(status_code=503, headers={**headers, "Retry-After": str(RETRY_AFTER_S)})
    if reason == BUSY:
        return unavailable
    if Settings(deps.get_index(request)).get("status_cards") != "on":
        return RedirectResponse(url=entry.url, status_code=302)
    if request.method != "GET":
        return unavailable
    body = _card(reason)

    def chunks():
        for start in range(0, len(body), _CARD_CHUNK):
            yield body[start:start + _CARD_CHUNK]

    return StreamingResponse(chunks(), media_type="video/mp2t", headers=headers)


def _proxy(url: str, range_header: Optional[str]) -> StreamingResponse:
    import requests

    upstream = requests.get(url, headers={"Range": range_header} if range_header else {},
                            stream=True, timeout=30)
    upstream.raise_for_status()

    def chunks():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        chunks(), status_code=206 if upstream.status_code == 206 else 200,
        media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
        headers={h: upstream.headers[h] for h in _PROXY_HEADERS if h in upstream.headers})


@router.api_route("/stream/{entry_id}", methods=["GET", "HEAD"])
def stream(entry_id: str, request: Request, mode: Optional[str] = None):
    entry = _entry(request, entry_id)
    url, reason = _play(deps.get_index(request), entry)
    if url is None:
        return _status_card(request, entry, reason or BUSY)
    if mode == "proxy":
        try:
            return _proxy(url, request.headers.get("range"))
        except Exception as e:
            LOG.warning("stream proxy failed for %s: %s", entry.id, e)
    return RedirectResponse(url=url, status_code=302)


@router.post("/entries/{entry_id}/link")
def make_link(entry_id: str, request: Request, body: Optional[dict] = Body(None)):
    """A signed link that plays one entry; ``expires_in`` seconds, or never."""
    entry = _entry(request, entry_id)
    expires_in = (body or {}).get("expires_in")
    expires_at = None
    if expires_in is not None:
        if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
            raise HTTPException(status_code=400, detail="expires_in must be a whole number of seconds above 0")
        expires_at = int(time.time()) + expires_in
    key = links.secret(deps.get_index(request))
    url = links.stream_url(links.public_base(str(request.base_url)), entry.id, key=key,
                           expires_at=expires_at)
    return {"url": url,
            "expires_at": None if expires_at is None else
            datetime.fromtimestamp(expires_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
