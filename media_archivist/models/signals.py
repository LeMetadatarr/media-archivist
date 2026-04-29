"""Disambiguation signals — the bag of facts we compare to decide
whether two rows describe the *same work*.

Comparison rules (encoded in :func:`compare`):

- A signal absent on either side is **not** a disagreement.
- All overlapping signals must agree → matched.
- Any single overlapping signal disagrees → conflict (caller quarantines).

Tolerances are intentionally conservative; loosen them on a per-medium
basis at the call site if needed (e.g. live recordings vs studio cuts).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Tolerances (defaults)
# ---------------------------------------------------------------------------
TITLE_FUZZY_MIN = 0.92
ARTIST_FUZZY_MIN = 0.90
YEAR_TOLERANCE = 1                 # years
RUNTIME_TOLERANCE_S = 5.0          # seconds


class Medium(str, Enum):
    MOVIE = "movie"
    TV = "tv"
    MUSIC = "music"
    BOOK = "book"
    PODCAST = "podcast"
    OTHER = "other"


class Signals(BaseModel):
    """Bag of signals extracted from a row, normalized for comparison."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    artist: Optional[str] = None       # for music/podcast: artist; for video: director
    year: Optional[int] = None
    country: Optional[str] = None      # ISO 3166-1 alpha-2
    runtime: Optional[float] = None    # seconds
    medium: Optional[Medium] = None
    language: Optional[str] = None     # ISO 639-1


class SignalConflict(BaseModel):
    """Single-field disagreement between two Signals bags."""

    model_config = ConfigDict(extra="forbid")

    signal: str
    ours: Any
    theirs: Any


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    import re
    text = (text or "").lower()
    text = re.sub(r"\s*(?:\(|\[)?\s*(?:feat|ft|featuring)\.?\s+[^)\]]*[)\]]?",
                  " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


# ---------------------------------------------------------------------------
# Pairwise comparison
# ---------------------------------------------------------------------------

def _agree_year(a: int, b: int, tolerance: int = YEAR_TOLERANCE) -> bool:
    return abs(int(a) - int(b)) <= tolerance


def _agree_runtime(a: float, b: float, tolerance: float = RUNTIME_TOLERANCE_S) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def _agree_string(a: str, b: str, threshold: float) -> bool:
    return fuzzy_ratio(a, b) >= threshold


def compare(ours: Signals, theirs: Signals) -> List[SignalConflict]:
    """Return the list of *overlapping* signals that disagree.

    Empty list ⇒ matched (no overlap counts as agreement, by design).
    """
    conflicts: List[SignalConflict] = []

    if ours.title and theirs.title and not _agree_string(
        ours.title, theirs.title, TITLE_FUZZY_MIN
    ):
        conflicts.append(SignalConflict(signal="title", ours=ours.title,
                                        theirs=theirs.title))

    if ours.artist and theirs.artist and not _agree_string(
        ours.artist, theirs.artist, ARTIST_FUZZY_MIN
    ):
        conflicts.append(SignalConflict(signal="artist", ours=ours.artist,
                                        theirs=theirs.artist))

    if ours.year is not None and theirs.year is not None and not _agree_year(
        ours.year, theirs.year
    ):
        conflicts.append(SignalConflict(signal="year", ours=ours.year,
                                        theirs=theirs.year))

    if ours.country and theirs.country and ours.country.upper() != theirs.country.upper():
        conflicts.append(SignalConflict(signal="country", ours=ours.country,
                                        theirs=theirs.country))

    if ours.runtime is not None and theirs.runtime is not None and not _agree_runtime(
        ours.runtime, theirs.runtime
    ):
        conflicts.append(SignalConflict(signal="runtime", ours=ours.runtime,
                                        theirs=theirs.runtime))

    if ours.medium and theirs.medium and ours.medium != theirs.medium:
        conflicts.append(SignalConflict(signal="medium",
                                        ours=ours.medium.value,
                                        theirs=theirs.medium.value))

    if ours.language and theirs.language and ours.language.lower() != theirs.language.lower():
        conflicts.append(SignalConflict(signal="language", ours=ours.language,
                                        theirs=theirs.language))

    return conflicts


def merged(*bags: Signals) -> Signals:
    """First non-None value wins, per field."""
    fields = ("title", "artist", "year", "country", "runtime", "medium", "language")
    out: Dict[str, Any] = {}
    for f in fields:
        for b in bags:
            v = getattr(b, f, None)
            if v not in (None, ""):
                out[f] = v
                break
    return Signals(**out)


def signal_hash(s: Signals) -> str:
    """A stable hash over the immutable signals — used as the canonical_id seed."""
    import hashlib
    parts = [
        _normalize_text(s.title or ""),
        _normalize_text(s.artist or ""),
        str(s.year) if s.year is not None else "",
        (s.country or "").upper(),
        f"{round(s.runtime):d}" if s.runtime is not None else "",
        s.medium.value if s.medium else "",
        (s.language or "").lower(),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
