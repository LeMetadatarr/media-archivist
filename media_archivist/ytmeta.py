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
fields stay empty and the limiter backs off; nothing here retries around
the check, rotates clients or supplies cookies.

The outcome of an extraction is cached on the row: the filled fields plus
``metadata_checked`` (UTC timestamp), so a video is extracted once.
A bot check writes nothing, so the row is tried again after the back-off.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Optional

from media_archivist.exceptions import MediaArchivistError

LOG = logging.getLogger("media_archivist.ytmeta")

_ENV_INTERVAL = "MEDIA_ARCHIVIST_YT_METADATA_INTERVAL"
DEFAULT_MIN_INTERVAL_S = 2.0
DEFAULT_BACKOFF_S = 15 * 60
MAX_BACKOFF_S = 6 * 3600

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:$|[T ])")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

# Substrings (lower-cased) of the errors yt-dlp raises when YouTube answers
# with its bot check, a captcha, or its per-session rate limit instead of the
# video. All three mean: stop asking for a while.
BOT_CHECK_MARKERS = (
    "sign in to confirm you",
    "not a bot",
    "requiring a captcha challenge",
    "rate-limited by youtube",
)


class MetadataError(MediaArchivistError):
    """A single-video extraction failed for a reason other than the bot check."""


class BotCheckError(MetadataError):
    """YouTube answered with its bot check; the caller must back off."""


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


def _env_interval() -> float:
    try:
        return max(0.0, float(os.environ.get(_ENV_INTERVAL, DEFAULT_MIN_INTERVAL_S)))
    except ValueError:
        return DEFAULT_MIN_INTERVAL_S


class MetadataLimiter:
    """Spacing between extractions plus a doubling back-off after a bot check.

    Thread-safe. :meth:`try_acquire` never waits (for request handlers);
    :meth:`acquire` sleeps out the spacing (for background work) but never a
    back-off: while blocked it returns ``False`` at once.
    """

    def __init__(self, min_interval: Optional[float] = None, *,
                 backoff: float = DEFAULT_BACKOFF_S, max_backoff: float = MAX_BACKOFF_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval = _env_interval() if min_interval is None else min_interval
        self.initial_backoff = backoff
        self.max_backoff = max_backoff
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._blocked_until = 0.0

    def blocked_for(self) -> float:
        """Seconds left in the current back-off (0 when not blocked)."""
        with self._lock:
            return max(0.0, self._blocked_until - self._clock())

    @property
    def blocked(self) -> bool:
        return self.blocked_for() > 0

    def try_acquire(self) -> bool:
        with self._lock:
            now = self._clock()
            if now < self._blocked_until or now < self._next_allowed:
                return False
            self._next_allowed = now + self.min_interval
            return True

    def acquire(self) -> bool:
        while True:
            with self._lock:
                now = self._clock()
                if now < self._blocked_until:
                    return False
                wait = self._next_allowed - now
                if wait <= 0:
                    self._next_allowed = now + self.min_interval
                    return True
            self._sleep(wait)

    def bot_check(self) -> float:
        """Enter back-off; returns its length in seconds."""
        with self._lock:
            length = self._backoff
            self._blocked_until = self._clock() + length
            self._backoff = min(self._backoff * 2, self.max_backoff)
            return length

    def success(self) -> None:
        with self._lock:
            self._backoff = self.initial_backoff


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


def _extract_binary(url: str, timeout: float) -> Dict[str, Any]:
    if shutil.which("yt-dlp") is None:
        raise MetadataError("neither the yt_dlp python module nor the yt-dlp binary is available")
    try:
        proc = subprocess.run(
            ["yt-dlp", "-J", "--skip-download", "--no-playlist", url],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError((e.stderr or "").strip() or str(e)) from e
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
        if is_bot_check(str(e)):
            raise BotCheckError(str(e)) from e
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
    status: str  # "filled" | "unchanged" | "error" | "bot-check" | "skipped"
    error: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)


def fill_row(db_path: str, raw: Dict[str, Any], *,
             extract: Optional[Callable[[str], Dict[str, Any]]] = None,
             limiter: Optional[MetadataLimiter] = None) -> FillResult:
    """Extract one video and cache the result on its row.

    The caller has already acquired the limiter. A bot check puts the
    limiter into back-off and writes nothing; any other failure stamps
    ``metadata_checked`` so the video is not extracted again unless forced.
    """
    lim = limiter or LIMITER
    url = raw["url"]
    try:
        info = (extract or extract_video)(raw["videoId"])
    except BotCheckError as e:
        length = lim.bot_check()
        LOG.warning("YouTube bot check while reading %s; metadata fills paused for %ds",
                    url, int(length))
        return FillResult(url=url, status="bot-check", error=str(e))
    except Exception as e:
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
    in back-off), when ``stop`` is set, or after ``limit`` extractions.
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
        if res.status == "error":
            result.errors += 1
        elif res.status == "filled":
            result.updated += 1
    result.remaining = pending_count(db_path)
    return result
