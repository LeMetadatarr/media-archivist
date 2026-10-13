# SPDX-License-Identifier: Apache-2.0
"""Offset/limit pagination for Index.view/count and the /entries and
/ui/entries/table routes -- so 10k+-entry libraries are browsable instead
of silently truncated at ``limit``.
"""
from __future__ import annotations

import pytest

from media_archivist.index import Index
from media_archivist.storage import EnvelopeJsonStorage

N_SEED = 25


def _seed(path):
    s = EnvelopeJsonStorage(str(path))
    for i in range(N_SEED):
        source = "bandcamp" if i % 5 == 0 else "youtube"
        entry = {
            "source": source,
            "url": f"https://example.test/{i:03d}",
            "title": f"Track {i:03d}",
            "duration": 100 + i,
        }
        if source == "youtube":
            entry["videoId"] = f"vid{i:08d}"
        s[f"https://example.test/{i:03d}"] = entry
    s.store()
    return str(path)


@pytest.fixture
def db(tmp_path):
    return _seed(tmp_path / "db.json")


# --- Index.view / Index.count -----------------------------------------

def test_view_offset_returns_middle_slice(db):
    idx = Index(db)
    full = [e.title for e in idx.view()]
    out = [e.title for e in idx.view(offset=10, limit=5)]
    assert out == full[10:15]


def test_view_offset_beyond_total_is_empty(db):
    idx = Index(db)
    assert list(idx.view(offset=1000, limit=5)) == []


def test_view_offset_default_zero_back_compat(db):
    idx = Index(db)
    assert [e.url for e in idx.view(limit=5)] == [e.url for e in idx.view(offset=0, limit=5)]


def test_count_matches_unfiltered_total(db):
    idx = Index(db)
    assert idx.count() == N_SEED == len(idx)


def test_count_matches_filtered_total(db):
    idx = Index(db)
    n_bandcamp = sum(1 for _ in idx.view(source="bandcamp"))
    assert idx.count(source="bandcamp") == n_bandcamp
    assert 0 < idx.count(source="bandcamp") < N_SEED


def test_count_ignores_limit_offset_not_accepted():
    # count() has no limit/offset params -- it always reports the full
    # filtered total, which is the whole point (paging needs the total
    # independent of the current page window).
    import inspect
    sig = inspect.signature(Index.count)
    assert "limit" not in sig.parameters
    assert "offset" not in sig.parameters


# --- GET /entries --------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from media_archivist.server.app import create_app

    db_path = _seed(tmp_path / "db.json")
    app = create_app(db_path)
    with TestClient(app) as c:
        yield c


def test_entries_route_offset_slice(client):
    full = client.get("/entries", params={"limit": N_SEED}).json()["entries"]
    r = client.get("/entries", params={"limit": 5, "offset": 5})
    body = r.json()
    assert r.status_code == 200
    assert [e["id"] for e in body["entries"]] == [e["id"] for e in full[5:10]]
    assert body["total"] == N_SEED
    assert body["limit"] == 5
    assert body["offset"] == 5


def test_entries_route_back_compat_no_offset(client):
    r = client.get("/entries", params={"limit": 5})
    body = r.json()
    assert r.status_code == 200
    assert body["offset"] == 0
    assert len(body["entries"]) == 5
    assert body["total"] == N_SEED


def test_entries_route_total_reflects_filter(client):
    r = client.get("/entries", params={"source": "bandcamp", "limit": 100})
    body = r.json()
    assert body["total"] == len(body["entries"])
    assert 0 < body["total"] < N_SEED


# --- GET /ui/entries/table -------------------------------------------------

def test_ui_table_first_page_has_next_no_prev(client):
    r = client.get("/ui/entries/table", params={"limit": 10, "offset": 0})
    assert r.status_code == 200
    assert "Showing 1–10 of 25" in r.text
    assert ">Next<" in r.text
    assert ">Prev<" not in r.text


def test_ui_table_last_page_has_prev_no_next(client):
    r = client.get("/ui/entries/table", params={"limit": 10, "offset": 20})
    assert r.status_code == 200
    assert "Showing 21–25 of 25" in r.text
    assert ">Prev<" in r.text
    assert ">Next<" not in r.text


def test_ui_table_filter_narrows_total_and_paging(client):
    r = client.get("/ui/entries/table", params={"source": "bandcamp", "limit": 10, "offset": 0})
    assert r.status_code == 200
    n_bandcamp = sum(1 for i in range(N_SEED) if i % 5 == 0)
    assert f"of {n_bandcamp}" in r.text
    assert ">Next<" not in r.text  # bandcamp count (5) fits on one 10-page
