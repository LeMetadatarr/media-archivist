# SPDX-License-Identifier: Apache-2.0
"""UX/a11y polish regressions for the htmx WebUI.

Covers: quarantine accept/reject confirmation fragments (P0 — the highest
stakes flow, previously the least protected), the visible-DSL-error fix on
the /ui/entries/table fragment endpoint (htmx does not swap 4xx bodies, so
a 400 there is invisible to the user), the uncapped ``limit`` parameter,
keyboard-accessible table rows, and the honest (non-red-by-default) health
dot.
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


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=a"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=a",
        "videoId": "a",
        "title": "Hello YouTube",
        "duration": 240,
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def _seed_quarantine_row(db_path, url="https://www.youtube.com/watch?v=q",
                          title="Big Buck Bunny (Blender Open Movie)",
                          candidate="canon:big-buck-bunny"):
    db = EnvelopeJsonStorage(str(db_path))
    db[url] = {"source": "youtube", "url": url, "videoId": "q", "title": title, "duration": 635}
    db.store()
    rid = stable_id(Source.YOUTUBE, url)
    sidecar = QuarantineSidecar()
    sidecar.entries[rid] = QuarantineEntry(
        row_id=rid,
        candidate_canonical_id=candidate,
        conflicts=[SignalConflict(signal="title", ours=title, theirs="Big Buck Bunny (2008)")],
    )
    save_quarantine(str(db_path), sidecar)
    return rid


# --- P0: quarantine accept/reject confirmation fragments --------------------

def test_quarantine_accept_returns_visible_confirmation_with_canonical_id(tmp_path):
    db_path = tmp_path / "q-db.json"
    rid = _seed_quarantine_row(db_path, candidate="canon:big-buck-bunny")

    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post(f"/ui/quarantine/{rid}/accept")
        assert r.status_code == 200
        # Before the fix this was HTMLResponse("") — the row just vanished
        # with zero feedback. Now it must say what happened.
        assert "Accepted" in r.text
        assert "canon:big-buck-bunny" in r.text


def test_quarantine_reject_returns_visible_confirmation(tmp_path):
    db_path = tmp_path / "q-db.json"
    rid = _seed_quarantine_row(db_path, url="https://www.youtube.com/watch?v=q2")

    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post(f"/ui/quarantine/{rid}/reject")
        assert r.status_code == 200
        assert "Rejected" in r.text


def test_quarantine_accept_already_resolved_shows_visible_error_not_silent(tmp_path):
    db_path = tmp_path / "q-db.json"
    rid = _seed_quarantine_row(db_path)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        first = c.post(f"/ui/quarantine/{rid}/accept")
        assert first.status_code == 200
        assert "Accepted" in first.text

        # Second accept on the same (now-resolved) row: must not silently
        # no-op — the user needs to see this row is already gone.
        second = c.post(f"/ui/quarantine/{rid}/accept")
        assert second.status_code == 200
        assert second.text.strip() != ""
        assert "no longer in quarantine" in second.text.lower()


def test_quarantine_reject_button_has_confirm_and_distinct_style(tmp_path):
    db_path = tmp_path / "q-db.json"
    _seed_quarantine_row(db_path)
    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.get("/ui/quarantine/list")
        assert r.status_code == 200
        assert "hx-confirm" in r.text
        assert "btn-ok" in r.text  # Accept: solid primary
        assert "btn-err-outline" in r.text or "btn-ghost" in r.text  # Reject: outline/ghost


# --- P1: visible DSL error on the fragment endpoint --------------------------

def test_bad_where_dsl_returns_200_with_error_text_in_swapped_body(client):
    # Fail-before: previously returned 400, and htmx does not swap 4xx
    # bodies by default — the table would silently stay stale in the
    # browser. This must now be 200 so htmx actually swaps the error in.
    r = client.get("/ui/entries/table", params={"where": "__import__('os')"})
    assert r.status_code == 200
    assert "where" in r.text.lower()
    assert "<div class=\"error\">" in r.text


def test_good_where_dsl_still_filters_normally(client):
    r = client.get("/ui/entries/table", params={"where": "duration > 100"})
    assert r.status_code == 200
    assert "Hello YouTube" in r.text


# --- P1/security-adjacent: limit is capped -----------------------------------

def test_entries_table_limit_is_capped(client):
    r = client.get("/ui/entries/table", params={"limit": 99999})
    assert r.status_code == 422


def test_entries_table_limit_zero_rejected(client):
    r = client.get("/ui/entries/table", params={"limit": 0})
    assert r.status_code == 422


def test_entries_table_limit_within_bound_ok(client):
    r = client.get("/ui/entries/table", params={"limit": 10})
    assert r.status_code == 200


# --- P1: keyboard-accessible table rows --------------------------------------

def test_entries_table_row_has_tabindex_and_role_and_keyboard_trigger(client):
    r = client.get("/ui/entries/table")
    assert r.status_code == 200
    assert 'tabindex="0"' in r.text
    assert 'role="button"' in r.text
    assert "keyup[key=='Enter']" in r.text


def test_entry_detail_still_opens_via_keyboard_trigger_route(client):
    listing = client.get("/entries").json()
    entry_id = listing["entries"][0]["id"]
    r = client.get(f"/ui/entries/{entry_id}")
    assert r.status_code == 200
    assert entry_id in r.text


# --- P2: honest, non-color-only health dot -----------------------------------

def test_health_dot_fragment_has_aria_label_reflecting_state(client):
    r = client.get("/ui/health-dot")
    assert r.status_code == 200
    assert "aria-label" in r.text
    assert "reachable" in r.text.lower()
    assert "health-dot ok" in r.text


def test_dashboard_initial_health_dot_is_not_error_colored_before_first_poll(client):
    r = client.get("/")
    assert r.status_code == 200
    # Fail-before: the dot defaulted to the plain "health-dot" class, which
    # CSS colored red (var(--err)) until the first async poll landed —
    # a false "server down" flash on every page load.
    assert 'class="health-dot checking"' in r.text
    assert 'class="health-dot"' not in r.text


# --- P2: in-flight feedback on canonicalize ----------------------------------

def test_dashboard_canonicalize_button_has_indicator_and_disables(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "hx-indicator" in r.text
    assert "hx-disabled-elt" in r.text


def test_providers_canonicalize_button_has_indicator_and_disables(client):
    r = client.get("/ui/providers")
    assert r.status_code == 200
    assert "hx-indicator" in r.text
    assert "hx-disabled-elt" in r.text


# --- P2: dashboard STRM quick link is not a literal dead link ----------------

def test_dashboard_strm_placeholder_is_not_a_clickable_link(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/strm/' not in r.text
    assert "<code>" in r.text
    assert "/strm/&lt;entry-id&gt;" in r.text or "/strm/<entry-id>" in r.text


def test_dashboard_m3u_and_rss_quick_links_remain_real_links(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/m3u"' in r.text
    assert 'href="/feed.rss"' in r.text


# --- P2: DSL syntax help ------------------------------------------------------

def test_entries_page_has_dsl_syntax_help_disclosure(client):
    r = client.get("/ui/entries")
    assert r.status_code == 200
    assert "<details" in r.text
    assert "Filter syntax" in r.text


# --- P2: archive min_duration sentinel has a helper note ---------------------

def test_archive_page_min_duration_has_helper_text(client):
    r = client.get("/ui/archive")
    assert r.status_code == 200
    assert "no minimum" in r.text.lower()
