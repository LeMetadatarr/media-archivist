# SPDX-License-Identifier: Apache-2.0
"""``media-archivist subscribe/unsubscribe/subscriptions/sync-subscriptions`` CLI.

Backends are mocked (patching media_archivist.subscriptions._archivist_class)
— no network.
"""
from __future__ import annotations

from unittest.mock import patch

from media_archivist import subscriptions as subs_mod
from media_archivist.cli import main
from media_archivist.storage import EnvelopeJsonStorage


def _seed(path):
    db = EnvelopeJsonStorage(str(path))
    db.store()
    return str(path)


def _fake_archivist_cls(rows_to_add=1):
    class _Fake:
        def __init__(self, db_path):
            self._db = EnvelopeJsonStorage(db_path)
            self._n = 0

        @property
        def video_urls(self):
            return list(self._db.keys()) + [f"fake:{i}" for i in range(self._n)]

        def archive(self, url):
            self._n += rows_to_add

    return _Fake


def test_subscribe_adds_entry(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    assert rc == 0
    subs = subs_mod.list_subscriptions(db_path)
    assert len(subs) == 1
    assert subs[0].backend == "youtube"
    err = capsys.readouterr().err
    assert "subscribed" in err


def test_subscribe_bad_url_errors(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["subscribe", "https://example.com/whatever", "--db-file", db_path])
    assert rc == 1
    assert "error" in capsys.readouterr().err


def test_subscriptions_lists_entries(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    rc = main(["subscriptions", "--db-file", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "https://www.youtube.com/@chan" in out


def test_subscriptions_empty_reports_none(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["subscriptions", "--db-file", db_path])
    assert rc == 0
    assert "no subscriptions" in capsys.readouterr().err


def test_unsubscribe_removes_entry(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    rc = main(["unsubscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    assert rc == 0
    assert subs_mod.list_subscriptions(db_path) == []


def test_unsubscribe_missing_errors(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["unsubscribe", "https://www.youtube.com/@nope", "--db-file", db_path])
    assert rc == 1


def test_sync_subscriptions_dry_run_archives_nothing(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(5)) as m:
        rc = main(["sync-subscriptions", "--db-file", db_path, "--dry-run"])
    assert rc == 0
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "dry-run" in err


def test_sync_subscriptions_runs_and_reports_rows(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(4)):
        rc = main(["sync-subscriptions", "--db-file", db_path])
    assert rc == 0
    err = capsys.readouterr().err
    assert "4 new rows" in err
    subs = subs_mod.list_subscriptions(db_path)
    assert subs[0].last_rows_added == 4


def test_subscribe_download_flag_sets_auto_download(tmp_path):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path,
               "--download"])
    assert rc == 0
    subs = subs_mod.list_subscriptions(db_path)
    assert subs[0].auto_download is True


def test_subscribe_without_download_flag_defaults_false(tmp_path):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    subs = subs_mod.list_subscriptions(db_path)
    assert subs[0].auto_download is False


def test_sync_subscriptions_download_flag_wired_to_sync_all(tmp_path):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    with patch.object(subs_mod, "sync_all", return_value=[]) as m:
        rc = main(["sync-subscriptions", "--db-file", db_path, "--download"])
    assert rc == 0
    _, kwargs = m.call_args
    assert kwargs["download"] is True


def test_sync_subscriptions_interval_calls_watch_not_sync_all(tmp_path):
    db_path = _seed(tmp_path / "db.json")
    main(["subscribe", "https://www.youtube.com/@chan", "--db-file", db_path])
    with patch.object(subs_mod, "watch") as m_watch, \
         patch.object(subs_mod, "sync_all") as m_sync_all:
        rc = main(["sync-subscriptions", "--db-file", db_path, "--interval", "5"])
    assert rc == 0
    m_watch.assert_called_once()
    m_sync_all.assert_not_called()
    _, kwargs = m_watch.call_args
    assert kwargs["interval"] == 5


def test_sync_subscriptions_interval_and_dry_run_conflict(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    rc = main(["sync-subscriptions", "--db-file", db_path, "--interval", "5",
               "--dry-run"])
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().err
