# SPDX-License-Identifier: Apache-2.0
"""The settings page reads and writes the same settings as ``/api/v1/settings``."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import indexdb  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.settings import Settings  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402

FORM = {"youtube_ip_family": "ipv6", "youtube_request_spacing": "4.5",
        "download_concurrency": "3", "status_cards": "off",
        "enabled_providers": ["youtube", "ia"], "adult_export_providers": []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("MEDIA_ARCHIVIST_API_KEY", "MEDIA_ARCHIVIST_AUTH_EXEMPT"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "db.json"
    EnvelopeJsonStorage(str(path)).store()
    with TestClient(create_app(str(path))) as c:
        yield c
    indexdb.close_all()


def test_page_shows_the_current_values_and_every_provider(client):
    r = client.get("/ui/settings")
    assert r.status_code == 200
    assert 'value="ipv4" selected' in r.text
    for name in ("youtube", "ia", "music", "bandcamp", "soundcloud"):
        assert f'name="enabled_providers" value="{name}" checked' in r.text
    assert 'href="/ui/settings"' in r.text


def test_saving_changes_the_stored_settings(client):
    r = client.post("/ui/settings", data=FORM)
    assert r.status_code == 200 and "Saved" in r.text
    got = Settings(client.app.state.index).all()
    assert got["youtube_ip_family"] == "ipv6"
    assert got["youtube_request_spacing"] == 4.5
    assert got["download_concurrency"] == 3
    assert got["status_cards"] == "off"
    assert got["enabled_providers"] == ["youtube", "ia"]
    assert client.get("/api/v1/settings").json()["download_concurrency"] == 3


@pytest.mark.parametrize("field,value", [
    ("youtube_ip_family", "ipv5"), ("download_concurrency", "99"),
    ("download_concurrency", "two"), ("youtube_request_spacing", "-1"),
    ("youtube_request_spacing", "nan"), ("status_cards", "maybe"),
    ("enabled_providers", ["nope"]), ("download_concurrency", "3.7"),
    ("youtube_request_spacing", "1e308"), ("youtube_request_spacing", "3601"),
])
def test_bad_values_are_refused_and_nothing_is_stored(client, field, value):
    before = Settings(client.app.state.index).all()
    r = client.post("/ui/settings", data={**FORM, field: value})
    assert r.status_code == 400
    assert 'class="error"' in r.text
    assert Settings(client.app.state.index).all() == before


@pytest.mark.parametrize("field,value,reason", [
    ("download_concurrency", "99", "whole number from 1 to 8"),
    ("youtube_ip_family", "ipv5", "youtube_ip_family"),
    ("youtube_request_spacing", "1e308", "from 0 to 3600"),
    ("enabled_providers", ["nope"], "nope"),
])
def test_the_400_names_the_reason(client, field, value, reason):
    r = client.post("/ui/settings", data={**FORM, field: value})
    assert r.status_code == 400
    error = re.search(r'<div class="error">(.*?)</div>', r.text, re.S)
    assert error and reason in error.group(1)


@pytest.mark.parametrize("field", ["youtube_request_spacing", "download_concurrency"])
def test_a_file_part_in_a_number_field_is_a_400(client, field):
    before = Settings(client.app.state.index).all()
    data = {k: v for k, v in FORM.items() if k != field and isinstance(v, str)}
    r = client.post("/ui/settings", data=data,
                    files={field: ("x.txt", b"12", "text/plain")})
    assert r.status_code == 400
    assert Settings(client.app.state.index).all() == before


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
])
def test_a_cross_site_post_is_refused_and_nothing_is_stored(client, headers):
    before = Settings(client.app.state.index).all()
    r = client.post("/ui/settings", data={**FORM, "adult_export_providers": ["ia"]},
                    headers=headers)
    assert r.status_code == 403
    assert Settings(client.app.state.index).all() == before


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://testserver"}, {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
])
def test_a_same_origin_or_headerless_post_is_accepted(client, headers):
    assert client.post("/ui/settings", data=FORM, headers=headers).status_code == 200


def test_the_page_renders_the_stored_values(client):
    Settings(client.app.state.index).update(
        {"youtube_request_spacing": 7.5, "status_cards": "off", "download_concurrency": 5,
         "adult_export_providers": ["ia"]})
    text = client.get("/ui/settings").text
    assert 'name="youtube_request_spacing" min="0" step="any" value="7.5"' in text
    assert 'value="off" selected' in text and 'value="on" selected' not in text
    assert 'name="adult_export_providers" value="ia" checked' in text
    assert 'name="adult_export_providers" value="youtube" checked' not in text
    assert 'name="download_concurrency" min="1" max="8" step="1" value="5"' in text
    assert '<form class="card" method="post" action="/ui/settings">' in text


def test_saving_the_unchanged_page_keeps_the_adult_allow_list(client):
    Settings(client.app.state.index).update({"adult_export_providers": ["ia"]})
    page = client.get("/ui/settings").text
    checked = re.findall(r'name="adult_export_providers" value="(\w+)" checked', page)
    client.post("/ui/settings", data={**FORM, "adult_export_providers": checked})
    assert Settings(client.app.state.index).get("adult_export_providers") == ["ia"]


def test_missing_field_is_a_400_not_a_crash(client):
    r = client.post("/ui/settings", data={"youtube_ip_family": "ipv4"})
    assert r.status_code == 400


def test_adult_export_switch_is_stored_per_provider(client):
    client.post("/ui/settings", data={**FORM, "adult_export_providers": ["ia"]})
    assert Settings(client.app.state.index).get("adult_export_providers") == ["ia"]
