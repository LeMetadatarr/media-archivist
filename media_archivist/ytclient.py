# SPDX-License-Identifier: Apache-2.0
"""The one way this process talks to YouTube.

Every YouTube request, whoever makes it, goes through :data:`CLIENT`:

Spacing
    One process-wide gate spaces the starts of YouTube requests by the
    ``youtube_request_spacing`` setting (10 seconds by default), read at
    each request so a change applies to the next one. A play, which a
    person waits on, is the exception: :meth:`YouTubeClient.call_unspaced`.

Kinds and pauses
    Each request names its kind (``details``, ``listing``, ``feed``,
    ``thumb``, ``search``, ``download``; see :mod:`media_archivist.backoff`).
    A request of a paused kind is refused at once with :class:`PausedError`
    and never reaches YouTube. A bot check pauses its kind and raises
    :class:`BotCheckError`; a success ends the kind's pause.

Failures
    A failure is classified by :func:`failure_kind`: the bot-check wording
    first, then a message that says the video itself cannot be read, then
    HTTP 403 or 429 (a bot check), then any other HTTP 4xx (permanent), and
    everything else is the network's. They raise :class:`BotCheckError`,
    :class:`PermanentError` and :class:`NetworkError`.

Address family
    The ``youtube_ip_family`` setting (``ipv4`` by default) pins yt-dlp with
    ``--force-ipv4``/``--force-ipv6`` or its ``source_address`` option, and
    tutubo through a session from ``tutubo.transport.make_session(family)``.
    A tutubo without ``make_session`` cannot pin a session; its requests then
    use any family and a warning is logged once. ``auto`` pins nothing.

Until :meth:`YouTubeClient.bind` gives it a working index, the client keeps
its pauses in memory and reads the settings' defaults.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from media_archivist import backoff as backoff_mod
from media_archivist.exceptions import MediaArchivistError
from media_archivist.settings import DEFAULTS, Settings

LOG = logging.getLogger("media_archivist.ytclient")

KINDS = backoff_mod.KINDS

# Substrings (lower-cased) of the errors yt-dlp raises when YouTube answers
# with its bot check, a captcha, or its per-session rate limit instead of the
# video. All three mean: stop asking for a while.
BOT_CHECK_MARKERS = (
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "not a bot",
    "this content isn't available",
    "this content isn’t available",
    "requiring a captcha challenge",
    "rate-limited by youtube",
    "http error 429",
    "too many requests",
)

# Substrings (lower-cased) of the errors that say the video itself cannot be
# read, now and later: unavailable, private, removed, deleted, terminated
# account, blocked in the country.
PERMANENT_MARKERS = (
    "video unavailable",
    "video is unavailable",
    "this video is not available",
    "available in your country",
    "blocked it in your country",
    "private video",
    "video is private",
    "has been removed",
    "been deleted",
    "has been terminated",
    "account associated with this video",
    "removed for violating",
    "age-restricted",
    "confirm your age",
    "members-only",
    "members only",
    "available to this channel's members",
    "requires payment",
    "drm protected",
    "recording is not available",
    "closed their youtube account",
    "isn't available anymore",
    "isn’t available anymore",
    "is not available anymore",
)

_HTTP_ERROR = re.compile(r"HTTP Error (\d{3})")
_SOURCE_ADDRESS = {"ipv4": "0.0.0.0", "ipv6": "::"}
_FORCE_FLAG = {"ipv4": "--force-ipv4", "ipv6": "--force-ipv6"}


class YouTubeError(MediaArchivistError):
    """A YouTube request of ``kind`` did not return what was asked."""

    def __init__(self, message: str = "", *, kind: Optional[str] = None) -> None:
        super().__init__(message)
        self.kind = kind


class PausedError(YouTubeError):
    """The kind is paused after a bot check; nothing was sent to YouTube."""

    def __init__(self, kind: str, until: float, remaining: float) -> None:
        super().__init__(f"YouTube {kind} requests are paused for {int(remaining)}s "
                         "after a bot check", kind=kind)
        self.until = until
        self.remaining = remaining


class BotCheckError(YouTubeError):
    """YouTube answered with its bot check; the kind is now paused for ``wait`` seconds."""

    def __init__(self, message: str = "", *, kind: Optional[str] = None,
                 wait: Optional[float] = None) -> None:
        super().__init__(message, kind=kind)
        self.wait = wait


class PermanentError(YouTubeError):
    """YouTube says the item cannot be read (unavailable, private, removed)."""


class NetworkError(YouTubeError):
    """The request failed before YouTube judged it; it is worth trying later."""


class BusyError(YouTubeError):
    """The request's turn in the spacing is further off than it would wait; nothing was sent."""


def is_bot_check(message: str) -> bool:
    low = (message or "").lower()
    return any(m in low for m in BOT_CHECK_MARKERS)


def _chain(error: BaseException):
    """*error* and everything it wraps (cause, context, yt-dlp's ``exc_info``)."""
    seen = set()
    todo = [error]
    while todo:
        e = todo.pop(0)
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        exc_info = getattr(e, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            todo.append(exc_info[1])
        todo.extend((e.__cause__, e.__context__))


def http_status(error: BaseException) -> Optional[int]:
    """The HTTP status behind *error*, from the exception chain or its text."""
    chain = list(_chain(error))
    for e in chain:
        status = getattr(e, "status", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        if isinstance(e, urllib.error.HTTPError):
            return e.code
        status = getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
    for e in chain:
        m = _HTTP_ERROR.search(str(e))
        if m:
            return int(m[1])
    return None


def failure_kind(error: BaseException) -> str:
    """``"permanent"``, ``"bot"`` or ``"network"``.

    The bot wording comes first: yt-dlp prefixes a blocked session's answer
    with "Video unavailable", which would otherwise read as a permanent
    failure. The markers are narrow, so yt-dlp's age gate ("Sign in to
    confirm your age") is no bot check. Then a known permanent message, even
    inside an HTTP 403 chain. Then HTTP 403 or 429, which YouTube answers to
    a blocked address or a missing token rather than about the video. Then
    any other HTTP 4xx (404, 410, 451) is permanent. Everything else, an
    unknown exception type included, is taken for the network's.
    """
    if isinstance(error, BotCheckError):
        return "bot"
    if isinstance(error, PermanentError):
        return "permanent"
    if isinstance(error, NetworkError):
        return "network"
    text = " ".join(str(e).lower() for e in _chain(error))
    if is_bot_check(text):
        return "bot"
    if any(m in text for m in PERMANENT_MARKERS):
        return "permanent"
    status = http_status(error)
    if status in (403, 429):
        return "bot"
    if status is not None and 400 <= status < 500:
        return "permanent"
    return "network"


class RequestGate:
    """Spaces the starts of requests by ``spacing()`` seconds, process-wide.

    A turn is reserved while the lock is held and slept for after it is
    released, so a caller that waits never holds up another caller's
    reservation, nor a :meth:`try_start`, for the length of its sleep.
    """

    def __init__(self, spacing: Callable[[], float], *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.spacing = spacing
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._last: Optional[float] = None

    def wait(self, max_wait: Optional[float] = None) -> None:
        """Return when a request may start, and count it as started.

        With ``max_wait``, raise :class:`BusyError` instead when the turn,
        queueing behind other waiting requests included, is further off.
        """
        with self._lock:
            now = self.clock()
            delay = 0.0
            if self._last is not None:
                delay = max(0.0, self._last + self.spacing() - now)
            if max_wait is not None and delay > max_wait:
                raise BusyError(f"no turn for a YouTube request within {max_wait:g}s")
            self._last = now + delay
        if delay > 0:
            self.sleep(delay)

    def try_start(self) -> float:
        """Count a request as started if one may start now; else the seconds to wait.

        Never sleeps.
        """
        with self._lock:
            now = self.clock()
            if self._last is not None:
                delay = self._last + self.spacing() - now
                if delay > 0:
                    return delay
            self._last = now
            return 0


class YouTubeClient:
    """Spacing, per-kind pauses and the address family for YouTube requests."""

    def __init__(self, *, settings: Optional[Settings] = None,
                 backoff: Optional[backoff_mod.Backoff] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self.backoff = backoff if backoff is not None else backoff_mod.Backoff()
        self.gate = RequestGate(self.spacing, clock=clock, sleep=sleep)
        self._warned_family = False
        self._active = threading.local()

    def bind(self, index) -> None:
        """Read settings from, and keep pauses in, the working index ``index``."""
        persisted = backoff_mod.Backoff(index)
        persisted.adopt(self.backoff)
        self.backoff = persisted
        self.settings = Settings(index)

    def _setting(self, key: str) -> Any:
        return DEFAULTS[key] if self.settings is None else self.settings.get(key)

    def spacing(self) -> float:
        return float(self._setting("youtube_request_spacing"))

    def ip_family(self) -> str:
        """``ipv4``, ``ipv6`` or ``auto``."""
        return self._setting("youtube_ip_family")

    def _pinned(self) -> Optional[str]:
        family = self.ip_family()
        return family if family in _SOURCE_ADDRESS else None

    def ytdlp_options(self) -> Dict[str, Any]:
        """The yt-dlp Python API options that pin the address family."""
        family = self._pinned()
        return {"source_address": _SOURCE_ADDRESS[family]} if family else {}

    def ytdlp_args(self) -> List[str]:
        """The yt-dlp command-line flags that pin the address family."""
        family = self._pinned()
        return [_FORCE_FLAG[family]] if family else []

    def tutubo_session(self) -> Optional[object]:
        """A tutubo HTTP session pinned to the family, or ``None`` for tutubo's default."""
        family = self._pinned()
        if family is None:
            return None
        from tutubo import transport

        make_session = getattr(transport, "make_session", None)
        if make_session is None:
            if not self._warned_family:
                self._warned_family = True
                LOG.warning("the installed tutubo cannot pin an address family; YouTube "
                            "listings and pictures use any family instead of %s", family)
            return None
        return make_session(family)

    def check(self, kind: str) -> None:
        """Raise :class:`PausedError` when ``kind`` is paused."""
        until = self.backoff.blocked_until(kind)
        if until is not None:
            raise PausedError(kind, until, self.backoff.remaining(kind))

    def call(self, kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``fn(*args, **kwargs)`` as one YouTube request of ``kind``.

        A call of ``kind`` made by ``fn`` itself is part of the same request:
        it runs at once, and its failure is classified once, by this call.
        """
        return self.call_within(None, kind, fn, *args, **kwargs)

    def call_within(self, max_wait: Optional[float], kind: str, fn: Callable[..., Any],
                    *args: Any, **kwargs: Any) -> Any:
        """:meth:`call`, waiting at most ``max_wait`` seconds for the turn (:class:`BusyError`)."""
        return self._call(lambda: self.gate.wait(max_wait), kind, fn, args, kwargs)

    def call_unspaced(self, kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """:meth:`call` outside the spacing: no turn is taken, awaited or moved.

        For a request a person is waiting on, which background work must not
        slow down and which must not slow it down. A paused ``kind`` still
        refuses it, and a bot check still pauses the kind.
        """
        return self._call(lambda: None, kind, fn, args, kwargs)

    def _call(self, take_turn: Callable[[], None], kind: str, fn: Callable[..., Any],
              args: tuple, kwargs: dict) -> Any:
        active = getattr(self._active, "kinds", None)
        if active is None:
            active = self._active.kinds = set()
        if kind in active:
            return fn(*args, **kwargs)
        self.check(kind)
        take_turn()
        self.check(kind)
        active.add(kind)
        started = self.backoff.clock()
        try:
            result = fn(*args, **kwargs)
        except (PausedError, BusyError):
            raise
        except Exception as e:
            failure = failure_kind(e)
            if failure == "bot":
                wait = self.backoff.mark_blocked(kind)
                LOG.warning("YouTube bot check on a %s request; %s requests paused for %ds",
                            kind, kind, int(wait))
                raise BotCheckError(str(e), kind=kind, wait=wait) from e
            if isinstance(e, YouTubeError):
                raise
            if failure == "permanent":
                raise PermanentError(str(e), kind=kind) from e
            raise NetworkError(str(e), kind=kind) from e
        finally:
            active.discard(kind)
        self.backoff.mark_clear(kind, started_at=started)
        return result

    # tutubo

    def _tutubo(self, cls, url: str):
        session = self.tutubo_session()
        return cls(url) if session is None else cls(url, session=session)

    def channel(self, url: str):
        """A tutubo ``Channel``; reading its properties makes requests outside the gate."""
        from tutubo.channel import Channel

        return self._tutubo(Channel, url)

    def playlist(self, url: str):
        """A tutubo ``Playlist``; reading its properties makes requests outside the gate."""
        from tutubo.channel import Playlist

        return self._tutubo(Playlist, url)

    def list_videos(self, url: str, kind: str = "listing") -> list:
        """Every video tutubo lists for a channel or a playlist (``list=``) URL."""
        source = self.playlist(url) if "list=" in url else self.channel(url)
        return self.call(kind, lambda: list(source.videos))

    def channel_info(self, url: str) -> Dict[str, Optional[str]]:
        """A channel's permanent id, title and picture."""
        channel = self.channel(url)
        return self.call("listing", lambda: {
            "channel_id": channel.channel_id or None,
            "title": channel.channel_name or None,
            "thumbnail": channel.thumbnail_url or None,
        })

    def channel_playlist_urls(self, url: str) -> List[str]:
        """The playlist URLs on a channel's Playlists tab."""
        channel = self.channel(url)
        return self.call("listing", lambda: list(channel.playlist_urls))

    def playlist_info(self, url: str) -> Dict[str, Optional[str]]:
        """A playlist's title and picture."""
        playlist = self.playlist(url)
        return self.call("listing", lambda: {
            "title": playlist.title or None,
            "thumbnail": playlist.thumbnail_url or None,
        })

    def fetch(self, url: str, kind: str = "thumb", *, timeout: float = 30.0) -> bytes:
        """The body of one YouTube HTTP resource, such as a picture."""
        def get():
            session = self.tutubo_session()
            if session is None:
                import requests

                session = requests.Session()
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content

        return self.call(kind, get)


def learn_lengths(videos: Iterable[Any], *, known: Optional[Mapping[str, float]] = None,
                  read: Optional[Callable[[str], Dict[str, Any]]] = None,
                  limit: Optional[int] = None,
                  client: Optional[YouTubeClient] = None) -> Dict[str, Dict[str, Any]]:
    """Lengths, upload dates and channels for listed videos.

    ``videos`` are tutubo ``Video`` objects. A length comes from the listing
    (``duration_seconds``); a video whose length neither the listing nor
    ``known`` (video id to seconds, what the index already holds) gives is
    read from yt-dlp under the ``details`` kind, which also gives its upload
    date and channel. At most ``limit`` such reads are made, and none once
    ``details`` is paused. Returns ``{video_id: {"duration", "published",
    "channel", "source"}}`` for every video it learned something about;
    ``source`` is ``listing`` or ``yt-dlp``.
    """
    from media_archivist import ytmeta

    known = known or {}
    client = client or CLIENT
    read = read or (lambda vid: ytmeta.extract_video(vid, client=client))
    out: Dict[str, Dict[str, Any]] = {}
    unknown: List[str] = []
    for video in videos:
        vid = getattr(video, "video_id", None)
        if not vid:
            continue
        duration = ytmeta.clean_duration(getattr(video, "duration_seconds", None))
        if duration is not None:
            out[vid] = {"duration": duration, "published": None, "channel": None,
                        "source": "listing"}
        elif ytmeta.clean_duration(known.get(vid)) is None:
            unknown.append(vid)
    reads = 0
    for vid in unknown:
        if limit is not None and reads >= limit:
            break
        reads += 1
        try:
            info = read(vid)
        except (PausedError, BotCheckError):
            break
        except YouTubeError as e:
            LOG.info("no length for %s from yt-dlp: %s", vid, e)
            continue
        fields = ytmeta.fields_from_info(info)
        out[vid] = {"duration": fields["duration"], "published": fields["published"] or None,
                    "channel": fields["author"], "source": "yt-dlp"}
    return out


CLIENT = YouTubeClient()
"""The process-wide client; the server binds it to its working index."""
