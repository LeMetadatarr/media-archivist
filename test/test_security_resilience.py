"""Adversarial tests for the verified security/resilience findings:

- H1: where-DSL string repetition => unauth single-request OOM.
- node-count budget on --where expressions.
- Index.get(): id-indexed lookup instead of full-table scan.
- /healthz: honest 503 on a broken db instead of a static 200.
- db_path: absolute filesystem path must not leak to clients.
- bandcamp explicit: raw "explicit" field must map onto MediaEntry.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.index import Index, WhereError, evaluate_where  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402
from media_archivist.views import to_media_entry  # noqa: E402


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "db.json"
    s = EnvelopeJsonStorage(str(p))
    s["a"] = {"source": "youtube", "url": "a", "videoId": "a", "title": "Cats",
              "duration": 60, "tags": ["pets"]}
    s["b"] = {"source": "youtube", "url": "b", "videoId": "b", "title": "Dogs",
              "duration": 600, "tags": ["pets", "long"]}
    s["c"] = {"source": "bandcamp", "url": "c", "title": "Explicit Track",
              "artist": "Foo", "duration": 240,
              "stream": "https://x/c.mp3", "explicit": True}
    s.store()
    return str(p)


def _entry(idx: Index, url: str):
    for raw in idx.raw_entries():
        if raw["url"] == url:
            return to_media_entry(raw)
    raise AssertionError(f"no such row: {url}")


# --------------------------------------------------------------------------
# H1: where-DSL string repetition => OOM
# --------------------------------------------------------------------------

def test_where_rejects_string_repetition(db):
    idx = Index(db)
    entry = _entry(idx, "a")
    # A moderate multiplier: big enough that unfixed code visibly allocates
    # a huge string (10 * 10**7 chars == ~100MB), small enough not to OOM
    # the test runner even if the fix is broken, but plainly wrong for a
    # filter predicate.
    with pytest.raises(WhereError):
        evaluate_where('"a" * 10**7 > ""', entry)


def test_where_rejects_int_times_string(db):
    idx = Index(db)
    entry = _entry(idx, "a")
    with pytest.raises(WhereError):
        evaluate_where('10**7 * "a" > ""', entry)


def test_where_still_allows_numeric_multiplication(db):
    idx = Index(db)
    entry = _entry(idx, "b")  # duration=600
    assert evaluate_where("duration * 2 > 100", entry) is True
    assert evaluate_where("duration * 2 < 100", entry) is False


def test_entries_endpoint_rejects_repetition_dsl(db):
    app = create_app(db)
    with TestClient(app) as c:
        r = c.get("/entries", params={"where": '"a" * 10000000 > ""'})
        assert r.status_code == 400
        assert "repetition" in r.json()["detail"].lower()


# --------------------------------------------------------------------------
# node-count budget
# --------------------------------------------------------------------------

def test_where_rejects_deeply_nested_expression(db):
    idx = Index(db)
    entry = _entry(idx, "a")
    # Build a deeply nested (but syntactically valid) boolean expression.
    expr = "duration > 0"
    for _ in range(100):
        expr = f"({expr}) and (duration > 0)"
    with pytest.raises(WhereError):
        evaluate_where(expr, entry)


def test_where_allows_small_expressions(db):
    idx = Index(db)
    entry = _entry(idx, "a")
    assert evaluate_where("duration > 0 and title != ''", entry) is True


# --------------------------------------------------------------------------
# Index.get()
# --------------------------------------------------------------------------

def test_index_get_returns_entry_by_id(db):
    idx = Index(db)
    entry_a = _entry(idx, "a")
    got = idx.get(entry_a.id)
    assert got is not None
    assert got.url == "a"
    assert got.title == "Cats"


def test_index_get_returns_none_for_unknown_id(db):
    idx = Index(db)
    assert idx.get("does-not-exist") is None


def test_entries_by_id_route_uses_index_get(db):
    idx = Index(db)
    entry_a = _entry(idx, "a")
    app = create_app(db)
    with TestClient(app) as c:
        r = c.get(f"/entries/{entry_a.id}")
        assert r.status_code == 200
        assert r.json()["url"] == "a"
        r404 = c.get("/entries/does-not-exist")
        assert r404.status_code == 404


def test_strm_route_uses_index_get(db):
    idx = Index(db)
    entry_c = _entry(idx, "c")  # bandcamp, has stream
    app = create_app(db)
    with TestClient(app) as c:
        r = c.get(f"/strm/{entry_c.id}")
        assert r.status_code == 200
        assert r.text.strip() == "https://x/c.mp3"
        r404 = c.get("/strm/does-not-exist")
        assert r404.status_code == 404


# --------------------------------------------------------------------------
# /healthz honesty
# --------------------------------------------------------------------------

def test_healthz_200_on_good_db(db):
    app = create_app(db)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_healthz_503_on_broken_db(tmp_path, monkeypatch):
    bad_db = tmp_path / "missing.json"  # never written -> unreadable envelope
    app = create_app(str(bad_db))

    import media_archivist.server.routes as routes_mod

    class _BoomIndex:
        def __init__(self, *a, **kw):
            raise RuntimeError("db unreadable")

    monkeypatch.setattr(routes_mod, "Index", _BoomIndex)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 503
        assert r.json()["status"] == "unhealthy"


# --------------------------------------------------------------------------
# L2: db_path must not leak the absolute filesystem path
# --------------------------------------------------------------------------

def test_db_path_not_leaked_in_healthz_stats_feed(db, tmp_path):
    app = create_app(db)
    with TestClient(app) as c:
        abs_marker = str(tmp_path)
        for path in ("/healthz", "/stats", "/feed.rss"):
            r = c.get(path)
            assert abs_marker not in r.text, f"{path} leaked absolute db_path"


# --------------------------------------------------------------------------
# bandcamp explicit mapping
# --------------------------------------------------------------------------

def test_bandcamp_explicit_field_is_mapped(db):
    idx = Index(db)
    entry_c = _entry(idx, "c")
    assert entry_c.explicit is True

    out = [e.url for e in idx.view(explicit=True)]
    assert out == ["c"]
