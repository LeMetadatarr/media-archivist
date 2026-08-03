"""Deprecated alias for :mod:`media_archivist.dedupe`.

The local fuzzy-dedupe helpers moved from ``media_archivist.canon`` to
``media_archivist.dedupe`` so the name no longer collides with
``media_archivist.canonicalize`` (the provider-backed canonical resolver).
Import from :mod:`media_archivist.dedupe` instead; this shim re-exports the
same names and will be removed in a future release.
"""
from __future__ import annotations

import warnings

from media_archivist.dedupe import (  # noqa: F401
    build_links,
    dedupe,
    durations_match,
    fingerprint,
    link,
    read_links_sidecar,
    write_dedupe_jsonl,
    write_links_sidecar,
)

warnings.warn(
    "media_archivist.canon is deprecated; import from media_archivist.dedupe "
    "instead. This alias will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "build_links",
    "dedupe",
    "durations_match",
    "fingerprint",
    "link",
    "read_links_sidecar",
    "write_dedupe_jsonl",
    "write_links_sidecar",
]
