# SPDX-License-Identifier: Apache-2.0
"""/api/v1/youtube/backoff and /api/v1/settings."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from media_archivist import backoff, indexdb, plugins, ytclient
from media_archivist.server import auth
from media_archivist.server.app import create_app

KEY = "0123456789abcdef0123456789abcdef"
OUTSIDE = ("203.0.113.7", 50000)
BOT = "ERROR: [youtube] x: Sign in to confirm you're not a bot"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    monkeypatch.delenv(auth.ENV_EXEMPT, raising=False)
    yield str(tmp_path / "db.json")
    indexdb.close_all()


def bot():
    raise RuntimeError(BOT)


def test_backoff_lists_every_kind_and_the_family(db_path):
    with TestClient(create_app(db_path)) as c:
        body = c.get("/api/v1/youtube/backoff").json()
    assert body == {"kinds": {k: {"paused": False} for k in backoff.KINDS},
                    "ip_family": "ipv4"}


def test_a_bot_check_shows_and_a_clear_ends_it(db_path):
    with TestClient(create_app(db_path)) as c:
        with pytest.raises(ytclient.BotCheckError):
            ytclient.CLIENT.call("details", bot)
        kinds = c.get("/api/v1/youtube/backoff").json()["kinds"]
        assert kinds["details"]["paused"] is True and kinds["details"]["wait"] == 1800
        assert set(kinds["details"]) == {"paused", "until", "wait", "since"}
        assert kinds["listing"] == {"paused": False}

        r = c.post("/api/v1/youtube/backoff/details/clear")
        assert r.status_code == 200
        assert r.json()["kinds"]["details"] == {"paused": False}
        assert ytclient.CLIENT.call("details", lambda: "ok") == "ok"


def test_delete_also_ends_a_pause(db_path):
    with TestClient(create_app(db_path)) as c:
        ytclient.CLIENT.backoff.mark_blocked("thumb")
        assert c.delete("/api/v1/youtube/backoff/thumb").json()["kinds"]["thumb"] == {
            "paused": False}
        ytclient.CLIENT.check("thumb")


def test_a_pause_set_through_the_index_refuses_the_client(db_path):
    with TestClient(create_app(db_path)):
        backoff.Backoff(indexdb.open_index(db_path)).mark_blocked("feed")
        with pytest.raises(ytclient.PausedError):
            ytclient.CLIENT.call("feed", lambda: "never")


def test_an_unknown_kind_is_404(db_path):
    with TestClient(create_app(db_path)) as c:
        assert c.post("/api/v1/youtube/backoff/comments/clear").status_code == 404
        assert c.delete("/api/v1/youtube/backoff/comments").status_code == 404


def test_settings_read_and_change(db_path):
    with TestClient(create_app(db_path)) as c:
        assert c.get("/api/v1/settings").json() == {
            "youtube_ip_family": "ipv4", "youtube_request_spacing": 10,
            "download_concurrency": 1, "status_cards": "on",
            "enabled_providers": list(plugins.BUILTIN_NAMES), "adult_export_providers": []}
        r = c.patch("/api/v1/settings", json={"youtube_ip_family": "ipv6",
                                              "youtube_request_spacing": 4})
        assert r.status_code == 200
        assert r.json() == {"youtube_ip_family": "ipv6", "youtube_request_spacing": 4,
                            "download_concurrency": 1, "status_cards": "on",
            "enabled_providers": list(plugins.BUILTIN_NAMES), "adult_export_providers": []}
        assert c.get("/api/v1/settings").json()["youtube_ip_family"] == "ipv6"
        assert c.get("/api/v1/youtube/backoff").json()["ip_family"] == "ipv6"
    assert ytclient.CLIENT.ytdlp_args() == ["--force-ipv6"]
    assert ytclient.CLIENT.spacing() == 4


@pytest.mark.parametrize("body", [
    {"youtube_ip_family": "ipv5"},
    {"youtube_request_spacing": -1},
    {"youtube_request_spacing": "10"},
    {"youtube_ip_family": "ipv6", "unknown": 1},
    {"youtube_ip_family": "ipv6", "youtube_request_spacing": True},
])
def test_a_bad_setting_is_400_and_nothing_changes(db_path, body):
    with TestClient(create_app(db_path)) as c:
        r = c.patch("/api/v1/settings", json=body)
        assert r.status_code == 400 and r.json()["detail"]
        assert c.get("/api/v1/settings").json()["youtube_ip_family"] == "ipv4"


def test_meta_names_the_features(db_path):
    with TestClient(create_app(db_path)) as c:
        features = c.get("/api/v1/meta").json()["features"]
    assert {"youtube_backoff", "settings"} <= set(features)


def test_the_routes_are_behind_the_key(db_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with TestClient(create_app(db_path), client=OUTSIDE) as c:
        assert c.get("/api/v1/settings").status_code == 401
        assert c.patch("/api/v1/settings", json={"youtube_ip_family": "auto"}).status_code == 401
        assert c.get("/api/v1/youtube/backoff").status_code == 401
        assert c.post("/api/v1/youtube/backoff/details/clear").status_code == 401
        assert c.get("/api/v1/settings", headers={"X-Api-Key": KEY}).status_code == 200
