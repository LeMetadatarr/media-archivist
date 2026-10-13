# SPDX-License-Identifier: Apache-2.0
"""htmx WebUI — server-rendered pages served alongside the JSON API."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


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
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def test_dashboard_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "media_archivist" in r.text


def test_entries_page_returns_html(client):
    r = client.get("/ui/entries")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_entries_table_fragment_with_filter(client):
    r = client.get("/ui/entries/table", params={"source": "bandcamp"})
    assert r.status_code == 200
    assert "Hello Bandcamp" in r.text
    assert "Hello YouTube" not in r.text


def test_entries_table_bad_where_returns_inline_error_not_500(client):
    # 200, not 400: htmx does not swap non-2xx response bodies by default,
    # so a 400 here would leave the table silently showing stale results
    # while the user's invalid DSL is quietly dropped on the floor.
    r = client.get("/ui/entries/table", params={"where": "import os"})
    assert r.status_code == 200
    assert "where" in r.text.lower()


def test_entry_detail_fragment(client):
    listing = client.get("/entries").json()
    entry_id = listing["entries"][0]["id"]
    r = client.get(f"/ui/entries/{entry_id}")
    assert r.status_code == 200
    assert entry_id in r.text


def test_providers_page_lists_providers(client):
    r = client.get("/ui/providers")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_static_app_css_served(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert "--bg" in r.text


def test_static_htmx_served(client):
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert len(r.text) > 1000


def test_quarantine_page_empty_state(client):
    r = client.get("/ui/quarantine/list")
    assert r.status_code == 200
    assert "No quarantined" in r.text


def test_archive_fragment_returns_task_id(client):
    r = client.post(
        "/ui/archive",
        data={"url": "https://www.youtube.com/@x", "backend": "youtube"},
    )
    assert r.status_code == 200
    assert "task id" in r.text.lower()

    import re
    m = re.search(r"<code>([0-9a-f]{32})</code>", r.text)
    assert m, r.text
    task_id = m.group(1)

    r2 = client.get(f"/ui/tasks/{task_id}")
    assert r2.status_code == 200


def test_archive_task_visible_across_webui_and_json_boundary(client):
    """The WebUI (/ui/archive) and JSON API (/tasks/{id}) must share one
    Scheduler instance. If web.py ever constructs its own Scheduler, a task
    submitted through the HTML route would be invisible to the JSON route.
    """
    r = client.post(
        "/ui/archive",
        data={"url": "https://www.youtube.com/@x", "backend": "youtube"},
    )
    assert r.status_code == 200

    import re
    m = re.search(r"<code>([0-9a-f]{32})</code>", r.text)
    assert m, r.text
    task_id = m.group(1)

    # JSON boundary (routes.py), not the /ui/ HTML boundary (web.py).
    r2 = client.get(f"/tasks/{task_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == task_id


def test_entry_detail_blocks_javascript_url(tmp_path):
    """A provider-injected javascript:/data: scheme must never reach an
    href/src attribute in the rendered entry_detail fragment (stored XSS).
    """
    db_path = tmp_path / "xss-db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=evil"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=evil",
        "videoId": "evil",
        "title": "Evil entry",
        "duration": 10,
        "thumbnail": "javascript:alert(1)",
        "stream": "javascript:alert(document.cookie)",
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        listing = c.get("/entries").json()
        entry_id = listing["entries"][0]["id"]
        r = c.get(f"/ui/entries/{entry_id}")
        assert r.status_code == 200
        assert "javascript:" not in r.text
        assert 'href=""' in r.text or 'src=""' in r.text


def test_quarantine_renders_real_signalconflicts_not_500(tmp_path):
    """Regression: quarantine rows produced by canonicalization hold
    ``SignalConflict`` objects, but the JSON model + UI expect strings.

    Before the ``render_conflict`` fix, both the JSON ``/quarantine`` endpoint
    and the ``/ui/quarantine/list`` fragment raised a 500 (pydantic
    ``string_type`` validation error) the moment any real conflict existed —
    they only ever passed because tests seeded zero conflicts.
    """
    from media_archivist.canonicalize import save_quarantine
    from media_archivist.models.canonical import stable_id
    from media_archivist.models.canonical_record import (
        QuarantineEntry,
        QuarantineSidecar,
    )
    from media_archivist.models.raw import Source
    from mediavocab.models.signals import SignalConflict

    db_path = tmp_path / "q-db.json"
    db = EnvelopeJsonStorage(str(db_path))
    url = "https://www.youtube.com/watch?v=q"
    db[url] = {
        "source": "youtube",
        "url": url,
        "videoId": "q",
        "title": "Big Buck Bunny (Blender Open Movie)",
        "duration": 635,
    }
    db.store()

    rid = stable_id(Source.YOUTUBE, url)
    sidecar = QuarantineSidecar()
    sidecar.entries[rid] = QuarantineEntry(
        row_id=rid,
        candidate_canonical_id="canon:big-buck-bunny",
        conflicts=[SignalConflict(signal="title", ours="Big Buck Bunny (Blender Open Movie)",
                                  theirs="Big Buck Bunny (2008)")],
    )
    save_quarantine(str(db_path), sidecar)

    app = create_app(str(db_path))
    with TestClient(app) as c:
        # JSON endpoint: must be 200 with the conflict rendered as a readable string.
        rj = c.get("/quarantine")
        assert rj.status_code == 200, rj.text
        payload = rj.json()
        assert payload["total"] == 1
        conflicts = payload["entries"][0]["conflicts"]
        assert conflicts and all(isinstance(x, str) for x in conflicts)
        assert "title" in conflicts[0] and "Big Buck Bunny (2008)" in conflicts[0]

        # WebUI fragment: must be 200 and contain the readable conflict text,
        # never the raw SignalConflict repr.
        rh = c.get("/ui/quarantine/list")
        assert rh.status_code == 200, rh.text
        assert "Big Buck Bunny (2008)" in rh.text
        assert "SignalConflict(" not in rh.text
