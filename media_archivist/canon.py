"""Cross-source canonicalization — fingerprint, link, dedupe.

The fingerprint is computed from ``(artist, title, duration±tolerance)``
on the canonical :class:`MediaEntry` view. Two entries from different
sources sharing the same fingerprint are *almost certainly* the same
piece of media.

``link`` writes a sidecar ``<dbfile>.links.json`` mapping fingerprint →
list of entry ids. ``dedupe`` reads view + sidecar and emits a deduped
canonical JSONL with a configurable source-preference order.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from media_archivist.index import Index
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source

DEFAULT_DURATION_TOLERANCE_S = 2.0
DEFAULT_PREFERENCE: Sequence[str] = (
    "bandcamp", "internet_archive", "youtube_music", "soundcloud", "youtube",
)

_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
_FEAT_RE = re.compile(r"\s*(?:\(|\[)?\s*(?:feat|ft|featuring)\.?\s+[^)\]]*[)\]]?",
                      re.IGNORECASE)
_PARENS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = _FEAT_RE.sub(" ", text)
    text = _PARENS_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def fingerprint(entry: MediaEntry) -> str:
    """Return a deterministic fingerprint for cross-source matching.

    Computed over normalized ``(artist, title)`` only — duration is used
    as a soft guard at dedupe time (see :func:`durations_match`) because
    fixed bucketing has unsolvable boundary cases.
    """
    artist = _normalize(entry.artist or "")
    title = _normalize(entry.title or "")
    key = f"{artist}|{title}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def durations_match(a: Optional[float], b: Optional[float],
                    tolerance: float = DEFAULT_DURATION_TOLERANCE_S) -> bool:
    """True if either side is missing, or both sides are within ``tolerance`` seconds."""
    if a is None or b is None:
        return True
    return abs(a - b) <= tolerance


def build_links(entries: Iterable[MediaEntry],
                duration_tolerance: float = DEFAULT_DURATION_TOLERANCE_S) -> Dict[str, List[str]]:
    """Group entry ids by fingerprint. Only fingerprints with ≥2 entries are kept.

    Within a group, candidates whose duration disagrees with the rest by
    more than ``duration_tolerance`` seconds are split into a separate
    group keyed ``<fp>:<n>``.
    """
    by_fp: Dict[str, List[MediaEntry]] = defaultdict(list)
    for entry in entries:
        if not (entry.title and entry.artist):
            continue
        by_fp[fingerprint(entry)].append(entry)

    links: Dict[str, List[str]] = {}
    for fp, candidates in by_fp.items():
        if len(candidates) < 2:
            continue
        # Greedy clustering by duration tolerance.
        clusters: List[List[MediaEntry]] = []
        for c in candidates:
            placed = False
            for cl in clusters:
                if all(durations_match(c.duration, m.duration, duration_tolerance)
                       for m in cl):
                    cl.append(c)
                    placed = True
                    break
            if not placed:
                clusters.append([c])
        for i, cl in enumerate(clusters):
            if len(cl) < 2:
                continue
            key = fp if i == 0 else f"{fp}:{i}"
            links[key] = [m.id for m in cl]
    return links


def write_links_sidecar(db_path: str, links: Dict[str, List[str]]) -> Path:
    """Write ``<db>.links.json`` next to the source DB."""
    sidecar = Path(db_path).with_suffix(".links.json")
    with sidecar.open("w", encoding="utf-8") as f:
        json.dump(links, f, indent=2, ensure_ascii=False, sort_keys=True)
    return sidecar


def read_links_sidecar(db_path: str) -> Dict[str, List[str]]:
    sidecar = Path(db_path).with_suffix(".links.json")
    if not sidecar.exists():
        return {}
    with sidecar.open(encoding="utf-8") as f:
        return json.load(f)


def link(db_path: str,
         duration_tolerance: float = DEFAULT_DURATION_TOLERANCE_S) -> Dict[str, List[str]]:
    """Compute fingerprints and write the sidecar. Returns the link map."""
    idx = Index(db_path)
    links = build_links(idx.view(), duration_tolerance=duration_tolerance)
    write_links_sidecar(db_path, links)
    return links


def _preference_rank(source: str, preference: Sequence[str]) -> int:
    try:
        return preference.index(source)
    except ValueError:
        return len(preference)


def dedupe(db_path: str, *,
           preference: Sequence[str] = DEFAULT_PREFERENCE,
           duration_tolerance: float = DEFAULT_DURATION_TOLERANCE_S
           ) -> List[MediaEntry]:
    """Return one canonical :class:`MediaEntry` per fingerprint group.

    Entries with no group (singletons) are kept as-is. Within a group the
    entry with the lowest preference rank wins; the discarded entries are
    attached as an ``alternates`` list under ``raw.alternates``.
    """
    idx = Index(db_path)
    entries: Dict[str, MediaEntry] = {e.id: e for e in idx.view()}
    links = build_links(entries.values(), duration_tolerance=duration_tolerance)

    canonical_ids: set[str] = set()
    out: List[MediaEntry] = []
    for ids in links.values():
        ranked = sorted(
            ids,
            key=lambda i: (_preference_rank(entries[i].source.value, preference),
                           entries[i].id),
        )
        winner = entries[ranked[0]].model_copy(deep=True)
        winner.raw["alternates"] = [
            {"id": i, "source": entries[i].source.value, "url": entries[i].url}
            for i in ranked[1:]
        ]
        out.append(winner)
        canonical_ids.update(ids)

    for e in entries.values():
        if e.id not in canonical_ids:
            out.append(e)
    return out


def write_dedupe_jsonl(entries: Iterable[MediaEntry], path: str) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(e.model_dump_json() + "\n")
            n += 1
    return n
