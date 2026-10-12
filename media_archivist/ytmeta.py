"""YouTube metadata: clean listing values and fill gaps from yt-dlp.

Channel and playlist listings (tutubo's page parsing, ``yt-dlp
--flat-playlist``) carry a video's duration, channel and upload date only
some of the time, and tutubo's "published" text is whatever label sat next
to the view count: ``"5 hours ago"``, a badge, or a second view count.
Only values that mean what the field says are stored: a positive number of
seconds, a non-empty channel name, a calendar date.

The gaps are filled by a full ``yt-dlp`` extraction of the single video,
one at a time through a shared :class:`MetadataLimiter`. When YouTube
answers with its bot check ("Sign in to confirm you're not a bot") the
fields stay empty and the ``details`` kind of :mod:`media_archivist.backoff`
pauses; nothing here retries around the check, rotates clients or supplies
cookies.

The outcome of an extraction is cached on the row: the filled fields plus
``metadata_checked`` (UTC timestamp), so a video is extracted once.
A bot check or a network failure writes nothing, so the row is tried again
later; only a definitive failure (unavailable, private, removed) is cached.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Optional

from media_archivist import backoff as backoff_mod
from media_archivist.exceptions import MediaArchivistError

LOG = logging.getLogger("media_archivist.ytmeta")

_ENV_INTERVAL = "MEDIA_ARCHIVIST_YT_METADATA_INTERVAL"
DEFAULT_MIN_INTERVAL_S = 2.0
DEFAULT_BACKOFF_S = backoff_mod.INITIAL_WAIT_S
MAX_BACKOFF_S = backoff_mod.MAX_WAIT_S
_READ_POLL_S = 0.05
NETWORK_FAILED_KEY = "metadata_network_failed"
MAX_NETWORK_FAILURES = 3

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:$|[T ])")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

# Substrings (lower-cased) of the errors yt-dlp raises when YouTube answers
# with its bot check, a captcha, or its per-session rate limit instead of the
# video. All three mean: stop asking for a while.
BOT_CHECK_MARKERS = (
    "confirm you're not a bot",
    "confirm you\u2019re not a bot",
    "not a bot",
    "this content isn't available",
    "this content isn\u2019t available",
    "requiring a captcha challenge",
    "rate-limited by youtube",
    "http error 429",
    "too many requests",
)

# Substrings (lower-cased) of the errors that say the video itself cannot be
# read, now and later: unavailable, private, removed, deleted, terminated
# account, blocked in the country. Only these (and an HTTP 4xx other than
# 429) stamp a row; every other failure is taken for the network's and the
# row is tried again.
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
    "isn\u2019t available anymore",
    "is not available anymore",
)


class MetadataError(MediaArchivistError):
    """A single-video extraction failed for a reason other than the bot check."""


class BotCheckError(MetadataError):
    """YouTube answered with its bot check; the caller must back off."""


class NetworkError(MetadataError):
    """The network failed before YouTube answered; the video is not judged."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_published(value: Any) -> str:
    """A calendar date (``YYYY-MM-DD``, optionally with a time) or ``""``.

    ``YYYYMMDD`` (yt-dlp's ``upload_date``) is rewritten as ``YYYY-MM-DD``.
    Relative texts ("3 years ago"), view counts ("121K") and labels are
    dropped.
    """
    if not isinstance(value, str):
        return ""
    v = value.strip()
    m = _COMPACT_DATE.match(v)
    if m:
        v = f"{m[1]}-{m[2]}-{m[3]}"
    m = _ISO_DATE.match(v)
    if not m:
        return ""
    try:
        date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return ""
    return v


def clean_duration(value: Any) -> Optional[float]:
    """Seconds as a positive finite number, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def clean_channel(value: Any) -> Optional[str]:
    """A non-empty channel name, else ``None``."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v or None


def published_from_info(info: Dict[str, Any]) -> str:
    """Upload date of a yt-dlp info dict (``upload_date``, else ``timestamp``)."""
    published = clean_published(info.get("upload_date") or "")
    if published:
        return published
    ts = info.get("timestamp") or info.get("release_timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return ""


def is_bot_check(message: str) -> bool:
    low = (message or "").lower()
    return any(m in low for m in BOT_CHECK_MARKERS)


_HTTP_ERROR = re.compile(r"HTTP Error (\d{3})")


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
    any other HTTP 4xx
    (404, 410, 451) is permanent. Everything else, an unknown exception type
    included, is taken for the network's and retried later.
    """
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


def is_definitive_failure(error: BaseException) -> bool:
    """True only when *error* positively says the video cannot be read."""
    return failure_kind(error) == "permanent"


def is_network_error(error: BaseException) -> bool:
    """True when *error* is neither permanent nor a bot check: it is retried later."""
    return failure_kind(error) == "network"


def _env_interval() -> float:
    try:
        return max(0.0, float(os.environ.get(_ENV_INTERVAL, DEFAULT_MIN_INTERVAL_S)))
    except ValueError:
        return DEFAULT_MIN_INTERVAL_S


class MetadataLimiter:
    """One extraction at a time, spaced, paused after a bot check.

    Thread-safe. A successful :meth:`try_acquire` or :meth:`acquire` takes the
    single read slot and the caller hands it back with :meth:`release` when
    the read is over. :meth:`try_acquire` never waits (for request handlers)
    and returns ``False`` while another read is running; :meth:`acquire`
    sleeps out the spacing and a running read (for background work) but never
    a pause: while paused it returns ``False`` at once.

    The pause is the ``details`` kind of a :class:`~media_archivist.backoff.Backoff`:
    in memory until :meth:`persist_to` moves it into the working index beside
    a database, after which it outlives a restart and is the same pause every
    other ``details`` request sees. A bot check while a pause is already
    running leaves that pause as it is.
    """

    KIND = "details"

    def __init__(self, min_interval: Optional[float] = None, *,
                 backoff: float = DEFAULT_BACKOFF_S, max_backoff: float = MAX_BACKOFF_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval = _env_interval() if min_interval is None else min_interval
        self.initial_backoff = backoff
        self.max_backoff = max_backoff
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._reading = False
        self.pause = backoff_mod.Backoff(initial=backoff, maximum=max_backoff, clock=clock)

    def persist_to(self, db_path: str) -> None:
        """Keep the pause in the working index of ``db_path`` from now on."""
        from media_archivist.indexdb import open_index

        persisted = backoff_mod.Backoff(open_index(db_path), initial=self.initial_backoff,
                            maximum=self.max_backoff)
        persisted.adopt(self.pause)
        self.pause = persisted

    def blocked_for(self) -> float:
        """Seconds left in the current pause (0 when not paused)."""
        return self.pause.remaining(self.KIND)

    @property
    def blocked(self) -> bool:
        return self.blocked_for() > 0

    def try_acquire(self) -> bool:
        if self.blocked:
            return False
        with self._lock:
            now = self._clock()
            if self._reading or now < self._next_allowed:
                return False
            self._reading = True
            self._next_allowed = now + self.min_interval
            return True

    def acquire(self) -> bool:
        while True:
            if self.blocked:
                return False
            with self._lock:
                now = self._clock()
                wait = self._next_allowed - now
                if wait <= 0 and not self._reading:
                    self._reading = True
                    self._next_allowed = now + self.min_interval
                    return True
            self._sleep(wait if wait > 0 else _READ_POLL_S)

    def release(self) -> None:
        """Give the read slot back."""
        with self._lock:
            self._reading = False

    def bot_check(self) -> float:
        """Enter the pause; returns its length in seconds."""
        return self.pause.mark_blocked(self.KIND)

    def success(self) -> None:
        self.pause.mark_clear(self.KIND)


LIMITER = MetadataLimiter()
"""Process-wide limiter shared by lazy fills and the re-enrich task."""


def _extract_python(yt_dlp, url: str, timeout: float) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": timeout,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        # process=False: the extractor's own metadata, without format
        # selection (which can fail on a video whose formats are fine to skip).
        return ydl.extract_info(url, download=False, process=False) or {}


def _error_lines(stderr: Optional[str]) -> str:
    """The ERROR lines of yt-dlp's stderr; a WARNING line says nothing about the failure."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("ERROR:")]
    return "\n".join(errors or [ln for ln in lines if not ln.startswith("WARNING:")])


def _extract_binary(url: str, timeout: float) -> Dict[str, Any]:
    if shutil.which("yt-dlp") is None:
        raise MetadataError("neither the yt_dlp python module nor the yt-dlp binary is available")
    try:
        proc = subprocess.run(
            ["yt-dlp", "-J", "--skip-download", "--no-playlist", url],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(_error_lines(e.stderr) or str(e)) from e
    return json.loads(proc.stdout) or {}


def extract_video(video_id: str, *, timeout: float = 60.0) -> Dict[str, Any]:
    """Full yt-dlp metadata for one video.

    Raises :class:`BotCheckError` on YouTube's bot check and
    :class:`MetadataError` on any other failure.
    """
    from media_archivist import streams

    url = f"https://www.youtube.com/watch?v={video_id}"
    yt_dlp = streams._import_yt_dlp()
    try:
        if yt_dlp is not None:
            return _extract_python(yt_dlp, url, timeout)
        return _extract_binary(url, timeout)
    except MetadataError:
        raise
    except Exception as e:
        kind = failure_kind(e)
        if kind == "bot":
            raise BotCheckError(str(e)) from e
        if kind == "network":
            raise NetworkError(f"yt-dlp could not reach {url}: {e}") from e
        raise MetadataError(f"yt-dlp failed for {url}: {e}") from e


def fields_from_info(info: Dict[str, Any]) -> Dict[str, Any]:
    """The row fields a yt-dlp info dict can fill: duration, author, published."""
    return {
        "duration": clean_duration(info.get("duration")),
        "author": clean_channel(info.get("channel") or info.get("uploader")),
        "published": published_from_info(info),
    }


def is_youtube_row(raw: Dict[str, Any]) -> bool:
    return raw.get("source") == "youtube" and bool(raw.get("videoId"))


def missing_fields(raw: Dict[str, Any]) -> bool:
    """True when a YouTube row lacks a duration, channel or upload date."""
    return (clean_duration(raw.get("duration")) is None
            or clean_channel(raw.get("author")) is None
            or not clean_published(raw.get("published")))


def needs_fill(raw: Dict[str, Any], *, force: bool = False) -> bool:
    if not is_youtube_row(raw) or not missing_fields(raw):
        return False
    return force or not raw.get("metadata_checked")


def sanitize_row(raw: Dict[str, Any]) -> bool:
    """Drop junk duration/author/published values in place; True if changed."""
    changed = False
    published = clean_published(raw.get("published"))
    if (raw.get("published") or "") != published:
        raw["published"] = published
        changed = True
    if raw.get("duration") is not None and clean_duration(raw.get("duration")) is None:
        raw["duration"] = None
        changed = True
    if raw.get("author") is not None and clean_channel(raw.get("author")) is None:
        raw["author"] = None
        changed = True
    return changed


def merge_fields(raw: Dict[str, Any], fields: Dict[str, Any]) -> bool:
    """Fill the row's empty fields from ``fields``; existing good values stay."""
    changed = False
    if clean_duration(raw.get("duration")) is None and fields.get("duration") is not None:
        raw["duration"] = fields["duration"]
        changed = True
    if clean_channel(raw.get("author")) is None and fields.get("author"):
        raw["author"] = fields["author"]
        changed = True
    if not clean_published(raw.get("published")) and fields.get("published"):
        raw["published"] = fields["published"]
        changed = True
    return changed


_write_lock = threading.Lock()


def _write_row(db_path: str, url: str, update: Callable[[Dict[str, Any]], bool]) -> bool:
    """Apply ``update`` to the row at ``url`` in a freshly loaded DB and store it."""
    from media_archivist.storage import EnvelopeJsonStorage

    with _write_lock:
        db = EnvelopeJsonStorage(db_path)
        raw = db.get(url)
        if raw is None:
            return False
        if not update(raw):
            return False
        db[url] = raw
        db.store()
        return True


@dataclass
class FillResult:
    url: str
    status: str  # "filled" | "unchanged" | "error" | "network" | "bot-check" | "skipped"
    error: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)


def fill_row(db_path: str, raw: Dict[str, Any], *,
             extract: Optional[Callable[[str], Dict[str, Any]]] = None,
             limiter: Optional[MetadataLimiter] = None) -> FillResult:
    """Extract one video and cache the result on its row.

    The caller has already acquired the limiter; the read slot is handed back
    when this returns. A bot check puts the limiter into back-off and writes
    nothing. A network failure writes nothing either, so the row is read
    again later. Any other failure (private, removed, unavailable) stamps
    ``metadata_checked`` so the video is not extracted again unless forced.
    """
    lim = limiter or LIMITER
    try:
        return _fill_row(db_path, raw, extract, lim)
    finally:
        lim.release()


def _fill_row(db_path: str, raw: Dict[str, Any],
              extract: Optional[Callable[[str], Dict[str, Any]]],
              lim: MetadataLimiter) -> FillResult:
    url = raw["url"]
    try:
        info = (extract or extract_video)(raw["videoId"])
    except Exception as e:
        kind = "bot" if isinstance(e, BotCheckError) else failure_kind(e)
        if kind == "bot":
            length = lim.bot_check()
            LOG.warning("YouTube bot check while reading %s; metadata fills paused for %ds",
                        url, int(length))
            return FillResult(url=url, status="bot-check", error=str(e))
        if kind == "network":
            LOG.info("network failure while reading %s: %s", url, e)
            stamp = _utcnow()

            def _note(row):
                row[NETWORK_FAILED_KEY] = stamp
                return True

            _write_row(db_path, url, _note)
            return FillResult(url=url, status="network", error=str(e))
        LOG.info("metadata extraction failed for %s: %s", url, e)
        stamp = _utcnow()

        def _mark(row):
            row["metadata_checked"] = stamp
            return True

        _write_row(db_path, url, _mark)
        return FillResult(url=url, status="error", error=str(e))
    lim.success()
    fields = fields_from_info(info)
    stamp = _utcnow()
    filled: Dict[str, bool] = {}

    def _apply(row):
        sanitize_row(row)
        filled["changed"] = merge_fields(row, fields)
        row["metadata_checked"] = stamp
        row.pop(NETWORK_FAILED_KEY, None)
        return True

    _write_row(db_path, url, _apply)
    return FillResult(url=url, status="filled" if filled.get("changed") else "unchanged",
                      fields=fields)


def fill_entry_lazily(db_path: str, raw: Dict[str, Any], *,
                      extract: Optional[Callable[[str], Dict[str, Any]]] = None,
                      limiter: Optional[MetadataLimiter] = None) -> FillResult:
    """Fill one row on read when it needs it and the limiter allows it now.

    Never waits: a back-off or a too-recent extraction returns ``skipped``
    and the row is served as it is.
    """
    if not needs_fill(raw):
        return FillResult(url=raw.get("url", ""), status="skipped")
    lim = limiter or LIMITER
    if not lim.try_acquire():
        return FillResult(url=raw["url"], status="skipped")
    return fill_row(db_path, raw, extract=extract, limiter=lim)


@dataclass
class ReenrichResult:
    checked: int = 0
    updated: int = 0
    sanitized: int = 0
    remaining: int = 0
    bot_check: bool = False
    errors: int = 0


def pending_count(db_path: str, *, force: bool = False) -> int:
    from media_archivist.storage import EnvelopeJsonStorage

    db = EnvelopeJsonStorage(db_path)
    return sum(1 for raw in db.values() if needs_fill(raw, force=force))


def reenrich(db_path: str, *, limit: int = 100, force: bool = False,
             extract: Optional[Callable[[str], Dict[str, Any]]] = None,
             limiter: Optional[MetadataLimiter] = None,
             stop: Optional[threading.Event] = None) -> ReenrichResult:
    """Clean every YouTube row, then fill up to ``limit`` rows from yt-dlp.

    Stops at the first bot check (the fields stay empty and the limiter is
    in back-off), after three network failures in a row, when ``stop`` is set, or after ``limit`` extractions.
    ``force`` also re-reads rows already marked ``metadata_checked``.
    """
    from media_archivist.storage import EnvelopeJsonStorage

    lim = limiter or LIMITER
    result = ReenrichResult()

    with _write_lock:
        db = EnvelopeJsonStorage(db_path)
        for url, raw in list(db.items()):
            if is_youtube_row(raw) and sanitize_row(raw):
                db[url] = raw
                result.sanitized += 1
        if result.sanitized:
            db.store()
        todo = [dict(raw) for raw in db.values() if needs_fill(raw, force=force)]
    # Rows that failed on the network most recently are read last.
    todo.sort(key=lambda raw: raw.get(NETWORK_FAILED_KEY) or "")
    network_failures = 0

    for raw in todo:
        if result.checked >= limit or (stop is not None and stop.is_set()):
            break
        if not lim.acquire():
            result.bot_check = True
            break
        res = fill_row(db_path, raw, extract=extract, limiter=lim)
        result.checked += 1
        if res.status == "bot-check":
            result.bot_check = True
            break
        if res.status == "network":
            network_failures += 1
            if network_failures >= MAX_NETWORK_FAILURES:
                break
            continue
        network_failures = 0
        if res.status == "error":
            result.errors += 1
        elif res.status == "filled":
            result.updated += 1
    result.remaining = pending_count(db_path)
    return result
