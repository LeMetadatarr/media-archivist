# SPDX-License-Identifier: Apache-2.0
"""``serve`` syncs each subscription on its own interval and remembers the last run."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

import media_archivist.youtube as youtube_mod  # noqa: E402
from media_archivist import subscriptions as subs_mod  # noqa: E402
from media_archivist.models.api import SyncRequest  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402

CHANNEL = "https://www.youtube.com/@somechannel"
OTHER = "https://www.youtube.com/@otherchannel"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS", raising=False)
    return str(tmp_path / "db.json")


@pytest.fixture
def archived(monkeypatch):
    calls = []

    def _archive(self, url):
        calls.append(url)
        if "broken" in url:
            raise RuntimeError("listing failed")
        self.db[f"https://www.youtube.com/watch?v={len(calls):0>11}"] = {
            "source": "youtube", "url": f"https://www.youtube.com/watch?v={len(calls):0>11}",
            "videoId": f"{len(calls):0>11}", "title": "new"}
        self.db.store()
        self.entries_seen = 1

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", _archive)
    return calls


def _periodic(c):
    p = c.app.state.periodic
    p.enrich_enabled = False
    p.sync_enabled = True
    p.index_enabled = False
    return p


def _wait_idle(c, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = c.get("/tasks").json()
        if body["queued"] == 0 and body["running"] == 0:
            return body
        time.sleep(0.02)
    pytest.fail("tasks never finished")


def _sub(db_path, url):
    return next(s for s in subs_mod.list_subscriptions(db_path) if s.url == url)


def test_due_subscriptions_are_synced_as_tasks(db_path, archived):
    subs_mod.add_subscription(db_path, CHANNEL)
    with TestClient(create_app(db_path)) as c:
        queued = _periodic(c).run_once()
        assert [(t.kind, t.request.url) for t in queued] == [("sync", CHANNEL)]
        body = _wait_idle(c)
        task = c.get(f"/tasks/{queued[0].id}").json()
    assert archived == [CHANNEL]
    assert task["status"] == "ok" and task["rows_added"] == 1
    assert body["tasks"][0]["request"] == {"kind": "sync", "url": CHANNEL}
    sub = _sub(db_path, CHANNEL)
    assert sub.last_synced_at and sub.last_rows_added == 1


def test_not_due_again_until_interval_passes_even_after_restart(db_path, archived):
    subs_mod.add_subscription(db_path, CHANNEL)
    with TestClient(create_app(db_path)) as c:
        _periodic(c).run_once()
        _wait_idle(c)
    with TestClient(create_app(db_path)) as c:
        assert _periodic(c).run_once() == []
    assert archived == [CHANNEL]

    later = datetime.now(timezone.utc) + timedelta(hours=6, minutes=1)
    assert [s.url for s in subs_mod.due_subscriptions(db_path, later)] == [CHANNEL]
    sooner = datetime.now(timezone.utc) + timedelta(hours=5, minutes=59)
    assert subs_mod.due_subscriptions(db_path, sooner) == []


def test_per_subscription_interval(db_path, archived):
    subs_mod.add_subscription(db_path, CHANNEL, interval_hours=1)
    subs_mod.add_subscription(db_path, OTHER)
    with TestClient(create_app(db_path)) as c:
        _periodic(c).run_once()
        _wait_idle(c)
    in_90_min = datetime.now(timezone.utc) + timedelta(minutes=90)
    assert [s.url for s in subs_mod.due_subscriptions(db_path, in_90_min)] == [CHANNEL]


def test_default_interval_from_environment(db_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS", "0.5")
    sub = subs_mod.add_subscription(db_path, CHANNEL)
    assert subs_mod.interval_of(sub) == timedelta(minutes=30)
    monkeypatch.setenv("MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS", "nonsense")
    assert subs_mod.interval_of(sub) == timedelta(hours=6)


def test_a_failed_sync_waits_for_its_interval(db_path, archived):
    subs_mod.add_subscription(db_path, "https://www.youtube.com/@broken")
    with TestClient(create_app(db_path)) as c:
        p = _periodic(c)
        task = p.run_once()[0]
        _wait_idle(c)
        assert c.get(f"/tasks/{task.id}").json()["status"] == "error"
        assert p.run_once() == []
    sub = _sub(db_path, "https://www.youtube.com/@broken")
    assert sub.last_error == "listing failed" and sub.last_synced_at


def test_no_second_task_while_one_is_pending(db_path, monkeypatch):
    import threading

    release = threading.Event()
    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive",
                        lambda self, url: release.wait(5))
    subs_mod.add_subscription(db_path, CHANNEL)
    with TestClient(create_app(db_path)) as c:
        p = _periodic(c)
        assert len(p.run_once()) == 1
        assert p.run_once() == []
        release.set()
        _wait_idle(c)


def test_scheduled_sync_can_be_turned_off(db_path, archived, monkeypatch):
    monkeypatch.setenv("MEDIA_ARCHIVIST_SCHEDULED_SYNC", "0")
    subs_mod.add_subscription(db_path, CHANNEL)
    with TestClient(create_app(db_path)) as c:
        p = c.app.state.periodic
        p.enrich_enabled = False
        assert not p.sync_enabled
        assert p.run_once() == []


def test_sync_keeps_subscriptions_changed_meanwhile(db_path, monkeypatch):
    subs_mod.add_subscription(db_path, CHANNEL)

    def _archive(self, url):
        subs_mod.add_subscription(db_path, OTHER, label="added during sync")
        self.entries_seen = 1

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", _archive)
    subs_mod.sync_one(db_path, CHANNEL)
    urls = {s.url: s for s in subs_mod.list_subscriptions(db_path)}
    assert set(urls) == {CHANNEL, OTHER}
    assert urls[CHANNEL].last_synced_at and urls[OTHER].label == "added during sync"


def test_api_sets_and_reports_the_interval(db_path):
    with TestClient(create_app(db_path)) as c:
        r = c.post("/subscriptions", json={"url": CHANNEL, "interval_hours": 12})
        assert r.status_code == 200
        assert r.json()["interval_hours"] == 12 and r.json()["next_sync_at"] is None
        assert c.post("/subscriptions", json={"url": OTHER, "interval_hours": 0}).status_code == 422
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sidecar = subs_mod.load_subscriptions(db_path)
    sidecar.subscriptions[0].last_synced_at = stamp.isoformat()
    subs_mod.save_subscriptions(db_path, sidecar)
    with TestClient(create_app(db_path)) as c:
        info = c.get("/subscriptions").json()["subscriptions"][0]
    assert info["next_sync_at"] == (stamp + timedelta(hours=12)).isoformat()


def test_cli_subscribe_takes_an_interval(db_path):
    from media_archivist.cli import main

    assert main(["subscribe", CHANNEL, "--db-file", db_path, "--interval-hours", "2"]) == 0
    assert _sub(db_path, CHANNEL).interval_hours == 2


def test_a_timed_out_sync_is_not_started_again_while_its_thread_lives(db_path, monkeypatch):
    import threading

    from media_archivist.server import routes

    monkeypatch.setattr(routes, "SYNC_TIMEOUT_S", 0.2)
    release = threading.Event()
    state = {"now": 0, "peak": 0, "calls": 0}
    guard = threading.Lock()

    def slow(self, url):
        with guard:
            state["now"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["now"])
        release.wait(10)
        with guard:
            state["now"] -= 1

    monkeypatch.setattr(youtube_mod.YoutubeArchivist, "archive", slow)
    subs_mod.add_subscription(db_path, CHANNEL)
    with TestClient(create_app(db_path)) as c:
        p = _periodic(c)
        first, = p.run_once()
        deadline = time.monotonic() + 5
        while c.get(f"/tasks/{first.id}").json()["status"] != "error":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert p.run_once() == []
        p.scheduler.submit(SyncRequest(url=CHANNEL))
        time.sleep(0.5)
        assert state["calls"] == 1 and state["peak"] == 1
        release.set()
        _wait_idle(c)
        deadline = time.monotonic() + 5
        while subs_mod.syncing_urls():
            assert time.monotonic() < deadline
            time.sleep(0.02)
    assert state["peak"] == 1


def test_huge_interval_is_rejected_by_add_and_cli(db_path, capsys):
    from media_archivist.cli import main

    with pytest.raises(ValueError):
        subs_mod.add_subscription(db_path, CHANNEL, interval_hours=1e9)
    assert main(["subscribe", CHANNEL, "--db-file", db_path, "--interval-hours", "1e12"]) == 1
    assert "interval" in capsys.readouterr().err
    assert subs_mod.list_subscriptions(db_path) == []


def test_huge_default_interval_from_environment_falls_back(db_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS", "1e12")
    assert subs_mod.default_interval_hours() == subs_mod.DEFAULT_INTERVAL_HOURS
    subs_mod.add_subscription(db_path, CHANNEL)
    sidecar = subs_mod.load_subscriptions(db_path)
    sidecar.subscriptions[0].last_synced_at = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    subs_mod.save_subscriptions(db_path, sidecar)
    assert subs_mod.due_subscriptions(db_path)
    with TestClient(create_app(db_path)) as c:
        r = c.get("/subscriptions")
        assert r.status_code == 200 and r.json()["subscriptions"][0]["next_sync_at"]


def test_a_stored_huge_interval_does_not_break_listing(db_path):
    subs_mod.add_subscription(db_path, CHANNEL)
    sidecar = subs_mod.load_subscriptions(db_path)
    sidecar.subscriptions[0].interval_hours = 1e12
    sidecar.subscriptions[0].last_synced_at = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    subs_mod.save_subscriptions(db_path, sidecar)
    subs_mod.due_subscriptions(db_path)
    with TestClient(create_app(db_path)) as c:
        assert c.get("/subscriptions").status_code == 200


def test_sync_all_skips_urls_whose_sync_thread_is_alive(db_path, archived):
    subs_mod.add_subscription(db_path, CHANNEL)
    subs_mod.add_subscription(db_path, OTHER)
    with subs_mod._syncing_lock:
        subs_mod._syncing.add(CHANNEL)
    try:
        results = subs_mod.sync_all(db_path)
    finally:
        with subs_mod._syncing_lock:
            subs_mod._syncing.discard(CHANNEL)
    by_url = {r.url: r for r in results}
    assert by_url[CHANNEL].skipped and by_url[CHANNEL].ok
    assert not by_url[OTHER].skipped and by_url[OTHER].rows_added == 1
    assert archived == [OTHER]
    assert not subs_mod.syncing_urls()


def test_sync_all_stores_each_run_before_releasing_its_claim(db_path, archived, monkeypatch):
    subs_mod.add_subscription(db_path, CHANNEL)
    subs_mod.add_subscription(db_path, OTHER)
    held = {}
    real = subs_mod._store_run

    def spy(path, sub):
        held[sub.url] = sub.url in subs_mod.syncing_urls()
        real(path, sub)

    monkeypatch.setattr(subs_mod, "_store_run", spy)
    subs_mod.sync_all(db_path)
    assert held == {CHANNEL: True, OTHER: True}
