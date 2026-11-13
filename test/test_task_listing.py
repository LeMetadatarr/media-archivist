# SPDX-License-Identifier: Apache-2.0
"""GET /tasks lists tasks newest first, paged, beside the per-status counts."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.models.api import ArchiveRequest, DownloadRequest, Task  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.server.scheduler import TaskStore  # noqa: E402


def _seed(db_path, specs):
    """Write a task ledger: one task per (id, created, status, kind)."""
    ledger = {}
    for tid, created, status, kind in specs:
        req = (ArchiveRequest(url=f"https://www.youtube.com/watch?v={tid:0<11}")
               if kind == "archive" else DownloadRequest(entry_id=tid))
        ledger[tid] = Task(id=tid, status=status, request=req, created=created).model_dump(mode="json")
    store = TaskStore(db_path)
    store.path.write_text(json.dumps(ledger))


SPECS = [
    ("a", "2026-01-01T00:00:00+00:00", "ok", "archive"),
    ("b", "2026-01-03T00:00:00+00:00", "error", "download"),
    ("c", "2026-01-02T00:00:00+00:00", "ok", "archive"),
    ("d", "2026-01-03T00:00:00+00:00", "ok", "archive"),  # same second as b, submitted later
]


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "db.json")
    _seed(db_path, SPECS)
    with TestClient(create_app(db_path)) as c:
        yield c


def _ids(body):
    return [t["id"] for t in body["tasks"]]


def test_newest_first_with_counts(client):
    body = client.get("/tasks").json()
    assert _ids(body) == ["d", "b", "c", "a"]
    assert (body["ok"], body["error"], body["total"], body["matched"]) == (3, 1, 4, 4)
    assert (body["limit"], body["offset"]) == (50, 0)


def test_paging(client):
    assert _ids(client.get("/tasks?limit=2").json()) == ["d", "b"]
    page = client.get("/tasks?limit=2&offset=2").json()
    assert _ids(page) == ["c", "a"] and page["matched"] == 4
    assert _ids(client.get("/tasks?offset=10").json()) == []


def test_filters(client):
    body = client.get("/tasks?status=ok").json()
    assert _ids(body) == ["d", "c", "a"]
    assert body["matched"] == 3 and body["total"] == 4
    assert _ids(client.get("/tasks?kind=download").json()) == ["b"]
    assert client.get("/tasks?status=bogus").status_code == 422
    assert client.get("/tasks?limit=0").status_code == 422


def test_task_lookup_still_works(client):
    assert client.get("/tasks/b").json()["request"]["kind"] == "download"
    assert client.get("/tasks/zzz").status_code == 404
