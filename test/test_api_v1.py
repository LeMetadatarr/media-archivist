# SPDX-License-Identifier: Apache-2.0
"""The /api/v1 router: health, meta, behind the server's API key."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import indexdb  # noqa: E402
from media_archivist.server import auth  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"
OUTSIDE = ("203.0.113.7", 50000)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    monkeypatch.delenv(auth.ENV_EXEMPT, raising=False)
    yield str(tmp_path / "db.json")
    indexdb.close_all()


def test_healthz_and_meta(db_path):
    with TestClient(create_app(db_path), client=OUTSIDE) as c:
        assert c.get("/api/v1/healthz").json() == {"status": "ok"}
        meta = c.get("/api/v1/meta").json()
    assert meta["api"] == "v1"
    assert meta["schema_version"] == indexdb.open_index(db_path).schema_version >= 2
    assert {"healthz", "meta"} <= set(meta["features"])


def test_api_v1_is_behind_the_key_except_healthz(db_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    with TestClient(create_app(db_path), client=OUTSIDE) as c:
        assert c.get("/api/v1/healthz").status_code == 200
        r = c.get("/api/v1/meta")
        assert r.status_code == 401 and r.json() == {"detail": "invalid or missing API key"}
        assert c.get("/api/v1/meta", headers={"X-Api-Key": KEY}).status_code == 200
        assert c.get("/api/v1/healthz/").status_code == 401


def test_app_label_defaults_to_user():
    from media_archivist.server.api_v1.deps import app_label

    class Req:
        def __init__(self, headers):
            self.headers = headers

    assert app_label(Req({})) == "user"
    assert app_label(Req({"x-archivist-app": " shortarr "})) == "shortarr"


def test_metadata_pause_survives_a_server_restart(db_path):
    from media_archivist import ytmeta

    with TestClient(create_app(db_path)):
        ytmeta.LIMITER.bot_check()
    indexdb.close_all()
    ytmeta.LIMITER = ytmeta.MetadataLimiter()
    with TestClient(create_app(db_path)):
        assert ytmeta.LIMITER.blocked
