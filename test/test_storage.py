"""Envelope-aware storage: empty-file init, envelope round-trip, format rejection."""
from __future__ import annotations

import filecmp
import json
import shutil

import pytest
from pydantic import ValidationError

from media_archivist.storage import EnvelopeJsonStorage


def test_empty_path_initialises_meta(tmp_path):
    p = tmp_path / "fresh.json"
    db = EnvelopeJsonStorage(str(p))
    assert len(db) == 0
    assert db.meta.schema_version == 2
    db.store()
    on_disk = json.loads(p.read_text())
    assert "_meta" in on_disk and "entries" in on_disk


def test_rejects_bare_mapping_files(tmp_path):
    """Files in the legacy bare-mapping shape are rejected; envelope is mandatory."""
    bad = tmp_path / "db.json"
    bad.write_text(json.dumps({
        "https://www.youtube.com/watch?v=a": {
            "source": "youtube",
            "url": "https://www.youtube.com/watch?v=a",
            "videoId": "a", "title": "t",
        }
    }))
    with pytest.raises(ValueError):
        EnvelopeJsonStorage(str(bad))


def test_envelope_round_trip(tmp_path):
    p = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(p))
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "Track",
    }
    db.store()
    db2 = EnvelopeJsonStorage(str(p))
    assert "https://x.bandcamp.com/track/y" in db2
    assert db2.meta.source_mix == {"bandcamp": 1}
    assert db2.meta.last_synced is not None


def test_store_roundtrip_byte_stable(tmp_path, monkeypatch):
    """Storing an unchanged envelope reproduces identical file bytes.

    The canonicalize stamping's minimal-diff property depends on a stable,
    deterministic serialization: reloading a stored envelope and re-storing it
    (without mutating anything that ``touch()``/``recompute_source_mix()``
    would change) must yield a byte-for-byte identical file.
    """
    # Freeze the sync timestamp so the only variable under test is the
    # serialization itself, not the wall clock.
    import media_archivist.models.archive as archive_mod
    monkeypatch.setattr(archive_mod, "_utcnow", lambda: "2026-01-01T00:00:00+00:00")

    p = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(p))
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "Track",
        "artist": "Someone",
        "duration": 123,
    }
    db.store()
    reference = tmp_path / "db-reference.json"
    shutil.copyfile(p, reference)

    # Reload and re-store the same content: bytes must be identical.
    db2 = EnvelopeJsonStorage(str(p))
    db2.store()
    assert filecmp.cmp(p, reference, shallow=False)
    assert p.read_bytes() == reference.read_bytes()
