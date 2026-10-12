# SPDX-License-Identifier: Apache-2.0
"""Signed stream links: signature, expiry, the API-key bypass for one route."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from media_archivist import backoff, indexdb, links, streams
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.server import auth
from media_archivist.server.app import create_app
from media_archivist.storage import EnvelopeJsonStorage

KEY = "0123456789abcdef0123456789abcdef"
OUTSIDE = ("203.0.113.7", 50000)
WATCH = "https://www.youtube.com/watch?v=vid00000001"
EID = stable_id(Source.YOUTUBE, WATCH)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    for var in (auth.ENV_KEY, auth.ENV_EXEMPT, links.ENV_SECRET, links.ENV_PUBLIC_URL):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db[WATCH] = {"source": "youtube", "url": WATCH, "videoId": "vid00000001",
                 "title": "A Short Film", "author": "Some Channel"}
    db.store()
    yield str(path)
    indexdb.close_all()


@pytest.fixture
def resolved(monkeypatch):
    calls = []

    def fake(url, *, source=None, **kw):
        calls.append(url)
        return streams.ResolvedStream(url="https://cdn.example.org/media.mp4")

    monkeypatch.setattr(streams, "resolve_stream", fake)
    return calls


def _client(db_path, monkeypatch, *, key=True):
    if key:
        monkeypatch.setenv(auth.ENV_KEY, KEY)
    return TestClient(create_app(db_path), client=OUTSIDE, follow_redirects=False)


def test_signature_round_trip_and_tampering(db_path):
    index = indexdb.open_index(db_path)
    secret = links.secret(index)
    sig = links.signature(secret, EID, None)
    assert links.verify(secret, EID, sig, None)
    assert not links.verify(secret, EID, sig[:-1] + ("0" if sig[-1] != "0" else "1"), None)
    assert not links.verify(secret, "f" * 40, sig, None)
    assert not links.verify(secret, EID, "", None)
    assert not links.verify(b"another secret", EID, sig, None)
    exp_sig = links.signature(secret, EID, 2000)
    assert links.verify(secret, EID, exp_sig, 2000, now=1999)
    assert not links.verify(secret, EID, exp_sig, 2000, now=2001)
    assert not links.verify(secret, EID, exp_sig, 3000, now=1999)
    assert not links.verify(secret, EID, exp_sig, None, now=1999)


def test_secret_is_generated_once_and_persisted(db_path):
    index = indexdb.open_index(db_path)
    first = links.secret(index)
    assert len(first) >= 32
    indexdb.close_all()
    assert links.secret(indexdb.open_index(db_path)) == first


def test_secret_from_environment_wins(db_path, monkeypatch):
    index = indexdb.open_index(db_path)
    generated = links.secret(index)
    monkeypatch.setenv(links.ENV_SECRET, "operator secret")
    assert links.secret(index) == b"operator secret" != generated


def test_secret_is_not_a_visible_setting(db_path):
    from media_archivist.settings import Settings

    index = indexdb.open_index(db_path)
    links.secret(index)
    assert "link_secret" not in Settings(index).all()


def test_signed_link_bypasses_the_key_for_the_stream_route_only(db_path, monkeypatch, resolved):
    with _client(db_path, monkeypatch) as c:
        r = c.post(f"/api/v1/entries/{EID}/link", headers={"X-Api-Key": KEY})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["expires_at"] is None
        url = urlparse(body["url"])
        assert url.path == f"/api/v1/stream/{EID}"
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert "exp" not in q

        assert c.get(f"/api/v1/stream/{EID}").status_code == 401
        r = c.get(f"/api/v1/stream/{EID}", params=q)
        assert r.status_code == 302
        assert r.headers["location"] == "https://cdn.example.org/media.mp4"
        assert c.head(f"/api/v1/stream/{EID}", params=q).status_code == 302

        assert c.get("/api/v1/meta", params=q).status_code == 401
        assert c.post(f"/api/v1/entries/{EID}/link", params=q).status_code == 401
        assert c.get(f"/api/v1/stream/{EID}/", params=q).status_code == 401
        assert c.get(f"/strm/{EID}", params=q).status_code == 401
        assert c.post(f"/api/v1/stream/{EID}", params=q).status_code == 401


def test_a_signature_for_one_entry_does_not_open_another(db_path, monkeypatch, resolved):
    index = indexdb.open_index(db_path)
    sig = links.signature(links.secret(index), EID, None)
    with _client(db_path, monkeypatch) as c:
        other = "youtube:vid00000001"
        assert c.get(f"/api/v1/stream/{other}", params={"sig": sig}).status_code == 401
        assert c.get(f"/api/v1/stream/{EID}", params={"sig": sig}).status_code == 302


def test_tampered_and_expired_links_are_refused(db_path, monkeypatch, resolved):
    with _client(db_path, monkeypatch) as c:
        r = c.post(f"/api/v1/entries/{EID}/link", json={"expires_in": 60},
                   headers={"X-Api-Key": KEY})
        assert r.status_code == 200
        assert r.json()["expires_at"] is not None
        q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["url"]).query).items()}
        assert c.get(f"/api/v1/stream/{EID}", params=q).status_code == 302
        later = dict(q, exp=str(int(q["exp"]) + 3600))
        assert c.get(f"/api/v1/stream/{EID}", params=later).status_code == 401
        assert c.get(f"/api/v1/stream/{EID}", params=dict(q, sig="0" * 64)).status_code == 401
        assert c.get(f"/api/v1/stream/{EID}", params=dict(q, exp="soon")).status_code == 401

        monkeypatch.setattr(links, "_now", lambda: int(q["exp"]) + 1)
        assert c.get(f"/api/v1/stream/{EID}", params=q).status_code == 401


def test_changing_the_secret_revokes_links(db_path, monkeypatch, resolved):
    with _client(db_path, monkeypatch) as c:
        url = c.post(f"/api/v1/entries/{EID}/link", headers={"X-Api-Key": KEY}).json()["url"]
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert c.get(f"/api/v1/stream/{EID}", params=q).status_code == 302
        monkeypatch.setenv(links.ENV_SECRET, "rotated")
        assert c.get(f"/api/v1/stream/{EID}", params=q).status_code == 401


def test_link_uses_the_public_url_and_rejects_bad_expiry(db_path, monkeypatch):
    monkeypatch.setenv(links.ENV_PUBLIC_URL, "https://archivist.example.org/base/")
    with _client(db_path, monkeypatch, key=False) as c:
        r = c.post("/api/v1/entries/youtube:vid00000001/link")
        assert r.status_code == 200
        assert r.json()["url"].startswith(f"https://archivist.example.org/base/api/v1/stream/{EID}?sig=")
        assert c.post(f"/api/v1/entries/{EID}/link", json={"expires_in": 0}).status_code == 400
        assert c.post(f"/api/v1/entries/{EID}/link", json={"expires_in": "x"}).status_code in (400, 422)
        assert c.post("/api/v1/entries/youtube:missing/link").status_code == 404


def test_stream_accepts_a_key_and_unknown_is_404(db_path, monkeypatch, resolved):
    with _client(db_path, monkeypatch, key=False) as c:
        r = c.get("/api/v1/stream/youtube:vid00000001")
        assert r.status_code == 302 and r.headers["location"].startswith("https://cdn.")
        assert c.get("/api/v1/stream/youtube:nothere").status_code == 404
    assert resolved == [WATCH]


def test_stream_never_waits_for_a_paused_details_kind(db_path, monkeypatch, resolved):
    with _client(db_path, monkeypatch, key=False) as c:
        backoff.Backoff(indexdb.open_index(db_path)).mark_blocked("details")
        r = c.get(f"/api/v1/stream/{EID}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp2t"
    assert resolved == []


def test_a_bot_check_while_resolving_pauses_details_and_redirects(db_path, monkeypatch):
    def challenged(url, **kw):
        raise streams.StreamResolveError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(streams, "resolve_stream", challenged)
    with _client(db_path, monkeypatch, key=False) as c:
        r = c.get(f"/api/v1/stream/{EID}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp2t"
    pauses = backoff.Backoff(indexdb.open_index(db_path)).blocked()
    assert pauses["details"]["paused"] is True
    assert pauses["listing"]["paused"] is False


def test_other_resolution_failures_redirect_without_pausing(db_path, monkeypatch):
    def broken(url, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(streams, "resolve_stream", broken)
    with _client(db_path, monkeypatch, key=False) as c:
        r = c.get(f"/api/v1/stream/{EID}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp2t"
    assert backoff.Backoff(indexdb.open_index(db_path)).blocked()["details"]["paused"] is False


def test_proxy_mode_passes_range_and_falls_back_to_redirect(db_path, monkeypatch, resolved):
    import requests

    seen = {}

    class Upstream:
        status_code = 206
        headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/10",
                   "Content-Length": "4"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"abcd"

        def close(self):
            pass

    def fake_get(url, headers=None, **kw):
        seen["range"] = (headers or {}).get("Range")
        return Upstream()

    monkeypatch.setattr(requests, "get", fake_get)
    with _client(db_path, monkeypatch, key=False) as c:
        from media_archivist import ytclient

        ytclient.CLIENT.gate.spacing = lambda: 0.0
        r = c.get(f"/api/v1/stream/{EID}", params={"mode": "proxy"},
                  headers={"Range": "bytes=0-3"})
        assert r.status_code == 206 and r.content == b"abcd"
        assert seen["range"] == "bytes=0-3"

        def failing_get(*a, **kw):
            raise requests.ConnectionError("unreachable")

        monkeypatch.setattr(requests, "get", failing_get)
        r = c.get(f"/api/v1/stream/{EID}", params={"mode": "proxy"})
    assert r.status_code == 302 and r.headers["location"] == "https://cdn.example.org/media.mp4"
