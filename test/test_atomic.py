"""Atomic-write helper and the canonicalize write-ordering contract — offline.

Covers truncation safety of :mod:`media_archivist._atomic` and the
sidecars-first / envelope-last ordering enforced by ``canonicalize()``.
"""
from __future__ import annotations

import threading

import pytest

from mediavocab import MediaType
from mediavocab.models import ExternalIds
from mediavocab.models.signals import Signals
from metadatarr.resolve.base import (
    MetadataProvider,
    ProviderMatch,
    _REGISTRY,
    register,
)

from media_archivist import _atomic
from media_archivist._atomic import atomic_write_json, atomic_write_text
from media_archivist.canonicalize import canonicalize
from media_archivist.storage import EnvelopeJsonStorage


# ---------------------------------------------------------------------------
# _atomic helper
# ---------------------------------------------------------------------------

def test_atomic_write_replaces_only_on_success(tmp_path):
    p = tmp_path / "data.json"
    atomic_write_json(str(p), {"a": 1})
    assert p.read_text() == '{\n    "a": 1\n}'
    # A second write replaces atomically and leaves no temp litter.
    atomic_write_json(str(p), {"a": 2, "b": [1, 2, 3]})
    assert p.read_text() == '{\n    "a": 2,\n    "b": [\n        1,\n        2,\n        3\n    ]\n}'
    assert [f.name for f in tmp_path.iterdir()] == ["data.json"]


def test_serialization_error_leaves_original_intact(tmp_path):
    p = tmp_path / "data.json"
    original = '{\n    "keep": "me"\n}'
    p.write_text(original)

    with pytest.raises(TypeError):
        atomic_write_json(str(p), {"bad": object()})

    # Original bytes untouched and no temp file left behind.
    assert p.read_text() == original
    assert [f.name for f in tmp_path.iterdir()] == ["data.json"]


def test_crash_between_temp_and_replace_leaves_original(tmp_path, monkeypatch):
    p = tmp_path / "data.json"
    original = "original-contents"
    p.write_text(original)

    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(_atomic.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(str(p), "new-contents")

    assert p.read_text() == original
    # The temp file must be cleaned up even though replace failed.
    assert [f.name for f in tmp_path.iterdir()] == ["data.json"]


def test_concurrent_writes_never_collide_on_temp_name(tmp_path):
    """Two threads hammering the same path must never see a torn temp file.

    Reproduces the review's race: a temp name shared across concurrent
    writers to the same path (e.g. one keyed only by pid) lets one thread's
    ``os.replace``/cleanup race another's open temp file, raising
    ``FileNotFoundError``. With a unique-per-call temp file (mkstemp) this
    never happens, and the file always holds one payload's full contents.
    """
    p = tmp_path / "shared.txt"
    payload_a = "A" * 37
    payload_b = "B" * 41
    errors = []

    def hammer(payload, n):
        for _ in range(n):
            try:
                atomic_write_text(str(p), payload)
            except Exception as exc:  # noqa: BLE001 - collecting for the assertion below
                errors.append(exc)

    t1 = threading.Thread(target=hammer, args=(payload_a, 300))
    t2 = threading.Thread(target=hammer, args=(payload_b, 300))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    assert p.read_text() in (payload_a, payload_b)


# ---------------------------------------------------------------------------
# canonicalize() ordering contract
# ---------------------------------------------------------------------------

class _StubProvider(MetadataProvider):
    name = "stub"
    media = {MediaType.MUSIC}

    def __init__(self, response=None, available=True):
        self.response = response
        self._available = available

    def is_available(self):
        return self._available

    def lookup(self, signals):
        return self.response


@pytest.fixture
def stub_registered():
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _seed_db(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "Hello",
               "artist": "Foo", "duration": 240}
    db.store()
    register(_StubProvider(ProviderMatch(
        provider="stub", confidence=0.95,
        signals=Signals(title="Hello", artist="Foo", year=1999, runtime=240,
                        medium=MediaType.MUSIC),
        external_ids=ExternalIds(musicbrainz_recording="mb-1"),
    )))
    return db_path


def test_canonicalize_single_store_at_end(tmp_path, stub_registered, monkeypatch):
    db_path = _seed_db(tmp_path)

    calls = {"n": 0}
    real_store = EnvelopeJsonStorage.store

    def counting_store(self, path=None):
        calls["n"] += 1
        return real_store(self, path)

    monkeypatch.setattr(EnvelopeJsonStorage, "store", counting_store)
    canonicalize(str(db_path), providers=["stub"])
    assert calls["n"] == 1


def test_write_order_sidecars_before_envelope(tmp_path, stub_registered, monkeypatch):
    db_path = _seed_db(tmp_path)

    order = []
    import media_archivist.canonicalize as cz

    def rec(name, fn):
        def wrapped(*args, **kwargs):
            order.append(name)
            return fn(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(cz, "save_entities", rec("entities", cz.save_entities))
    monkeypatch.setattr(cz, "save_canonical", rec("canonical", cz.save_canonical))
    monkeypatch.setattr(cz, "save_quarantine", rec("quarantine", cz.save_quarantine))
    real_store = EnvelopeJsonStorage.store

    def store_recording(self, path=None):
        order.append("envelope")
        return real_store(self, path)

    monkeypatch.setattr(EnvelopeJsonStorage, "store", store_recording)

    canonicalize(str(db_path), providers=["stub"])

    assert order == ["entities", "canonical", "quarantine", "envelope"]
