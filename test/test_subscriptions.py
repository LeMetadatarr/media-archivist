# SPDX-License-Identifier: Apache-2.0
"""media_archivist.subscriptions — sidecar store, backend inference, sync.

No network: sync_subscription/sync_all patch _archivist_class so
`.archive(url)` is a fake no-op that "adds" N rows.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# optional auto-download of newly-indexed items
# ---------------------------------------------------------------------------

def _fake_archivist_cls_with_urls(existing, new):
    """A fake archivist whose db pre-holds `existing` urls; archive() adds `new`."""

    class _Fake:
        def __init__(self, db_path):
            self._db = EnvelopeJsonStorage(db_path)
            for u in existing:
                self._db[u] = {"url": u}
            self._db.store()

        @property
        def video_urls(self):
            return list(self._db.keys())

        def archive(self, url):
            for u in new:
                self._db[u] = {"url": u}
            self._db.store()

    return _Fake


def test_sync_download_only_calls_downloader_for_new_entries(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    existing = ["https://www.youtube.com/watch?v=old1"]
    new = ["https://www.youtube.com/watch?v=new1", "https://www.youtube.com/watch?v=new2"]
    downloader = MagicMock()

    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls_with_urls(existing, new)), \
         patch("media_archivist.streams.ytdlp_available", return_value=True):
        result = subs_mod.sync_subscription(
            db_path, sub, download=True, download_dir="/tmp/dl", downloader=downloader,
        )

    assert result.ok is True
    assert sorted(result.new_urls) == sorted(new)
    called_urls = {c.args[0] for c in downloader.call_args_list}
    assert called_urls == set(new)
    assert "https://www.youtube.com/watch?v=old1" not in called_urls
    assert sorted(result.downloaded) == sorted(new)


def test_sync_dry_run_never_downloads(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    downloader = MagicMock()
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls_with_urls([], ["https://x/new"])), \
         patch("media_archivist.streams.ytdlp_available", return_value=True):
        result = subs_mod.sync_subscription(
            db_path, sub, dry_run=True, download=True, downloader=downloader,
        )
    assert result.dry_run is True
    downloader.assert_not_called()


def test_sync_download_skipped_when_ytdlp_unavailable_but_still_indexes(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    downloader = MagicMock()
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls_with_urls([], ["https://x/new"])), \
         patch("media_archivist.streams.ytdlp_available", return_value=False):
        result = subs_mod.sync_subscription(
            db_path, sub, download=True, downloader=downloader,
        )
    assert result.ok is True
    assert result.rows_added == 1
    assert result.new_urls == ["https://x/new"]
    downloader.assert_not_called()
    assert result.downloaded == []


def test_sync_download_failure_per_entry_does_not_abort_sync(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    new = ["https://x/ok", "https://x/bad"]

    def _downloader(url, dest):
        if url.endswith("/bad"):
            raise RuntimeError("boom")

    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls_with_urls([], new)), \
         patch("media_archivist.streams.ytdlp_available", return_value=True):
        result = subs_mod.sync_subscription(
            db_path, sub, download=True, downloader=_downloader,
        )
    assert result.ok is True
    assert result.downloaded == ["https://x/ok"]
    assert "https://x/bad" in result.download_errors
    assert "boom" in result.download_errors["https://x/bad"]


def test_sync_subscription_auto_download_flag_forces_download(db_path):
    sub = subs_mod.add_subscription(
        db_path, "https://www.youtube.com/@chan", auto_download=True,
    )
    downloader = MagicMock()
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls_with_urls([], ["https://x/new"])), \
         patch("media_archivist.streams.ytdlp_available", return_value=True):
        # download=False on the call — sub.auto_download alone should trigger it.
        subs_mod.sync_subscription(db_path, sub, download=False, downloader=downloader)
    downloader.assert_called_once()


def test_auto_download_persisted_in_sidecar(db_path):
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan", auto_download=True)
    reloaded = subs_mod.list_subscriptions(db_path)
    assert reloaded[0].auto_download is True


def test_auto_download_defaults_false_back_compat(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    assert sub.auto_download is False


# ---------------------------------------------------------------------------
# watch() — periodic auto-sync loop
# ---------------------------------------------------------------------------

def test_watch_runs_cycles_and_stops_via_event(db_path):
    calls = []

    def _fake_sync_all(path, **kwargs):
        calls.append(kwargs)
        return []

    stop_event = threading.Event()
    cycles_seen = []

    def _on_cycle(results):
        cycles_seen.append(results)
        if len(cycles_seen) >= 2:
            stop_event.set()

    with patch.object(subs_mod, "sync_all", side_effect=_fake_sync_all):
        subs_mod.watch(db_path, interval=0.01, stop_event=stop_event, on_cycle=_on_cycle)

    assert len(calls) >= 2
    assert len(cycles_seen) >= 2


def test_watch_stops_promptly_when_event_preset(db_path):
    stop_event = threading.Event()
    stop_event.set()  # already stopped before the loop starts
    with patch.object(subs_mod, "sync_all") as m:
        m.return_value = []
        start = time.time()
        subs_mod.watch(db_path, interval=5, stop_event=stop_event)
        elapsed = time.time() - start
    # Loop body's `while not event.is_set()` never runs.
    m.assert_not_called()
    assert elapsed < 1


def test_watch_rejects_nonpositive_interval(db_path):
    with pytest.raises(ValueError):
        subs_mod.watch(db_path, interval=0)


def test_watch_passes_download_flag_through_to_sync_all(db_path):
    stop_event = threading.Event()

    def _once(*args, **kwargs):
        stop_event.set()
        return []

    with patch.object(subs_mod, "sync_all", side_effect=_once) as m:
        subs_mod.watch(db_path, interval=0.01, download=True, stop_event=stop_event)
    _, kwargs = m.call_args
    assert kwargs["download"] is True


# ---------------------------------------------------------------------------
# notify firing on sync
# ---------------------------------------------------------------------------

def test_sync_subscription_notifies_on_new_rows(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan", label="Chan")
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls(rows_to_add=3)), \
         patch("media_archivist.notify.notify") as m:
        subs_mod.sync_subscription(db_path, sub, dry_run=False)
    m.assert_called_once()
    args, _ = m.call_args
    assert args[0] == "subscription_sync"
    assert "3" in args[1]


def test_sync_subscription_no_notify_when_zero_rows(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls(rows_to_add=0)), \
         patch("media_archivist.notify.notify") as m:
        subs_mod.sync_subscription(db_path, sub, dry_run=False)
    m.assert_not_called()


def test_sync_subscription_no_notify_on_dry_run(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls(rows_to_add=3)), \
         patch("media_archivist.notify.notify") as m:
        subs_mod.sync_subscription(db_path, sub, dry_run=True)
    m.assert_not_called()


def test_sync_subscription_notify_failure_is_swallowed(db_path):
    sub = subs_mod.add_subscription(db_path, "https://www.youtube.com/@chan")
    with patch.object(subs_mod, "_archivist_class",
                       return_value=_fake_archivist_cls(rows_to_add=1)), \
         patch("media_archivist.notify.notify", side_effect=RuntimeError("boom")):
        result = subs_mod.sync_subscription(db_path, sub, dry_run=False)
    # notify() raising must never break the sync result itself.
    assert result.ok is True
    assert result.rows_added == 1
