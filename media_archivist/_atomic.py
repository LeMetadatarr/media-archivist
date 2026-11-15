"""Atomic file writes for the on-disk database and its sidecars.

Every persisted JSON/JSONL file (the envelope and each derived sidecar) is
written by serializing into a temporary file in the *same directory*, flushing
it to disk, and then :func:`os.replace`-ing it onto the destination. Because
``os.replace`` is atomic on a single filesystem, a crash at any instant leaves
the destination either old-complete or new-complete — never truncated or
half-written. The same-directory temp file is mandatory: ``os.replace`` must
not cross filesystem boundaries. The temp file name is unique per call
(:func:`tempfile.mkstemp`), so concurrent writers to the same path never
collide on each other's temp file. After the replace, the containing
directory is itself fsync'd, so the rename survives a power loss, not just a
process crash.

Serialization happens *before* the destination is touched, so a payload that
fails to serialize raises without disturbing any pre-existing file, and the
temporary file is always removed on failure.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def atomic_write_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write *text* to *path*.

    The content is written to a unique temp file in the same directory
    (:func:`tempfile.mkstemp`), fsync'd, then :func:`os.replace`-d onto
    *path*; the directory is fsync'd afterwards so the rename is durable
    across a power loss, not just a process crash. Parent directories are
    created if missing. On any failure after the temp file is created, the
    temp file is unlinked and the original *path* is left untouched.
    """
    path = os.path.expanduser(path)
    target_dir = os.path.dirname(path) or "."
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=f"{os.path.basename(path)}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(target_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: str, payload: Any, *, indent: int = 4,
                      sort_keys: bool = False) -> None:
    """Atomically write *payload* to *path* as UTF-8 JSON.

    Serializes to a string *before* touching the destination, so a payload
    containing an unserializable object raises before ``<path>`` is affected
    and no temp file is left behind. Uses ``ensure_ascii=False`` and the given
    ``indent`` to keep output byte-stable for unchanged data. Delegates the
    write to :func:`atomic_write_text` (same-directory temp + ``os.replace``).
    """
    text = json.dumps(payload, indent=indent, ensure_ascii=False, sort_keys=sort_keys)
    atomic_write_text(path, text)
