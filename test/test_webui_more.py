# SPDX-License-Identifier: Apache-2.0
"""Additional htmx WebUI coverage: filter combinations, external_ids
rendering, quarantine accept/reject lifecycle, health-dot failure path,
dashboard stats, providers badges, static assets, and quick-link feeds.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.canonicalize import save_canonical, save_quarantine  # noqa: E402
from media_archivist.models.canonical import stable_id  # noqa: E402
from media_archivist.models.canonical_record import (  # noqa: E402
    CanonicalRecord,
    CanonicalSidecar,
    QuarantineEntry,
    QuarantineSidecar,
)
from media_archivist.models.raw import Source  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402
from mediavocab.models import ExternalIds  # noqa: E402
from mediavocab.models.signals import SignalConflict, Signals  # noqa: E402


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
        "tags": ["x"],
    }
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "Hello Bandcamp",
        "artist": "Foo",
        "duration": 200,
        "stream": "https://x.bandcamp.com/stream.mp3",
    }
    db["https://music.youtube.com/watch?v=z"] = {
        "source": "youtube_music",
        "url": "https://music.youtube.com/watch?v=z",
        "title": "Short Explicit Track",
        "artist": "Foo",
        "duration": 30,
        "explicit": True,
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


# --- /ui/entries/table filter combinations ---------------------------------

def test_entries_table_combined_filters_source_grep_has_stream(client):
    r = client.get("/ui/entries/table", params={
        "source": "bandcamp", "grep": "hello", "has_stream": True,
    })
    assert r.status_code == 200
    assert "Hello Bandcamp" in r.text
    assert "Short Explicit Track" not in r.text
    assert "Hello YouTube" not in r.text


def test_entries_table_grep_is_case_insensitive(client):
    r = client.get("/ui/entries/table", params={"grep": "HELLO YOUTUBE"})
    assert r.status_code == 200
    assert "Hello YouTube" in r.text
    assert "Hello Bandcamp" not in r.text


def test_entries_table_explicit_filter(client):
    r = client.get("/ui/entries/table", params={"explicit": True})
    assert r.status_code == 200
    assert "Short Explicit Track" in r.text
    assert "Hello Bandcamp" not in r.text
    assert "Hello YouTube" not in r.text


def test_entries_table_where_dsl_positive_case(client):
    r = client.get("/ui/entries/table", params={"where": "duration > 300"})
    assert r.status_code == 200
    # nothing exceeds 300s in the fixture set
    assert "No entries match this filter" in r.text
    for title in ("Hello YouTube", "Hello Bandcamp", "Short Explicit Track"):
        assert title not in r.text

    r2 = client.get("/ui/entries/table", params={"where": "duration > 150"})
    assert r2.status_code == 200
    assert "Hello YouTube" in r2.text
    assert "Hello Bandcamp" in r2.text
    assert "Short Explicit Track" not in r2.text


def test_entries_table_limit_caps_results(client):
    r = client.get("/ui/entries/table", params={"limit": 1})
    assert r.status_code == 200
    assert "Showing 1–1 of" in r.text


# --- /ui/entries/{id} detail -------------------------------------------------

def test_entry_detail_shows_only_populated_external_ids(tmp_path):
    db_path = tmp_path / "eid-db.json"
    db = EnvelopeJsonStorage(str(db_path))
    url = "https://www.youtube.com/watch?v=eid"
    db[url] = {
        "source": "youtube",
        "url": url,
        "videoId": "eid",
        "title": "Rick Astley - Never Gonna Give You Up",
        "duration": 213,
    }
    db.store()

    rid = stable_id(Source.YOUTUBE, url)
    canon_id = "canon:rick-astley-never-gonna-give-you-up"
    canonical = CanonicalSidecar()
    canonical.records[canon_id] = CanonicalRecord(
        canonical_id=canon_id,
        signals=Signals(title="Never Gonna Give You Up"),
        members=[rid],
        external_ids=ExternalIds(musicbrainz_recording="7f8f5c1e-abc1-4d3a-9c1a-000000000000"),
    )
    save_canonical(str(db_path), canonical)

    raw = db[url]
    raw["_meta"] = {"canonical_id": canon_id, "canonical_status": "matched"}
    db[url] = raw
    db.store()

    app = create_app(str(db_path))
    with TestClient(app) as c:
        listing = c.get("/entries").json()
        entry_id = listing["entries"][0]["id"]
        r = c.get(f"/ui/entries/{entry_id}")
        assert r.status_code == 200
        assert "musicbrainz_recording" in r.text
        assert "7f8f5c1e-abc1-4d3a-9c1a-000000000000" in r.text
        # No wall of unset external-id fields: pick a couple of the many
        # ExternalIds fields that were left unset and assert they never
        # rendered as "key = None" or at all.
        assert "imdb" not in r.text
        assert "tvdb" not in r.text
        assert "= None" not in r.text


def test_entry_detail_unknown_id_returns_404(client):
    r = client.get("/ui/entries/does-not-exist")
    assert r.status_code == 404


# --- quarantine accept/reject lifecycle -------------------------------------

def _seed_quarantine_row(db_path, url="https://www.youtube.com/watch?v=q", title="Big Buck Bunny (Blender Open Movie)"):
    rid = stable_id(Source.YOUTUBE, url)
    sidecar = QuarantineSidecar()
    sidecar.entries[rid] = QuarantineEntry(
        row_id=rid,
        candidate_canonical_id="canon:big-buck-bunny",
        conflicts=[SignalConflict(signal="title", ours=title, theirs="Big Buck Bunny (2008)")],
    )
    save_quarantine(str(db_path), sidecar)
    return rid


def test_quarantine_accept_removes_row(tmp_path):
    db_path = tmp_path / "q-db.json"
    db = EnvelopeJsonStorage(str(db_path))
    url = "https://www.youtube.com/watch?v=q"
    db[url] = {"source": "youtube", "url": url, "videoId": "q",
               "title": "Big Buck Bunny (Blender Open Movie)", "duration": 635}
    db.store()
    rid = _seed_quarantine_row(db_path, url)

    app = create_app(str(db_path))
    with TestClient(app) as c:
        pre = c.get("/ui/quarantine/list")
        assert rid in pre.text

        r = c.post(f"/ui/quarantine/{rid}/accept")
        assert r.status_code == 200

        post = c.get("/ui/quarantine/list")
        assert post.status_code == 200
        assert rid not in post.text
        assert "No quarantined" in post.text


def test_quarantine_reject_removes_row(tmp_path):
    db_path = tmp_path / "q-db.json"
    db = EnvelopeJsonStorage(str(db_path))
    url = "https://www.youtube.com/watch?v=q2"
    db[url] = {"source": "youtube", "url": url, "videoId": "q2",
               "title": "Big Buck Bunny (Blender Open Movie)", "duration": 635}
    db.store()
    rid = _seed_quarantine_row(db_path, url)

    app = create_app(str(db_path))
    with TestClient(app) as c:
        r = c.post(f"/ui/quarantine/{rid}/reject")
        assert r.status_code == 200

        post = c.get("/ui/quarantine/list")
        assert post.status_code == 200
        assert rid not in post.text


def test_quarantine_accept_unknown_row_id_visible_error(client):
    # 200, not 404: htmx does not swap 4xx bodies by default, so a 404 here
    # would leave the row silently un-updated. The user must see the error.
    r = client.post("/ui/quarantine/does-not-exist/accept")
    assert r.status_code == 200
    assert "not accepted" in r.text.lower() or "no longer in quarantine" in r.text.lower()


def test_quarantine_reject_unknown_row_id_visible_error(client):
    r = client.post("/ui/quarantine/does-not-exist/reject")
    assert r.status_code == 200
    assert "not rejected" in r.text.lower() or "no longer in quarantine" in r.text.lower()


# --- health dot ---------------------------------------------------------

def test_health_dot_healthy(client):
    r = client.get("/ui/health-dot")
    assert r.status_code == 200
    assert 'class="health-dot ok"' in r.text


def test_health_dot_unhealthy_does_not_500(client, monkeypatch):
    import media_archivist.server.web as web_mod

    class _BoomIndex:
        def __init__(self, *a, **kw):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(web_mod, "Index", _BoomIndex)
    r = client.get("/ui/health-dot")
    assert r.status_code == 200
    assert 'class="health-dot ok"' not in r.text
    # Unhealthy is its own explicit class (not color-only, has a title too)
    # so it's distinguishable from the neutral pre-poll "checking" state.
    assert 'class="health-dot err"' in r.text
    assert "unreachable" in r.text.lower()


# --- dashboard ------------------------------------------------------------

def test_dashboard_shows_stats_and_source_mix(client):
    r = client.get("/")
    assert r.status_code == 200
    assert ">3<" in r.text  # total entries
    assert "bandcamp" in r.text
    assert "youtube" in r.text


def test_dashboard_quick_links_present(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/m3u"' in r.text
    assert 'href="/feed.rss"' in r.text
    assert "/strm/" in r.text


def test_dashboard_providers_strip_present(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "providers available" in r.text
    assert re.search(r"\d+/\d+", r.text)


# --- providers page ---------------------------------------------------------

def test_providers_page_renders_availability_badges(client):
    r = client.get("/ui/providers")
    assert r.status_code == 200
    assert ("badge-ok" in r.text) or ("badge-err" in r.text)


# --- static assets ----------------------------------------------------------

def test_static_htmx_contains_htmx_banner(client):
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert "htmx" in r.text.lower()


# --- quick-link feeds ---------------------------------------------------

def test_m3u_returns_playlist_with_seeded_entry(client):
    r = client.get("/m3u")
    assert r.status_code == 200
    assert "audio/x-mpegurl" in r.headers["content-type"]
    assert r.text.startswith("#EXTM3U")
    assert "x.bandcamp.com/stream.mp3" in r.text


def test_feed_rss_returns_seeded_entry(client):
    r = client.get("/feed.rss")
    assert r.status_code == 200
    assert "rss" in r.headers["content-type"]
    assert "<rss" in r.text
    assert "Hello YouTube" in r.text or "Hello Bandcamp" in r.text
