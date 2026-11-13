# SPDX-License-Identifier: Apache-2.0
"""The one door every YouTube request goes through.

Code that talks to YouTube (listings, feeds, pictures, per-video details)
never calls tutubo, ``requests`` or yt-dlp directly; it hands the call to
the installed gate with the kind of request it is::

    videos = ytgate.get().call("listing", read_listing, url)

The gate decides whether the call may run now, runs it, and turns what went
wrong into one of the errors below, so a caller only has to tell three
outcomes apart: the kind is paused (:class:`Paused`, nothing was sent),
YouTube answered with its bot check (:class:`BotCheck`), or the request
failed for another reason (:class:`RequestFailed`). The kinds are those of
:mod:`media_archivist.backoff`.

:class:`ClientGate` is the default: it hands every call to
:data:`media_archivist.ytclient.CLIENT`, which spaces requests, keeps the
per-kind pauses and pins the address family. :class:`PassThroughGate` runs
every call at once and only classifies the errors; tests install it, or
another gate, through :func:`install`.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from media_archivist.backoff import KINDS


class GateError(Exception):
    """A gated call did not produce a result."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


class Paused(GateError):
    """The kind is paused after a bot check; the call was not made."""

    def __init__(self, kind: str, until: Optional[float] = None) -> None:
        super().__init__(kind, f"{kind} requests are paused")
        self.until = until


class BotCheck(GateError):
    """YouTube answered with its bot check (or a rate limit) instead of data."""


class RequestFailed(GateError):
    """The request failed for a reason other than the bot check.

    ``permanent`` is true when the answer says the thing asked for cannot be
    read now or later (removed, private, an HTTP 404), false when the
    network or YouTube failed before answering.
    """

    def __init__(self, kind: str, message: str = "", *, permanent: bool = False) -> None:
        super().__init__(kind, message)
        self.permanent = permanent


class Gate:
    def call(self, kind: str, fn: Callable[..., Any], *args, **kwargs) -> Any:
        raise NotImplementedError

    def paused(self, kind: str) -> bool:
        """Whether a call of ``kind`` would be refused right now."""
        return False


def classify(kind: str, error: BaseException) -> GateError:
    """The gate error a failed call of ``kind`` stands for."""
    from media_archivist import ytmeta

    if isinstance(error, GateError):
        return error
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (403, 429):
        failure = "bot"
    elif isinstance(status, int) and 400 <= status < 500:
        failure = "permanent"
    else:
        failure = ytmeta.failure_kind(error)
    if failure == "bot":
        return BotCheck(kind, str(error))
    return RequestFailed(kind, str(error) or type(error).__name__,
                         permanent=failure == "permanent")


class PassThroughGate(Gate):
    """Runs every call at once; only turns its failures into gate errors."""

    def call(self, kind: str, fn: Callable[..., Any], *args, **kwargs) -> Any:
        if kind not in KINDS:
            raise ValueError(f"unknown request kind {kind!r}; expected one of {', '.join(KINDS)}")
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise classify(kind, e) from e


class ClientGate(Gate):
    """Every call is one request of :data:`media_archivist.ytclient.CLIENT`."""

    def call(self, kind: str, fn: Callable[..., Any], *args, **kwargs) -> Any:
        from media_archivist import ytclient

        try:
            return ytclient.CLIENT.call(kind, fn, *args, **kwargs)
        except ytclient.PausedError as e:
            raise Paused(kind, e.until) from e
        except ytclient.BotCheckError as e:
            raise BotCheck(kind, str(e)) from e
        except ytclient.YouTubeError as e:
            raise RequestFailed(kind, str(e), permanent=isinstance(e, ytclient.PermanentError)) from e

    def paused(self, kind: str) -> bool:
        from media_archivist import ytclient

        return ytclient.CLIENT.backoff.blocked_until(kind) is not None


_gate: Gate = ClientGate()
_lock = threading.Lock()


def get() -> Gate:
    return _gate


def install(gate: Gate) -> Gate:
    """Make ``gate`` the one every caller uses; returns the one it replaced."""
    global _gate
    with _lock:
        previous, _gate = _gate, gate
    return previous
