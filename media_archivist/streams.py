"""Stream resolution core — source-aware, yt-dlp by default.

media-archivist archives *streams*, not bytes: the primary job of this
module is :func:`resolve_stream` — resolving a fresh, directly-playable
media URL for a given watch/listing URL (YouTube and friends expire their
direct URLs after a while, so a stored one goes stale). Downloading to a
local directory (:func:`download`) is a secondary, optional path for
callers that actually want a copy on disk.

``resolve_stream`` dispatches by ``source``: soundcloud and bandcamp use
their native archivist libs (``nuvem_de_som`` / ``py_bandcamp`` — yt-dlp
isn't the right or best backend for them), internet_archive URLs are
already direct (no resolution needed), and everything else (YouTube,
generic, or no source given) uses yt-dlp. We prefer the ``yt_dlp`` Python
API when it's importable, and fall back to shelling out to the ``yt-dlp``
binary on ``PATH`` (the same fallback shape used by
:mod:`media_archivist.enrich.transcripts`). Subprocess calls always use an
argument list — never ``shell=True``.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse, parse_qs

from media_archivist import ytclient
from media_archivist.exceptions import MediaArchivistError

LOG = logging.getLogger("media_archivist.streams")

_DEFAULT_TIMEOUT = 30.0
_ENV_DOWNLOAD_DIR = "MEDIA_ARCHIVIST_DOWNLOAD_DIR"

# Best video plus best audio, merged by ffmpeg; the single best pre-merged
# stream when the source offers no separate ones. YouTube offers only
# separate streams, so yt-dlp's "best" selector matches nothing there.
DEFAULT_DOWNLOAD_FORMAT = "bv*+ba/b"
# Without ffmpeg yt-dlp rejects a merging selector before it reaches the
# "/b" alternative, so such hosts get the single best stream.
NO_FFMPEG_DOWNLOAD_FORMAT = "b"


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


def _is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


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
    if _is_youtube(url):
        opts.update(ytclient.CLIENT.ytdlp_options())
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
    family = ytclient.CLIENT.ytdlp_args() if _is_youtube(url) else []
    cmd = ["yt-dlp", "-g", "-f", fmt_selector, *family, url]
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


def _resolve_via_ytdlp(url: str, prefer: str, timeout: float) -> ResolvedStream:
    """The original yt-dlp-only path: python API, falling back to the binary."""
    yt_dlp = _import_yt_dlp()
    if yt_dlp is not None:
        return _resolve_via_python(url, prefer, timeout)
    LOG.debug("yt_dlp python module unavailable — falling back to the binary")
    return _resolve_via_binary(url, prefer, timeout)


def _resolve_soundcloud(url: str) -> ResolvedStream:
    from nuvem_de_som import SoundCloud  # local import: optional dep

    direct_url = SoundCloud().resolve_stream(url)
    if not direct_url:
        raise StreamResolveError(f"nuvem_de_som resolved no stream for {url}")
    return ResolvedStream(
        url=direct_url,
        is_direct=True,
        expires=_guess_expiry(direct_url),
    )


def _resolve_bandcamp(url: str) -> ResolvedStream:
    from py_bandcamp import BandcampTrack  # local import: optional dep

    track = BandcampTrack.from_url(url)
    direct_url = track.stream
    if not direct_url:
        raise StreamResolveError(f"py_bandcamp resolved no stream for {url}")
    return ResolvedStream(
        url=direct_url,
        is_direct=True,
        expires=_guess_expiry(direct_url),
        title=getattr(track, "title", None),
        duration=getattr(track, "duration", None),
        thumbnail=getattr(track, "image", None),
    )


def _resolve_internet_archive(url: str) -> ResolvedStream:
    # IA download URLs are stable direct files (no expiry) — the entry's
    # stored `stream`/`url` (passed in here by the caller) already *is*
    # the direct URL, so there is nothing to resolve.
    return ResolvedStream(url=url, is_direct=True)


# source value -> native resolver. Anything not listed here (including
# "youtube"/"youtube_music"/None) falls through to the yt-dlp path.
_NATIVE_RESOLVERS: Dict[str, Callable[[str], ResolvedStream]] = {
    "soundcloud": _resolve_soundcloud,
    "bandcamp": _resolve_bandcamp,
    "internet_archive": _resolve_internet_archive,
}


def resolve_stream(url: str, *, source: Optional[str] = None, prefer: str = "best",
                    timeout: float = _DEFAULT_TIMEOUT) -> ResolvedStream:
    """Resolve a fresh, directly-playable media URL for ``url``.

    Source-aware: soundcloud/bandcamp/internet_archive URLs are resolved
    via their native archivist libs (``nuvem_de_som`` / ``py_bandcamp`` /
    already-direct, respectively) — yt-dlp isn't the right or best backend
    for those. YouTube and anything else (or ``source=None``, preserving
    back-compat) go through the existing yt-dlp path.

    If the native lib is missing (ImportError) or raises, this logs a
    warning and falls back to the yt-dlp path rather than hard-failing —
    yt-dlp may still be able to resolve the url. Raises
    :class:`StreamResolveError` on total failure, or if ``url`` isn't
    http(s).
    """
    _require_http(url)
    resolver = _NATIVE_RESOLVERS.get(source) if source else None
    if resolver is not None:
        try:
            return resolver(url)
        except ImportError as e:
            LOG.warning(
                "native lib for source=%r unavailable (%s) — falling back to yt-dlp for %s",
                source, e, url,
            )
        except Exception as e:
            LOG.warning(
                "native resolve failed for source=%r (%s) — falling back to yt-dlp for %s",
                source, e, url,
            )
    return _resolve_via_ytdlp(url, prefer, timeout)


def list_playlist(url: str, *, timeout: float = 120.0) -> Dict[str, Any]:
    """List a playlist / channel without resolving each video.

    Equivalent to ``yt-dlp --flat-playlist -J``: returns the playlist info
    dict whose ``entries`` hold one flat record per video. Uses the
    ``yt_dlp`` Python module when installed, otherwise the binary.
    """
    _require_http(url)
    yt_dlp = _import_yt_dlp()
    if yt_dlp is not None:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "socket_timeout": timeout,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise StreamResolveError(f"yt-dlp failed to list {url}: {e}") from e
        return info or {}
    if shutil.which("yt-dlp") is None:
        raise StreamResolveError(
            "neither the yt_dlp python module nor the yt-dlp binary is available"
        )
    try:
        proc = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-J", url],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        return json.loads(proc.stdout) or {}
    except subprocess.CalledProcessError as e:
        raise StreamResolveError(
            f"yt-dlp binary failed to list {url}: {(e.stderr or '').strip()}"
        ) from e
    except (subprocess.TimeoutExpired, ValueError) as e:
        raise StreamResolveError(f"yt-dlp binary could not list {url}: {e}") from e



def ffmpeg_available() -> bool:
    """True when ffmpeg can merge streams (yt-dlp's own check, else ``PATH``)."""
    yt_dlp = _import_yt_dlp()
    if yt_dlp is not None:
        try:
            from yt_dlp.postprocessor import FFmpegMergerPP
            return bool(FFmpegMergerPP().available)
        except Exception:
            pass
    return shutil.which("ffmpeg") is not None


def default_download_format() -> str:
    """The selector used when the caller names none."""
    return DEFAULT_DOWNLOAD_FORMAT if ffmpeg_available() else NO_FFMPEG_DOWNLOAD_FORMAT


def download(url: str, dest_dir: str, *, format: Optional[str] = None,
             progress_hook: Optional[Callable[[dict], None]] = None,
             timeout: Optional[float] = None) -> Path:
    """Download ``url`` into ``dest_dir``; return the final file path.

    Prefers the ``yt_dlp`` Python API (so ``progress_hook`` gets live
    ``%``); falls back to the ``yt-dlp`` binary on ``PATH``. ``format`` is
    passed to yt-dlp unchanged; ``None`` picks :func:`default_download_format`.
    """
    try:
        _require_http(url)
    except StreamResolveError as e:
        raise StreamDownloadError(str(e)) from e
    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    if format is None:
        format = default_download_format()

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
            # A merged download fires "finished" once per stream, so the hook
            # holds the last part file; yt-dlp records the merged file here.
            requested = (info or {}).get("requested_downloads") or []
            filepath = (requested[0].get("filepath") if requested else None) \
                or result_path["path"]
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


# The fields a download's file name may be built from, and the yt-dlp
# output-template field each one reads. yt-dlp sanitises every field value
# for the file system; the literal text around them is sanitised here.
FILENAME_FIELDS: Dict[str, str] = {
    "title": "title",
    "id": "id",
    "video_id": "id",
    "uploader": "uploader",
    "channel": "channel",
    "upload_date": "upload_date",
    "date": "upload_date>%Y-%m-%d",
    "year": "upload_date>%Y",
    "ext": "ext",
}
DEFAULT_FILENAME_TEMPLATE = "{title} [{video_id}]"
MAX_FILENAME_TEMPLATE = 200
SUBTITLE_EXTS = frozenset({"vtt", "srt", "ass", "ssa", "ttml", "srv1", "srv2", "srv3",
                           "json3", "lrc"})

# manual: tracks a person wrote; automatic: YouTube's own recogniser, in the
# video's language and translated into others; automatic_original: only the
# recogniser's track in the video's own language.
CAPTION_KINDS = ("manual", "automatic", "automatic_original")
DEFAULT_CAPTION_KINDS = ["manual", "automatic"]

PROGRESS_MARK = "ARCHIVIST-PROGRESS"
DONE_MARK = "ARCHIVIST-DONE"
_UNSAFE_LITERAL = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
_PLAIN_PERCENT = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)%")


def output_template(filename: Optional[str] = None) -> str:
    """The yt-dlp output template for a file name built from :data:`FILENAME_FIELDS`.

    ``filename`` is the name without its extension, such as
    ``"{title} ({year}) [{video_id}]"``. Characters a file name may not
    hold (path separators, control characters and the ones Windows refuses)
    are replaced with ``_`` in the literal text, so the name can never leave
    the folder it is written to. Raises ``ValueError`` for an unknown field,
    a format spec or conversion, a template with no field, or one longer
    than :data:`MAX_FILENAME_TEMPLATE`.
    """
    import string

    template = DEFAULT_FILENAME_TEMPLATE if filename is None else filename
    if not template.strip() or len(template) > MAX_FILENAME_TEMPLATE:
        raise ValueError(f"filename must be 1 to {MAX_FILENAME_TEMPLATE} characters")
    parts = []
    fields = 0
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as e:
        raise ValueError(f"filename is not a valid template: {e}") from None
    for literal, field_name, spec, conversion in parsed:
        parts.append(_UNSAFE_LITERAL.sub("_", literal).replace("%", "%%"))
        if field_name is None:
            continue
        if field_name not in FILENAME_FIELDS:
            raise ValueError(f"unknown filename field {{{field_name}}}; expected one of "
                             + ", ".join(f"{{{f}}}" for f in FILENAME_FIELDS))
        if spec or conversion:
            raise ValueError(f"filename field {{{field_name}}} takes no format spec")
        parts.append(f"%({FILENAME_FIELDS[field_name]})s")
        fields += 1
    if not fields:
        raise ValueError("filename must use at least one field, such as {video_id}")
    name = "".join(parts).strip().lstrip(".")
    return f"{name}.%(ext)s"


def ytdlp_command() -> Optional[List[str]]:
    """How to run yt-dlp as a program: the binary on ``PATH``, else ``python -m yt_dlp``."""
    import importlib.util
    import sys

    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def download_argv(url: str, dest_dir: str, *, filename: Optional[str] = None,
                  format: Optional[str] = None, subtitles: Optional[List[str]] = None,
                  subtitles_kinds: Optional[List[str]] = None, info_json: bool = False,
                  ip_family: str = "auto") -> List[str]:
    """yt-dlp's arguments for one download into ``dest_dir``.

    Progress is printed one line per update as ``ARCHIVIST-PROGRESS
    <downloaded> <total> <estimate>`` (``NA`` where unknown), and the
    finished video as ``ARCHIVIST-DONE <json>`` with its ``filepath``,
    ``title``, ``id`` and ``ext``; :func:`parse_download_line` reads both.

    ``subtitles_kinds`` (see :data:`CAPTION_KINDS`) picks which caption tracks
    are asked for; without it, manual and automatic tracks together. The
    video's info JSON is written beside the video when captions are asked
    for or ``info_json`` is true, so :func:`classify_captions` can read it.
    """
    argv = [
        "--newline", "--progress", "--no-playlist", "--no-colors",
        "--socket-timeout", "60",
        "--trim-filenames", "200",
        "--progress-template",
        (f"download:{PROGRESS_MARK} %(progress.downloaded_bytes)s "
         "%(progress.total_bytes)s %(progress.total_bytes_estimate)s"),
        "--print", f"after_move:{DONE_MARK} %(.{{filepath,title,id,ext}})j",
        "-f", format or default_download_format(),
        "-o", str(Path(dest_dir) / output_template(filename)),
    ]
    if ip_family == "ipv4":
        argv.append("--force-ipv4")
    elif ip_family == "ipv6":
        argv.append("--force-ipv6")
    kinds = subtitles_kinds or DEFAULT_CAPTION_KINDS
    if subtitles:
        langs = list(subtitles)
        if "automatic_original" in kinds:
            langs += [f"{lang}-orig" for lang in subtitles]
        if "manual" in kinds:
            argv.append("--write-subs")
        if "automatic" in kinds or "automatic_original" in kinds:
            argv.append("--write-auto-subs")
        argv += ["--sub-langs", ",".join(dict.fromkeys(langs))]
    if subtitles or info_json:
        argv.append("--write-info-json")
    argv += ["--", url]
    return argv


def _number(text: str) -> Optional[float]:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def parse_download_line(line: str) -> Optional[Dict[str, Any]]:
    """One line of yt-dlp's output as ``{"progress": …}`` or ``{"done": …}``.

    A progress line gives ``downloaded`` bytes and ``percent`` (``None``
    when yt-dlp knows no size); yt-dlp's plain ``[download]  42.0%`` line is
    read too. Any other line is ``None``.
    """
    line = line.strip()
    if line.startswith(PROGRESS_MARK + " "):
        fields = line[len(PROGRESS_MARK) + 1:].split()
        if len(fields) != 3:
            return None
        downloaded, total, estimate = (_number(f) for f in fields)
        total = total or estimate
        percent = min(100.0, downloaded * 100 / total) if downloaded is not None and total else None
        return {"progress": {"downloaded": downloaded, "percent": percent}}
    if line.startswith(DONE_MARK + " "):
        try:
            done = json.loads(line[len(DONE_MARK) + 1:])
        except ValueError:
            return None
        return {"done": done} if isinstance(done, dict) else None
    m = _PLAIN_PERCENT.match(line)
    if m:
        return {"progress": {"downloaded": None, "percent": min(100.0, float(m[1]))}}
    return None


def _is_translated_url(tracks: Any) -> bool:
    """Whether the track yt-dlp downloads from this list (the last) is a translation."""
    last = tracks[-1] if isinstance(tracks, list) and tracks else tracks
    return isinstance(last, dict) and bool(re.search(r"[?&]tlang=", str(last.get("url") or "")))


def classify_captions(info: Dict[str, Any], tracks: Iterable[str], *, manual: bool = True,
                      automatic: bool = True) -> Dict[str, Dict[str, Any]]:
    """What each of the caption tracks ``tracks`` is, from yt-dlp's info JSON.

    ``tracks`` are the track names of the caption files yt-dlp wrote. The
    info JSON does not say which tracks were requested, so the files are the
    starting point and the video's ``subtitles`` and ``automatic_captions``
    say what each is. A track is ``manual`` when ``manual`` was asked for and
    the video has a manual track of that name (yt-dlp prefers it), else
    ``automatic`` when ``automatic`` was asked for and the video lists one;
    any other name is left out. ``language`` is the track name without
    YouTube's ``-orig`` mark. ``is_translation`` is false for a manual track
    and for an automatic one that is the recogniser's own (the ``-orig``
    track, or the plain track of a language that has one), true for one whose
    address carries ``tlang``, and ``None`` where the info cannot tell.
    """
    subs = info.get("subtitles") if isinstance(info.get("subtitles"), dict) else {}
    auto = info.get("automatic_captions") if isinstance(info.get("automatic_captions"), dict) else {}
    originals = {k[:-5] for k in auto if k.endswith("-orig")}
    out: Dict[str, Dict[str, Any]] = {}
    for track in tracks:
        if manual and track in subs:
            out[track] = {"kind": "manual", "language": track, "is_translation": False}
        elif automatic and track in auto:
            language = track[:-5] if track.endswith("-orig") else track
            translated: Optional[bool]
            if _is_translated_url(auto[track]):
                translated = True
            elif language in originals:
                translated = False
            else:
                translated = None
            out[track] = {"kind": "automatic", "language": language, "is_translation": translated}
    return out
