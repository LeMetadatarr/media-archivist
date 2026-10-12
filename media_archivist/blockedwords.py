# SPDX-License-Identifier: Apache-2.0
"""Title words and phrases that leave a video out of a query.

A word matches a title the way a person reading it would expect: case,
hyphens, underscores and runs of spaces are folded (after NFKC
normalisation, so full-width and compatibility forms read as their plain
letters), a word never matches inside a longer word, and its plural with
``-s`` or ``-es`` matches too. ``behind the scenes`` matches
"Behind-the-Scenes", ``trailer`` matches "Official Trailers" but not
"Trailblazer". Accents are kept: ``café`` does not match "cafe".
"""
from __future__ import annotations

import functools
import re
import unicodedata
from typing import Iterable, List, Optional


def fold(text) -> str:
    return re.sub(r"[\s_-]+", " ", unicodedata.normalize("NFKC", str(text)).casefold()).strip()


@functools.lru_cache(maxsize=512)
def _pattern(word: str):
    return re.compile(rf"(?<!\w){re.escape(fold(word))}(?:e?s)?(?!\w)")


def blocked(title, words: Optional[Iterable[str]]) -> Optional[str]:
    """The first of ``words`` found in ``title``, as given; ``None`` when there is none."""
    t = fold(title or "")
    return next((w for w in words or () if fold(w) and _pattern(w).search(t)), None)


def clean_words(words) -> List[str]:
    """``words`` trimmed and without repeats (compared folded).

    Raises ``ValueError`` for anything but a list of non-empty strings.
    """
    if not isinstance(words, (list, tuple)) or not all(isinstance(w, str) and w.strip() for w in words):
        raise ValueError("blocked words must be a list of non-empty words or phrases")
    seen, out = set(), []
    for w in (w.strip() for w in words):
        if fold(w) not in seen:
            seen.add(fold(w))
            out.append(w)
    return out
