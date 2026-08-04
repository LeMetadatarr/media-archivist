"""yt-dlp stream core.

media-archivist archives *streams*, not bytes: the primary job of this
module is :func:`resolve_stream` — resolving a fresh, directly-playable
media URL for a given watch/listing URL (YouTube and friends expire their
direct URLs after a while, so a stored one goes stale). Downloading to a
local directory (:func:`download`) is a secondary, optional path for
callers that actually want a copy on disk.

We prefer the ``yt_dlp`` Python API when it's importable, and fall back to
shelling out to the ``yt-dlp`` binary on ``PATH`` (the same fallback shape
used by :mod:`media_archivist.enrich.transcripts`). Subprocess calls always
use an argument list — never ``shell=True``.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, parse_qs

from media_archivist.exceptions import MediaArchivistError

LOG = logging.getLogger("media_archivist.streams")

_DEFAULT_TIMEOUT = 30.0
_ENV_DOWNLOAD_DIR = "MEDIA_ARCHIVIST_DOWNLOAD_DIR"


class StreamResolveError(MediaArchivistError):
    """Raised when a fresh direct stream URL could not be resolved."""


class StreamDownloadError(MediaArchivistError):
    """Raised when a download to disk fails."""


@dataclass
class ResolvedStream:
    """A freshly-resolved, directly-playable media URL plus metadata."""

    url: str
    ext: Optional[str] = None
    format_id: Optional[str] = None
    protocol: Optional[str] = None
    is_direct: bool = True
    expires: Optional[int] = None
    title: Optional[str] = None
    duration: Optional[float] = None
    thumbnail: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def _require_http(url: str) -> None:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise StreamResolveError(
            f"refusing to resolve non-http(s) url (scheme={scheme!r}): {url}"
        )


def _guess_expiry(direct_url: str) -> Optional[int]:
    """Best-effort epoch-seconds expiry, pulled from common query params."""
    try:
        q = parse_qs(urlparse(direct_url).query)
    except Exception:
        return None
    for key in ("expire", "expires", "Expires", "X-Amz-Expires"):
        if key in q:
            try:
                val = int(q[key][0])
            except (ValueError, IndexError):
                continue
            # X-Amz-Expires is a duration in seconds, not an epoch; skip it —
            # we have no reliable anchor timestamp to add it to here.
            if key == "X-Amz-Expires":
                continue
            return val
    return None


def _import_yt_dlp():
    try:
        import yt_dlp  # type: ignore
        return yt_dlp
    except ImportError:
        return None


def ytdlp_available() -> bool:
    """True if either the ``yt_dlp`` Python module or the binary is usable."""
    if _import_yt_dlp() is not None:
        return True
    return shutil.which("yt-dlp") is not None


def default_download_dir() -> Path:
    """Directory downloads land in by default.

    Honors ``MEDIA_ARCHIVIST_DOWNLOAD_DIR``; otherwise falls back to the
    XDG data dir used elsewhere in this project.
    """
    override = os.environ.get(_ENV_DOWNLOAD_DIR)
    if override:
        return Path(override).expanduser()
    try:
        from json_database import xdg_data_home
        return Path(xdg_data_home()) / "media_archivist" / "downloads"
    except Exception:  # pragma: no cover - defensive fallback
        return Path.home() / ".local" / "share" / "media_archivist" / "downloads"


def _pick_format(info: Dict[str, Any], prefer: str) -> Dict[str, Any]:
    """Pick the format entry matching ``prefer`` (best/bestaudio/worst)."""
    formats = info.get("formats") or []
    if not formats:
        # extract_info() already resolved a single direct-play URL (e.g.
        # some non-YouTube extractors, or a raw progressive stream).
        if info.get("url"):
            return info
        raise StreamResolveError("no formats found in extractor result")

    def has_audio_video(f: Dict[str, Any]) -> bool:
        return f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")

    def has_audio(f: Dict[str, Any]) -> bool:
        return f.get("acodec") not in (None, "none")

    def has_video(f: Dict[str, Any]) -> bool:
        return f.get("vcodec") not in (None, "none")

    candidates = list(formats)
    if prefer == "bestaudio":
        audio_only = [f for f in candidates if has_audio(f) and not has_video(f)]
        candidates = audio_only or [f for f in candidates if has_audio(f)]
    elif prefer == "worst":
        pass
    else:  # "best" (default) and anything else falls back to best-effort
        combined = [f for f in candidates if has_audio_video(f)]
        candidates = combined or candidates

    if not candidates:
        raise StreamResolveError(f"no formats matched prefer={prefer!r}")

    def sort_key(f: Dict[str, Any]) -> float:
        return f.get("tbr") or f.get("abr") or f.get("vbr") or 0

    candidates.sort(key=sort_key, reverse=(prefer != "worst"))
    return candidates[0]


def _resolve_via_python(url: str, prefer: str, timeout: float) -> ResolvedStream:
    yt_dlp = _import_yt_dlp()
    assert yt_dlp is not None
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": timeout,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise StreamResolveError(f"yt-dlp failed to resolve {url}: {e}") from e

    if not info:
        raise StreamResolveError(f"yt-dlp returned no info for {url}")

    fmt = _pick_format(info, prefer)
    direct_url = fmt.get("url")
    if not direct_url:
        raise StreamResolveError(f"resolved format has no url for {url}")

    return ResolvedStream(
        url=direct_url,
        ext=fmt.get("ext") or info.get("ext"),
        format_id=fmt.get("format_id"),
        protocol=fmt.get("protocol"),
        is_direct=True,
        expires=_guess_expiry(direct_url),
        title=info.get("title"),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
    )


def _resolve_via_binary(url: str, prefer: str, timeout: float) -> ResolvedStream:
    if shutil.which("yt-dlp") is None:
        raise StreamResolveError(
            "neither the yt_dlp python module nor the yt-dlp binary is available "
            "(install the `ytdlp` extra, or `pip install yt-dlp` / a system package)"
        )
    fmt_selector = {
        "best": "best",
        "bestaudio": "bestaudio",
        "worst": "worst",
    }.get(prefer, prefer)
    cmd = ["yt-dlp", "-g", "-f", fmt_selector, url]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise StreamResolveError(
            f"yt-dlp binary failed for {url}: {(e.stderr or '').strip()}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise StreamResolveError(f"yt-dlp binary timed out resolving {url}") from e

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise StreamResolveError(f"yt-dlp binary returned no url for {url}")
    direct_url = lines[0].strip()

    ext_match = re.search(r"\.([A-Za-z0-9]{2,4})(?:\?|$)", direct_url)
    return ResolvedStream(
        url=direct_url,
        ext=ext_match.group(1) if ext_match else None,
        format_id=None,
        protocol=urlparse(direct_url).scheme or None,
        is_direct=True,
        expires=_guess_expiry(direct_url),
        title=None,
        duration=None,
        thumbnail=None,
    )


def resolve_stream(url: str, *, prefer: str = "best",
                    timeout: float = _DEFAULT_TIMEOUT) -> ResolvedStream:
    """Resolve a fresh, directly-playable media URL for ``url``.

    Prefers the ``yt_dlp`` Python API; falls back to the ``yt-dlp`` binary
    on ``PATH``. Raises :class:`StreamResolveError` on failure, or if
    ``url`` isn't http(s).
    """
    _require_http(url)
    yt_dlp = _import_yt_dlp()
    if yt_dlp is not None:
        return _resolve_via_python(url, prefer, timeout)
    LOG.debug("yt_dlp python module unavailable — falling back to the binary")
    return _resolve_via_binary(url, prefer, timeout)


def download(url: str, dest_dir: str, *, format: str = "best",
             progress_hook: Optional[Callable[[dict], None]] = None,
             timeout: Optional[float] = None) -> Path:
    """Download ``url`` into ``dest_dir``; return the final file path.

    Prefers the ``yt_dlp`` Python API (so ``progress_hook`` gets live
    ``%``); falls back to the ``yt-dlp`` binary on ``PATH``.
    """
    try:
        _require_http(url)
    except StreamResolveError as e:
        raise StreamDownloadError(str(e)) from e
    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    yt_dlp = _import_yt_dlp()
    if yt_dlp is not None:
        return _download_via_python(url, dest, format, progress_hook, timeout)
    LOG.debug("yt_dlp python module unavailable — falling back to the binary")
    return _download_via_binary(url, dest, format, timeout)


def _download_via_python(url: str, dest: Path, format: str,
                          progress_hook: Optional[Callable[[dict], None]],
                          timeout: Optional[float]) -> Path:
    yt_dlp = _import_yt_dlp()
    assert yt_dlp is not None

    result_path: Dict[str, Optional[str]] = {"path": None}

    def _hook(d: dict) -> None:
        if d.get("status") == "finished":
            result_path["path"] = d.get("filename") or d.get("info_dict", {}).get("filepath")
        if progress_hook is not None:
            progress_hook(d)

    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": format,
        "outtmpl": str(dest / "%(title)s [%(id)s].%(ext)s"),
        "progress_hooks": [_hook],
    }
    if timeout:
        opts["socket_timeout"] = timeout

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = result_path["path"]
            if not filepath:
                filepath = ydl.prepare_filename(info)
    except Exception as e:
        raise StreamDownloadError(f"yt-dlp failed to download {url}: {e}") from e

    return Path(filepath)


def _download_via_binary(url: str, dest: Path, format: str,
                          timeout: Optional[float]) -> Path:
    if shutil.which("yt-dlp") is None:
        raise StreamDownloadError(
            "neither the yt_dlp python module nor the yt-dlp binary is available "
            "(install the `ytdlp` extra, or `pip install yt-dlp` / a system package)"
        )
    out_template = str(dest / "%(title)s [%(id)s].%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", format,
        "-o", out_template,
        "--print", "after_move:filepath",
        url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise StreamDownloadError(
            f"yt-dlp binary failed to download {url}: {(e.stderr or '').strip()}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise StreamDownloadError(f"yt-dlp binary timed out downloading {url}") from e

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise StreamDownloadError(f"yt-dlp binary printed no filepath for {url}")
    return Path(lines[-1].strip())
