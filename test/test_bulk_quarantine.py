# SPDX-License-Identifier: Apache-2.0
"""Bulk accept/reject for the Quarantine WebUI.

A homelabber running a big canonicalize can land hundreds of dedup conflicts
in quarantine; these routes let them select many rows and resolve them in
one request instead of clicking Accept/Reject one row at a time.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.canonicalize import save_quarantine  # noqa: E402
from media_archivist.models.canonical import stable_id  # noqa: E402
from media_archivist.models.canonical_record import (  # noqa: E402
    QuarantineEntry,
    QuarantineSidecar,
)
from media_archivist.models.raw import Source  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402
from mediavocab.models.signals import SignalConflict  # noqa: E402


def _seed(tmp_path, n=3):
    """Seed ``n`` real SignalConflict quarantine rows plus their source
    records, mirroring how canonicalize actually populates quarantine."""
    db_path = tmp_path / "q-db.json"
    db = EnvelopeJsonStorage(str(db_path))
    row_ids = []
    for i in range(n):
        url = f"https://www.youtube.com/watch?v=q{i}"
        db[url] = {
            "source": "youtube",
            "url": url,
            "videoId": f"q{i}",
            "title": f"Video {i}",
            "duration": 100 + i,
        }
        row_ids.append(stable_id(Source.YOUTUBE, url))
    db.store()

    sidecar = QuarantineSidecar()
    for i, rid in enumerate(row_ids):
        sidecar.entries[rid] = QuarantineEntry(
            row_id=rid,
            candidate_canonical_id=f"canon:video-{i}",
            conflicts=[SignalConflict(signal="title", ours=f"Video {i}",
                                      theirs=f"Video {i} (Official)")],
        )
    save_quarantine(str(db_path), sidecar)
    return db_path, row_ids


def test_bulk_accept_selected_rows(tmp_path):
    db_path, row_ids = _seed(tmp_path, 3)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post("/ui/quarantine/bulk/accept",
                   data={"row_ids": [row_ids[0], row_ids[1]]})
        assert r.status_code == 200, r.text
        assert "Accepted 2" in r.text
        assert f"q-row-{row_ids[0]}" not in r.text
        assert f"q-row-{row_ids[1]}" not in r.text
        assert f"q-row-{row_ids[2]}" in r.text

        # Confirm state actually changed, not just the fragment.
        r2 = c.get("/ui/quarantine/list")
        assert f"q-row-{row_ids[2]}" in r2.text
        assert f"q-row-{row_ids[0]}" not in r2.text


def test_bulk_reject_selected_rows(tmp_path):
    db_path, row_ids = _seed(tmp_path, 3)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post("/ui/quarantine/bulk/reject",
                   data={"row_ids": [row_ids[0], row_ids[2]]})
        assert r.status_code == 200, r.text
        assert "Rejected 2" in r.text
        assert f"q-row-{row_ids[0]}" not in r.text
        assert f"q-row-{row_ids[2]}" not in r.text
        assert f"q-row-{row_ids[1]}" in r.text


def test_bulk_accept_skips_unknown_or_already_resolved_ids(tmp_path):
    db_path, row_ids = _seed(tmp_path, 3)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post("/ui/quarantine/bulk/accept",
                   data={"row_ids": [row_ids[0], "not-a-real-row-id"]})
        assert r.status_code == 200, r.text
        assert "Accepted 1" in r.text
        assert "1 already resolved or unknown" in r.text
        assert f"q-row-{row_ids[0]}" not in r.text
        assert f"q-row-{row_ids[1]}" in r.text
        assert f"q-row-{row_ids[2]}" in r.text


def test_bulk_accept_empty_selection_is_a_noop(tmp_path):
    db_path, row_ids = _seed(tmp_path, 3)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post("/ui/quarantine/bulk/accept", data=[])
        assert r.status_code == 200, r.text
        assert "Nothing selected" in r.text
        for rid in row_ids:
            assert f"q-row-{rid}" in r.text


def test_select_all_checkbox_present_in_list_fragment(tmp_path):
    db_path, row_ids = _seed(tmp_path, 3)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.get("/ui/quarantine/list")
        assert r.status_code == 200, r.text
        assert 'id="q-select-all"' in r.text
        for rid in row_ids:
            assert f'name="row_ids" value="{rid}"' in r.text


def test_single_row_accept_still_works_alongside_bulk(tmp_path):
    """Regression: the per-row Accept/Reject buttons must keep working now
    that a checkbox column and bulk form wrap the table."""
    db_path, row_ids = _seed(tmp_path, 3)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post(f"/ui/quarantine/{row_ids[0]}/accept")
        assert r.status_code == 200, r.text
        assert "Accepted" in r.text

        r2 = c.post(f"/ui/quarantine/{row_ids[1]}/reject")
        assert r2.status_code == 200, r2.text
        assert "Rejected" in r2.text

        r3 = c.get("/ui/quarantine/list")
        assert f"q-row-{row_ids[0]}" not in r3.text
        assert f"q-row-{row_ids[1]}" not in r3.text
        assert f"q-row-{row_ids[2]}" in r3.text
