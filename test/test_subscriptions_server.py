# SPDX-License-Identifier: Apache-2.0
"""/subscriptions JSON API and /ui/subscriptions WebUI.

Archivist backends are mocked (patching media_archivist.subscriptions.
_archivist_class) — no network.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import subscriptions as subs_mod  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db.store()
    return str(path)


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    with TestClient(app) as c:
        yield c


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


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

def test_post_subscriptions_adds(client, db_path):
    r = client.post("/subscriptions", json={"url": "https://www.youtube.com/@chan"})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "youtube"
    assert subs_mod.list_subscriptions(db_path)[0].url == "https://www.youtube.com/@chan"


def test_post_subscriptions_bad_url_400(client):
    r = client.post("/subscriptions", json={"url": "https://example.com/x"})
    assert r.status_code == 400


def test_get_subscriptions_lists(client):
    client.post("/subscriptions", json={"url": "https://www.youtube.com/@chan"})
    r = client.get("/subscriptions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["subscriptions"][0]["url"] == "https://www.youtube.com/@chan"


def test_delete_subscriptions_removes(client):
    client.post("/subscriptions", json={"url": "https://www.youtube.com/@chan"})
    r = client.request("DELETE", "/subscriptions", json={"url": "https://www.youtube.com/@chan"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_delete_subscriptions_missing_404(client):
    r = client.request("DELETE", "/subscriptions", json={"url": "https://nope.example/x"})
    assert r.status_code == 404


def test_post_subscriptions_sync_returns_results(client):
    client.post("/subscriptions", json={"url": "https://www.youtube.com/@chan"})
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(3)):
        r = client.post("/subscriptions/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["results"][0]["ok"] is True
    assert body["results"][0]["rows_added"] == 3


def test_post_subscriptions_sync_dry_run(client):
    client.post("/subscriptions", json={"url": "https://www.youtube.com/@chan"})
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(3)) as m:
        r = client.post("/subscriptions/sync", params={"dry_run": "true"})
    assert r.status_code == 200
    assert r.json()["results"][0]["dry_run"] is True
    m.assert_not_called()


# ---------------------------------------------------------------------------
# WebUI
# ---------------------------------------------------------------------------

def test_ui_subscriptions_page_renders(client):
    r = client.get("/ui/subscriptions")
    assert r.status_code == 200
    assert "Subscriptions" in r.text


def test_ui_subscriptions_table_empty(client):
    r = client.get("/ui/subscriptions/table")
    assert r.status_code == 200
    assert "No subscriptions" in r.text


def test_ui_subscriptions_post_adds_and_renders_row(client):
    r = client.post("/ui/subscriptions", data={"url": "https://www.youtube.com/@chan"})
    assert r.status_code == 200
    assert "https://www.youtube.com/@chan" in r.text


def test_ui_subscriptions_delete_removes_row(client):
    client.post("/ui/subscriptions", data={"url": "https://www.youtube.com/@chan"})
    r = client.request("DELETE", "/ui/subscriptions", data={"url": "https://www.youtube.com/@chan"})
    assert r.status_code == 200
    assert "No subscriptions" in r.text


def test_ui_subscriptions_sync_renders_summary(client):
    client.post("/ui/subscriptions", data={"url": "https://www.youtube.com/@chan"})
    with patch.object(subs_mod, "_archivist_class", return_value=_fake_archivist_cls(2)):
        r = client.post("/ui/subscriptions/sync")
    assert r.status_code == 200
    assert "Synced" in r.text
