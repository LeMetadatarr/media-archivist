"""Progress bar utility shared by every backend.

Wraps :func:`tqdm.tqdm`. Bars print to stderr so stdout stays clean for
``urls`` / ``export`` pipelines. Disabled automatically when stderr is not
a TTY (e.g. piped into a log file or running under CI), unless explicitly
forced via the :envvar:`MEDIA_ARCHIVIST_PROGRESS` environment variable
(``always`` = force on, ``never`` = force off).
"""
from __future__ import annotations

import os
import sys
from typing import Iterable, Iterator, Optional, TypeVar

try:
    from tqdm import tqdm as _tqdm
except ImportError:  # pragma: no cover
    _tqdm = None  # type: ignore[assignment]

T = TypeVar("T")


def _enabled() -> bool:
    override = os.environ.get("MEDIA_ARCHIVIST_PROGRESS", "").lower()
    if override == "always":
        return True
    if override == "never":
        return False
    if _tqdm is None:
        return False
    return sys.stderr.isatty()


def progress(iterable: Iterable[T], *, desc: str = "",
             total: Optional[int] = None, unit: str = "it") -> Iterator[T]:
    """Yield items from ``iterable`` while drawing a progress bar.

    Falls back to a plain pass-through when tqdm is unavailable, the stderr
    stream is not a TTY, or ``MEDIA_ARCHIVIST_PROGRESS=never`` is set.
    """
    if not _enabled():
        yield from iterable
        return
    bar = _tqdm(iterable, desc=desc or None, total=total, unit=unit,
                file=sys.stderr, leave=False, dynamic_ncols=True)
    try:
        for item in bar:
            yield item
    finally:
        bar.close()
