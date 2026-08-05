# SPDX-License-Identifier: Apache-2.0
"""media_archivist.subscriptions — sidecar store, backend inference, sync.

No network: sync_subscription/sync_all patch _archivist_class so
`.archive(url)` is a fake no-op that "adds" N rows.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from media_archivist import subscriptions as subs_mod
from media_archivist.storage import EnvelopeJsonStorage


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db.store()
    return str(path)


def _fake_archivist_cls(rows_to_add=1, raises=None):
    """Return a fake JsonArchivist-shaped class for patching _archivist_class."""

    class _Fake:
        def __init__(self, db_path):
            self.db_path = db_path
            self._db = EnvelopeJsonStorage(db_path)
            self._n = 0

        @property
        def video_urls(self):
            return list(self._db.keys()) + [f"fake:{i}" for i in range(self._n)]

        def archive(self, url):
            if raises is not None:
                raise raises
            self._n += rows_to_add

    return _Fake


# ---------------------------------------------------------------------------
# sidecar round-trip
# ---------------------------------------------------------------------------

def test_add_then_load_round_trips(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    assert sub.backend == "youtube"
    loaded = subs_mod.list_subscriptions(db_path)
    assert len(loaded) == 1
    assert loaded[0].url == "https://www.youtube.com/@chan"


def test_save_load_sidecar_file_exists(db_path, tmp_path):
    subs_mod.add_subscription(db_path, "https://archive.org/details/foo", label="Foo")
    sidecar_path = tmp_path / "db.subscriptions.json"
    assert sidecar_path.exists()
    reloaded = subs_mod.load_subscriptions(db_path)
    assert reloaded.subscriptions[0].label == "Foo"


def test_remove_subscription(db_path):
    subs_mod.add_subscription(db_path, "https://soundcloud.com/artist")
    assert subs_mod.remove_subscription(db_path, "https://soundcloud.com/artist") is True
    assert subs_mod.list_subscriptions(db_path) == []


def test_remove_nonexistent_returns_false(db_path):
    assert subs_mod.remove_subscription(db_path, "https://nope.example/x") is False


def test_add_dedupes_by_url(db_path):
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan", label="A")
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan", label="B")
    subs = subs_mod.list_subscriptions(db_path)
    assert len(subs) == 1
    assert subs[0].label == "B"


# ---------------------------------------------------------------------------
# backend inference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/@SomeChannel", "youtube"),
    ("https://youtu.be/abc123", "youtube"),
    ("https://music.youtube.com/channel/xyz", "music"),
    ("https://archive.org/details/some_collection", "ia"),
    ("https://someartist.bandcamp.com", "bandcamp"),
    ("https://soundcloud.com/someartist", "soundcloud"),
])
def test_infer_backend(url, expected):
    assert subs_mod.infer_backend(url) == expected


def test_infer_backend_unknown_url_returns_none():
    assert subs_mod.infer_backend("https://example.com/whatever") is None


def test_add_subscription_unknown_backend_raises(db_path):
    with pytest.raises(ValueError):
        subs_mod.add_subscription(db_path, "https://example.com/whatever")


def test_add_subscription_explicit_backend_overrides_inference(db_path):
    sub = subs_mod.add_subscription(db_path, "https://example.com/whatever", backend="ia")
    assert sub.backend == "ia"


# ---------------------------------------------------------------------------
# sync_subscription / sync_all
# ---------------------------------------------------------------------------

def test_sync_subscription_adds_rows_and_stamps(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(rows_to_add=3)):
        result = subs_mod.sync_subscription(db_path, sub, dry_run=False)
    assert result.ok is True
    assert result.rows_added == 3
    assert sub.last_synced_at is not None
    assert sub.last_rows_added == 3
    assert sub.last_error is None


def test_sync_subscription_dry_run_archives_nothing(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    fake_cls = _fake_archivist_cls(rows_to_add=5)
    with patch.object(subs_mod, "_archivist_class", return_value=fake_cls) as m:
        result = subs_mod.sync_subscription(db_path, sub, dry_run=True)
    assert result.ok is True
    assert result.dry_run is True
    assert result.rows_added == 0
    assert sub.last_synced_at is None  # untouched
    m.assert_not_called()


def test_sync_subscription_captures_error_without_raising(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    boom = RuntimeError("network exploded")
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls(raises=boom)):
        result = subs_mod.sync_subscription(db_path, sub, dry_run=False)
    assert result.ok is False
    assert "network exploded" in result.error
    assert sub.last_error is not None
    # last_synced_at is still stamped so the UI shows *when* it last tried.
    assert sub.last_synced_at is not None


def test_sync_all_syncs_every_subscription_and_persists(db_path):
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan1")
    subs_mod.add_subscription(db_path, "https://archive.org/details/coll")
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(rows_to_add=2)):
        results = subs_mod.sync_all(db_path, dry_run=False)
    assert len(results) == 2
    assert all(r.ok and r.rows_added == 2 for r in results)
    reloaded = subs_mod.list_subscriptions(db_path)
    assert all(s.last_rows_added == 2 for s in reloaded)


def test_sync_all_one_failure_does_not_abort_others(db_path):
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@ok")
    subs_mod.add_subscription(db_path, "https://archive.org/details/bad")

    good_cls = _fake_archivist_cls(rows_to_add=1)
    bad_cls = _fake_archivist_cls(raises=RuntimeError("boom"))

    def _select(backend):
        return bad_cls if backend == "ia" else good_cls

    with patch.object(subs_mod, "_archivist_class", side_effect=_select):
        results = subs_mod.sync_all(db_path, dry_run=False)
    assert len(results) == 2
    by_backend = {r.backend: r for r in results}
    assert by_backend["youtube"].ok is True
    assert by_backend["ia"].ok is False


def test_sync_all_dry_run_leaves_sidecar_untouched(db_path):
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(rows_to_add=9)) as m:
        results = subs_mod.sync_all(db_path, dry_run=True)
    assert all(r.dry_run for r in results)
    reloaded = subs_mod.list_subscriptions(db_path)
    assert reloaded[0].last_synced_at is None
    m.assert_not_called()
