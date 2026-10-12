# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/stream/{id}`` and signed links to it.

A play resolves a fresh stream and redirects to it, or with ``mode=proxy``
passes its bytes through with the player's ``Range``. It never answers with
an error for a known entry: when resolution fails, when the ``details``
kind is paused after YouTube's bot check, or when no resolution slot frees
up within :data:`PLAY_WAIT_S`, it redirects to the watch page instead,
because a ``.strm`` that errors breaks the item in Jellyfin.

A play is a request a person waits on, so it takes no turn in the request
spacing that holds back background work, and background work does not hold
it back; a bot check met while resolving still pauses the ``details``
kind, for YouTube Music as for YouTube. At most :data:`PLAY_CONCURRENCY`
plays resolve at once, a further one waits up to :data:`PLAY_WAIT_S` for a
slot, and when :data:`PLAY_MAX_WAITING` plays already wait it is redirected
at once, so waiting plays cannot take the server's worker threads. A
resolved address is kept for :data:`PLAY_CACHE_TTL_S`, with plays of one
entry sharing one resolution, so a burst of plays on one link, such as a
Jellyfin scan, makes one request to YouTube and keeps the server's other
routes fast.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from media_archivist import links, streams, strm, ytclient
from media_archivist.models.raw import Source
from media_archivist.server.api_v1 import deps

LOG = logging.getLogger("media_archivist.server.api_v1.stream")

router = APIRouter()
deps.FEATURES.update({"stream", "links"})

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


def _resolve(index, entry) -> Optional[str]:
    """A fresh stream address, or ``None`` when there is none to be had now."""
    def resolve():
        return streams.resolve_stream(entry.stream or entry.url, source=entry.source.value).url

    try:
        if entry.source in _YOUTUBE_SOURCES:
            return ytclient.CLIENT.call_unspaced("details", resolve)
        return resolve()
    except (ytclient.PausedError, ytclient.BusyError):
        return None
    except Exception as e:
        LOG.warning("stream resolution failed for %s: %s", entry.id, e)
        return None


def reset_play_state() -> None:
    """Forget cached addresses and release the resolution slots."""
    global _PLAYS
    global _WAITING
    with _STATE:
        _CACHE.clear()
        _INFLIGHT.clear()
        _WAITING = 0
        _PLAYS = threading.BoundedSemaphore(PLAY_CONCURRENCY)


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
    """The address a play redirects to, or ``None`` for the watch page.

    Cached, shared with a play already resolving, or fresh. At most
    :data:`PLAY_MAX_WAITING` plays wait for a slot or for another play's
    resolution; one more is answered ``None`` at once, so waiting plays
    cannot take the server's worker threads.
    """
    key = (entry.id, entry.stream or entry.url)
    deadline = time.monotonic() + PLAY_WAIT_S
    waited = False
    while True:
        with _STATE:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] > time.monotonic():
                return cached[1]
            if waited:
                return None
            event = _INFLIGHT.get(key)
            if event is None:
                event = _INFLIGHT[key] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            if not _begin_wait():
                return None
            try:
                if not event.wait(max(0.0, deadline - time.monotonic())):
                    return None
            finally:
                _end_wait()
            waited = True
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
                    url = _resolve(index, entry)
                finally:
                    slots.release()
            if url is not None:
                with _STATE:
                    _CACHE[key] = (time.monotonic() + PLAY_CACHE_TTL_S, url)
                    if len(_CACHE) > _CACHE_MAX:
                        now = time.monotonic()
                        for k in [k for k, v in _CACHE.items() if v[0] <= now] or [next(iter(_CACHE))]:
                            _CACHE.pop(k, None)
            return url
        finally:
            with _STATE:
                if _INFLIGHT.get(key) is event:
                    del _INFLIGHT[key]
            event.set()


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
    url = _play_url(deps.get_index(request), entry)
    if url is None:
        return RedirectResponse(url=entry.url, status_code=302)
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
