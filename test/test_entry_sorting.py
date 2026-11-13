# SPDX-License-Identifier: Apache-2.0
"""sortKey / sortDirection on GET /entries and the library table."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.index import Index, parse_sort  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402

ROWS = [
    # (videoId, title, author, duration, published), in indexing order
    ("aaaaaaaaaaa", "banana", "Zed", 30.0, "2020-05-01"),
    ("bbbbbbbbbbb", "Apple", None, 300.0, ""),
    ("ccccccccccc", "cherry", "amy", None, "2019-01-01"),
    ("ddddddddddd", "date", "Bob", 120.0, "2021-12-31"),
]


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db.json")
    db = EnvelopeJsonStorage(path)
    for vid, title, author, duration, published in ROWS:
        url = f"https://www.youtube.com/watch?v={vid}"
        db[url] = {"source": "youtube", "url": url, "videoId": vid, "title": title,
                   "author": author, "duration": duration, "published": published,
                   "metadata_checked": "2026-01-01T00:00:00+00:00"}
    db.store()
    return path


def _titles(body):
    return [e["title"] for e in body["entries"]]


@pytest.mark.parametrize("query,expected", [
    ("", ["banana", "Apple", "cherry", "date"]),
    ("sortKey=added&sortDirection=descending", ["date", "cherry", "Apple", "banana"]),
    ("sortKey=title", ["Apple", "banana", "cherry", "date"]),
    ("sortKey=title&sortDirection=desc", ["date", "cherry", "banana", "Apple"]),
    ("sortKey=artist", ["cherry", "date", "banana", "Apple"]),
    ("sortKey=artist&sortDirection=descending", ["banana", "date", "cherry", "Apple"]),
    ("sortKey=duration", ["banana", "date", "Apple", "cherry"]),
    ("sortKey=duration&sortDirection=descending", ["Apple", "date", "banana", "cherry"]),
    ("sortKey=published&sortDirection=descending", ["date", "banana", "cherry", "Apple"]),
])
def test_api_sorting(db_path, query, expected):
    with TestClient(create_app(db_path)) as c:
        body = c.get(f"/entries?{query}").json()
    assert _titles(body) == expected
    assert body["total"] == 4


def test_sorting_happens_before_paging(db_path):
    with TestClient(create_app(db_path)) as c:
        first = c.get("/entries?sortKey=duration&sortDirection=descending&limit=2").json()
        second = c.get("/entries?sortKey=duration&sortDirection=descending&limit=2&offset=2").json()
    assert _titles(first) == ["Apple", "date"]
    assert _titles(second) == ["banana", "cherry"]
    assert first["total"] == second["total"] == 4


def test_sorting_respects_filters(db_path):
    with TestClient(create_app(db_path)) as c:
        body = c.get("/entries?sortKey=title&sortDirection=descending&where=duration > 60").json()
    assert _titles(body) == ["date", "Apple"]
    assert body["total"] == 2


@pytest.mark.parametrize("query", ["sortKey=raw", "sortKey=__class__", "sortDirection=sideways"])
def test_bad_sort_is_rejected(db_path, query):
    with TestClient(create_app(db_path)) as c:
        r = c.get(f"/entries?{query}")
    assert r.status_code == 400


def test_parse_sort_defaults():
    assert parse_sort(None, None) == ("added", False)
    assert parse_sort("title", "DESCENDING") == ("title", True)


def test_index_sort_with_limit(db_path):
    titles = [e.title for e in Index(db_path).view(sort_key="title", limit=1, offset=1)]
    assert titles == ["banana"]


def test_ui_table_sorts_and_carries_the_sort(db_path):
    with TestClient(create_app(db_path)) as c:
        html = c.get("/ui/entries/table?sortKey=duration&sortDirection=descending&limit=2").text
        assert html.index("Apple") < html.index("date")
        assert 'id="f-sort-key" name="sortKey" value="duration" hx-swap-oob="true"' in html
        assert 'id="f-sort-direction" name="sortDirection" value="descending"' in html
        # the active column's header flips the direction; others start ascending
        assert '"sortKey": "duration", "sortDirection": "ascending"' in html
        assert '"sortKey": "title", "sortDirection": "ascending"' in html
        assert 'aria-sort="descending"' in html
        page = c.get("/ui/entries").text
        assert 'name="sortKey"' in page and 'name="sortDirection"' in page
        bad = c.get("/ui/entries/table?sortKey=nope")
        assert bad.status_code == 200 and "unknown sortKey" in bad.text
