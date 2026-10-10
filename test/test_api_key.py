# SPDX-License-Identifier: Apache-2.0
"""Optional API key: X-Api-Key or ?apikey=, local and Tailscale peers exempt."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server import auth  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"
OUTSIDE = ("203.0.113.7", 50000)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    monkeypatch.delenv(auth.ENV_EXEMPT, raising=False)
    return str(tmp_path / "db.json")


def _client(db_path, peer=OUTSIDE):
    return TestClient(create_app(db_path), client=peer)


def test_off_by_default(db_path):
    with _client(db_path) as c:
        assert c.get("/stats").status_code == 200
        assert c.get("/ui/entries").status_code == 200


def test_outside_peer_needs_the_key(db_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with _client(db_path) as c:
        r = c.get("/stats")
        assert r.status_code == 401 and r.json() == {"detail": "invalid or missing API key"}
        assert c.get("/stats", headers={"X-Api-Key": KEY}).status_code == 200
        assert c.get("/stats", headers={"x-api-key": KEY}).status_code == 200
        assert c.get(f"/stats?apikey={KEY}").status_code == 200
        assert c.get("/stats", headers={"X-Api-Key": KEY + "x"}).status_code == 401
        assert c.get("/stats?apikey=").status_code == 401
        assert c.post("/archive", json={"url": "https://x"}).status_code == 401
        assert c.get("/ui/entries").status_code == 401


def test_healthz_stays_open(db_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with _client(db_path) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/healthz/").status_code == 401


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "10.1.2.3", "172.16.0.1",
                                  "172.31.255.255", "192.168.1.107", "100.124.67.49",
                                  "fd7a:115c:a1e0::1", "::ffff:192.168.1.5"])
def test_local_and_tailscale_peers_are_exempt(db_path, monkeypatch, peer):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with _client(db_path, (peer, 1)) as c:
        assert c.get("/stats").status_code == 200


@pytest.mark.parametrize("peer", ["203.0.113.7", "172.32.0.1", "100.128.0.1",
                                  "2001:db8::1", "::ffff:203.0.113.1", "testclient", ""])
def test_other_peers_are_not_exempt(db_path, monkeypatch, peer):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with _client(db_path, (peer, 1)) as c:
        assert c.get("/stats").status_code == 401


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_forwarded_requests_are_never_exempt(db_path, monkeypatch, header):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with _client(db_path, ("192.168.1.10", 1)) as c:
        assert c.get("/stats", headers={header: "203.0.113.7"}).status_code == 401
        assert c.get("/stats", headers={header: "203.0.113.7", "X-Api-Key": KEY}).status_code == 200


def test_exempt_ranges_are_configurable(db_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    monkeypatch.setenv(auth.ENV_EXEMPT, "203.0.113.0/24")
    with _client(db_path) as c:
        assert c.get("/stats").status_code == 200
    with _client(db_path, ("127.0.0.1", 1)) as c:
        assert c.get("/stats").status_code == 401
    monkeypatch.setenv(auth.ENV_EXEMPT, "")
    with _client(db_path, ("127.0.0.1", 1)) as c:
        assert c.get("/stats").status_code == 401


def test_bad_exempt_range_fails_at_start(db_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    monkeypatch.setenv(auth.ENV_EXEMPT, "not-a-network")
    with pytest.raises(ValueError):
        create_app(db_path)


def test_key_comparison_is_exact():
    a = auth.ApiKeyAuth(KEY)
    assert a.matches(KEY)
    assert not a.matches(KEY[:-1]) and not a.matches(KEY.upper()) and not a.matches(None)
    assert not auth.ApiKeyAuth(None).matches("")
