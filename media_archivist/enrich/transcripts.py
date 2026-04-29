"""YouTube transcript enrichment via ``yt-dlp`` + a tiny VTT parser.

We shell out to ``yt-dlp --skip-download --write-auto-subs --write-subs
--convert-subs vtt`` so the actual cookies / impersonation policy stays
in yt-dlp. Then parse the resulting VTT into
:class:`TranscriptCue` objects.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

from media_archivist.models.enriched import TranscriptBlock, TranscriptCue

LOG = logging.getLogger("media_archivist.enrich.transcripts")

_VTT_TIME = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})"
    r"\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})"
)


def _has_yt_dlp() -> bool:
    return shutil.which("yt-dlp") is not None


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(text: str) -> List[TranscriptCue]:
    """Lightweight VTT parser — captures time ranges and concatenated text."""
    cues: List[TranscriptCue] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        time_match = next((m for m in (_VTT_TIME.match(ln) for ln in lines) if m), None)
        if time_match is None:
            continue
        start = _to_seconds(time_match["h"], time_match["m"],
                            time_match["s"], time_match["ms"])
        end = _to_seconds(time_match["eh"], time_match["em"],
                          time_match["es"], time_match["ems"])
        text_lines = [ln for ln in lines
                      if not _VTT_TIME.match(ln) and ln.strip().upper() != "WEBVTT"]
        # Strip simple WebVTT formatting tags.
        text = " ".join(re.sub(r"<[^>]+>", "", ln) for ln in text_lines).strip()
        if text:
            cues.append(TranscriptCue(start=start, end=end, text=text))
    return cues


def fetch_youtube_transcript(url: str, *,
                             languages: Iterable[str] = ("en",)
                             ) -> Optional[TranscriptBlock]:
    """Return a :class:`TranscriptBlock` for the URL, or ``None``."""
    if not _has_yt_dlp():
        LOG.warning("yt-dlp not on PATH — transcript enrichment skipped")
        return None
    langs = ",".join(languages)
    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / "%(id)s.%(ext)s")
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-langs", langs,
            "--sub-format", "vtt",
            "--convert-subs", "vtt",
            "-o", out_template,
            url,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            LOG.warning("yt-dlp transcript fetch failed for %s: %s", url, e)
            return None

        vtt_files = sorted(Path(tmp).glob("*.vtt"))
        if not vtt_files:
            return None
        vtt = vtt_files[0]
        # Filename is "<id>.<lang>.vtt"; pull the lang token.
        parts = vtt.name.rsplit(".vtt", 1)[0].split(".")
        language = parts[-1] if len(parts) > 1 else next(iter(languages))
        auto = "auto" in vtt.name.lower() or any("auto" in p for p in parts)
        cues = parse_vtt(vtt.read_text(encoding="utf-8"))
        if not cues:
            return None
        return TranscriptBlock(language=language, auto_generated=auto, cues=cues)
