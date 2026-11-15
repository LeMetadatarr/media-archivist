# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/subscriptions``: stable ids, holders per app, refresh, playlists."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import indexdb  # noqa: E402
from media_archivist import subscriptions as subs_mod  # noqa: E402
from media_archivist.server import auth  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from ytindex_fakes import HANDLE_URL, UC, FakeGate, FakeYouTube, playlist, videos  # noqa: E402

SID = f"youtube:channel:{UC}"
SHORTARR = {"X-Archivist-App": "shortarr"}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    monkeypatch.delenv("MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS", raising=False)
    return str(tmp_path / "db.json")


@pytest.fixture
def yt():
    fake = FakeYouTube()
    fake.add_channel(UC, videos(1, 3), handle=HANDLE_URL)
    fake.tabs[UC] = ["PLa"]
    fake.playlists["PLa"] = playlist("PLa", [2, 1], title="Animation")
    return fake


@pytest.fixture
def client(db_path, yt):
    with TestClient(create_app(db_path)) as c:
        periodic = c.app.state.periodic
        periodic.index_client = yt
        periodic.index_gate = FakeGate()
        yield c


def wait_idle(c, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = c.get("/tasks").json()
        if body["queued"] == 0 and body["running"] == 0:
            return body
        time.sleep(0.02)
    pytest.fail("tasks never finished")


def test_meta_lists_the_features(client):
    assert {"subscriptions", "entries", "playlists"} <= set(client.get("/api/v1/meta").json()["features"])


def test_a_handle_resolves_once_and_two_apps_share_one_subscription(client, yt):
    r = client.post("/api/v1/subscriptions", json={"url": HANDLE_URL, "label": "Some"}, headers=SHORTARR)
    assert r.status_code == 201
    sub = r.json()
    assert sub["id"] == SID and sub["kind"] == "channel" and sub["holders"] == ["shortarr"]
    assert sub["export"] == {"automatic": False, "target_dir": None, "layout": "movies"}
    assert sub["index_playlists"] is True and sub["first_indexed_at"] is None
    wait_idle(client)
    r = client.post("/api/v1/subscriptions", json={"url": f"https://www.youtube.com/channel/{UC}/videos"})
    assert r.status_code == 200
    assert r.json()["id"] == SID and r.json()["holders"] == ["shortarr", "user"]
    assert r.json()["label"] == "Some"
    got = client.get(f"/api/v1/subscriptions/{SID}").json()
    assert got["entry_count"] == 3 and got["first_indexed_at"].endswith("Z")
    assert got["title"] == "Some Channel"
    assert got["thumbnail"] == f"/api/v1/subscriptions/{SID}/thumbnail"
    reads = [c for c in yt.calls if c[0] == "read_channel"]
    assert reads == [("read_channel", HANDLE_URL, False),
                     ("read_channel", f"https://www.youtube.com/channel/{UC}", True)]
    assert [s["id"] for s in client.get("/api/v1/subscriptions").json()["subscriptions"]] == [SID]


def test_an_unresolved_handle_is_moved_to_its_id_by_the_first_listing(client, yt):
    yt.fail["read_channel"] = ConnectionError("offline")
    r = client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    assert r.status_code == 201 and r.json()["id"] == f"youtube:url:{HANDLE_URL}"
    wait_idle(client)
    del yt.fail["read_channel"]
    r = client.post(f"/api/v1/subscriptions/youtube:url:{HANDLE_URL}/refresh")
    assert r.status_code == 202
    wait_idle(client)
    assert client.get(f"/api/v1/subscriptions/youtube:url:{HANDLE_URL}").status_code == 404
    assert client.get(f"/api/v1/subscriptions/{SID}").json()["entry_count"] == 3
    again = client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    assert again.status_code == 200 and again.json()["id"] == SID


@pytest.mark.parametrize("body,needle", [
    ({}, "url is required"),
    ({"url": "https://example.org/x"}, "YouTube"),
    ({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, "YouTube"),
    ({"url": HANDLE_URL, "colour": "red"}, "unknown field"),
    ({"url": HANDLE_URL, "index_playlists": "yes"}, "index_playlists"),
    ({"url": HANDLE_URL, "export": {"automatic": True}}, "target_dir is required"),
    ({"url": HANDLE_URL, "export": {"layout": "albums"}}, "layout"),
    ({"url": HANDLE_URL, "export": {"when": "always"}}, "unknown export field"),
])
def test_a_create_that_cannot_be_read_is_400(client, body, needle):
    r = client.post("/api/v1/subscriptions", json=body)
    assert r.status_code == 400 and needle in r.json()["detail"]
    assert client.get("/api/v1/subscriptions").json() == {"subscriptions": []}


def test_a_body_that_is_not_json_is_400(client):
    r = client.post("/api/v1/subscriptions", content=b"{nope", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_a_playlist_subscription_ignores_index_playlists(client, yt):
    r = client.post("/api/v1/subscriptions",
                    json={"url": "https://www.youtube.com/playlist?list=PLa", "index_playlists": True})
    assert r.status_code == 201
    assert r.json()["id"] == "youtube:playlist:PLa" and r.json()["index_playlists"] is False
    wait_idle(client)
    got = client.get("/api/v1/subscriptions/youtube:playlist:PLa").json()
    assert got["entry_count"] == 2 and got["title"] == "Animation"
    pl = client.get("/api/v1/playlists/youtube:playlist:PLa").json()
    assert pl["videos"] == ["youtube:v0000000002", "youtube:v0000000001"]


def test_patch_changes_export_and_label_and_clears_flagged(client, db_path):
    client.post("/api/v1/subscriptions", json={"url": f"https://www.youtube.com/channel/{UC}"})
    wait_idle(client)
    index = client.app.state.index
    with index.write() as conn:
        conn.execute("UPDATE subscriptions SET flagged = 1, failures = 3")
    r = client.patch(f"/api/v1/subscriptions/{SID}",
                     json={"export": {"automatic": True, "target_dir": "/media/streams/youtube"},
                           "label": "Mine", "index_playlists": False})
    assert r.status_code == 200
    body = r.json()
    assert body["export"] == {"automatic": True, "target_dir": "/media/streams/youtube", "layout": "movies"}
    assert body["label"] == "Mine" and body["index_playlists"] is False
    assert body["flagged"] is False and body["failures"] == 0
    r = client.patch(f"/api/v1/subscriptions/{SID}", json={"export": {"target_dir": None}})
    assert r.status_code == 400
    assert client.patch(f"/api/v1/subscriptions/{SID}", json={"url": "x"}).status_code == 400
    assert client.patch("/api/v1/subscriptions/youtube:channel:UCnope", json={}).status_code == 404


def test_delete_removes_the_calling_app_and_the_last_holder_removes_the_subscription(client):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL}, headers=SHORTARR)
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    r = client.delete(f"/api/v1/subscriptions/{SID}", headers=SHORTARR)
    assert r.json() == {"id": SID, "removed": False, "holders": ["user"]}
    assert client.get(f"/api/v1/subscriptions/{SID}").json()["holders"] == ["user"]
    r = client.delete(f"/api/v1/subscriptions/{SID}", headers=SHORTARR)
    assert r.json()["removed"] is False
    r = client.delete(f"/api/v1/subscriptions/{SID}")
    assert r.json() == {"id": SID, "removed": True, "holders": []}
    assert client.get(f"/api/v1/subscriptions/{SID}").status_code == 404
    assert client.delete(f"/api/v1/subscriptions/{SID}").status_code == 404
    assert client.get("/api/v1/entries/youtube:v0000000001").status_code == 200


def test_delete_all_holders(client):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL}, headers=SHORTARR)
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    r = client.delete(f"/api/v1/subscriptions/{SID}?all_holders=true", headers=SHORTARR)
    assert r.json()["removed"] is True
    wait_idle(client)
    assert client.get("/api/v1/subscriptions").json() == {"subscriptions": []}


def test_subscribing_again_does_not_make_the_back_catalogue_new(client, yt):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    first = client.get("/api/v1/entries", params={"subscription": SID}).json()["entries"]
    client.delete(f"/api/v1/subscriptions/{SID}")
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    again = client.get("/api/v1/entries", params={"subscription": SID}).json()["entries"]
    assert [e["first_seen"] for e in again] == [e["first_seen"] for e in first]


def test_refresh_queues_a_listing_and_returns_its_task(client, yt):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    yt.channels[UC].videos += videos(4, 1)
    r = client.post(f"/api/v1/subscriptions/{SID}/refresh")
    assert r.status_code == 202
    task = r.json()["task"]
    assert task["request"] == {"kind": "index", "subscription_id": SID}
    wait_idle(client)
    assert client.get(f"/tasks/{task['id']}").json()["status"] == "ok"
    assert client.get(f"/api/v1/subscriptions/{SID}").json()["entry_count"] == 4
    assert client.post("/api/v1/subscriptions/youtube:channel:UCnope/refresh").status_code == 404


def test_a_refresh_while_listings_are_paused_waits(client, yt):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    client.app.state.periodic.index_gate.paused_kinds = {"listing"}
    task = client.post(f"/api/v1/subscriptions/{SID}/refresh").json()["task"]
    wait_idle(client)
    done = client.get(f"/tasks/{task['id']}").json()
    assert done["status"] == "ok" and done["detail"].startswith("waiting")
    assert client.app.state.index.execute("SELECT listing_requested FROM subscriptions").fetchone()[0] == 1


def test_a_failed_listing_fails_its_task_and_is_counted(client, yt):
    yt.fail["read_channel"] = ConnectionError("offline")
    client.post("/api/v1/subscriptions", json={"url": f"https://www.youtube.com/channel/{UC}"})
    wait_idle(client)
    task = client.get("/tasks").json()["tasks"][0]
    assert task["status"] == "error" and "offline" in task["error"]
    got = client.get(f"/api/v1/subscriptions/{SID}").json()
    assert got["failures"] == 1 and got["last_error"] == "offline" and not got["flagged"]


def test_thumbnail_redirects_to_the_stored_picture(client, yt):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    r = client.get(f"/api/v1/subscriptions/{SID}/thumbnail", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://yt3.example/c.jpg"
    with client.app.state.index.write() as conn:
        conn.execute("UPDATE subscriptions SET thumbnail = 'javascript:alert(1)'")
    assert client.get(f"/api/v1/subscriptions/{SID}/thumbnail", follow_redirects=False).status_code == 404
    assert client.get("/api/v1/subscriptions/youtube:channel:UCnope/thumbnail").status_code == 404


def test_channel_playlists_and_one_playlist(client, yt):
    client.post("/api/v1/subscriptions", json={"url": HANDLE_URL})
    wait_idle(client)
    ix = client.app.state.periodic.indexer()
    ix.run(SID, "playlists")
    body = client.get(f"/api/v1/subscriptions/{SID}/playlists").json()
    assert body["listed_at"].endswith("Z")
    assert body["playlists"] == [{
        "id": "youtube:playlist:PLa", "title": None,
        "url": "https://www.youtube.com/playlist?list=PLa", "thumbnail": None,
        "video_count": None, "videos": None, "videos_at": None, "error": None}]
    ix.run("*", "playlist_videos")
    pl = client.get(f"/api/v1/subscriptions/{SID}/playlists").json()["playlists"][0]
    assert pl["title"] == "Animation" and pl["video_count"] == 2
    assert pl["videos"] == ["youtube:v0000000002", "youtube:v0000000001"]
    assert pl["thumbnail"] == "https://i.ytimg.com/pl/PLa.jpg"
    assert client.get("/api/v1/playlists/youtube:playlist:PLa").json() == pl
    entries = client.get("/api/v1/entries", params={"playlist": "youtube:playlist:PLa"}).json()["entries"]
    assert {e["key"] for e in entries} == {"youtube:v0000000001", "youtube:v0000000002"}
    assert entries[0]["playlists"] == [{"id": "youtube:playlist:PLa", "title": "Animation"}]
    assert client.get("/api/v1/playlists/youtube:playlist:PLnope").status_code == 404
    assert client.get("/api/v1/subscriptions/youtube:channel:UCnope/playlists").status_code == 404


def test_the_import_rows_are_rekeyed_by_the_first_listing(db_path, yt):
    subs_mod.add_subscription(db_path, HANDLE_URL, label="from the CLI")
    with TestClient(create_app(db_path)) as c:
        periodic = c.app.state.periodic
        periodic.index_client, periodic.index_gate = yt, FakeGate()
        before = c.get("/api/v1/subscriptions").json()["subscriptions"]
        assert [(s["id"], s["holders"]) for s in before] == [(f"youtube:url:{HANDLE_URL}", ["user"])]
        assert periodic.indexer().run_due()["listing:ok"] == 1
        after = c.get("/api/v1/subscriptions").json()["subscriptions"]
    assert [(s["id"], s["holders"], s["label"], s["entry_count"]) for s in after] == \
        [(SID, ["user"], "from the CLI", 3)]


def test_the_scheduled_pass_queues_one_index_task_and_skips_youtube_sidecar_syncs(db_path, yt,
                                                                                monkeypatch):
    synced = []
    monkeypatch.setattr(subs_mod, "sync_one", lambda db, url: synced.append(url) or subs_mod.SyncResult(
        url=url, backend="ia", ok=True))
    subs_mod.add_subscription(db_path, HANDLE_URL)
    subs_mod.add_subscription(db_path, "https://archive.org/details/somecollection")
    with TestClient(create_app(db_path)) as c:
        periodic = c.app.state.periodic
        periodic.index_client, periodic.index_gate = yt, FakeGate()
        periodic.enrich_enabled = False
        periodic.sync_enabled = True
        gate = periodic.index_gate
        gate.paused_kinds = {"listing", "feed", "thumb", "details"}
        queued = periodic.run_once()
        assert [(t.kind, getattr(t.request, "url", None)) for t in queued] == \
            [("sync", "https://archive.org/details/somecollection")]
        gate.paused_kinds = set()
        task, = [t for t in periodic.run_once() if t.kind == "index"]
        assert task.request.subscription_id is None
        assert [t for t in periodic.run_once() if t.kind == "index"] in ([], [task])
        wait_idle(c)
        assert c.get(f"/api/v1/subscriptions/{SID}").json()["entry_count"] == 3
        other = "ia:url:https://archive.org/details/somecollection"
        assert c.post(f"/api/v1/subscriptions/{other}/refresh").status_code == 409
        assert periodic.indexer().run(other, "listing") == "unsupported"
        assert [c for c in yt.calls if "archive.org" in str(c)] == []
    assert set(synced) == {"https://archive.org/details/somecollection"}


def test_with_the_index_off_youtube_sidecar_syncs_run_and_record_memberships(db_path, monkeypatch):
    import media_archivist.youtube as youtube_mod

    class _V:
        def __init__(self, n):
            self.video_id = f"v{n:010d}"
            self.title = f"Video {n}"
            self.watch_url = f"https://www.youtube.com/watch?v={self.video_id}"
            self.thumbnail_url = ""
            self.duration_seconds = 90

    class _Channel:
        def __init__(self, url):
            self.channel_name = "Some Channel"
            self.channel_id = UC
            self.videos = [_V(1), _V(2)]

    monkeypatch.setattr(youtube_mod, "Channel", _Channel)
    with TestClient(create_app(db_path)) as c:
        subs_mod.add_subscription(db_path, HANDLE_URL)
        assert [s["id"] for s in c.get("/api/v1/subscriptions").json()["subscriptions"]] == \
            [f"youtube:url:{HANDLE_URL}"]
        periodic = c.app.state.periodic
        periodic.enrich_enabled, periodic.sync_enabled, periodic.index_enabled = False, True, False
        assert [t.kind for t in periodic.run_once()] == ["sync"]
        wait_idle(c)
        sub = c.get(f"/api/v1/subscriptions/{SID}").json()
    assert sub["entry_count"] == 2 and sub["first_indexed_at"] and sub["holders"] == ["user"]


def test_removing_a_sidecar_subscription_releases_the_users_hold(db_path, yt):
    with TestClient(create_app(db_path)) as c:
        c.app.state.periodic.index_client = yt
        c.post("/api/v1/subscriptions", json={"url": HANDLE_URL}, headers=SHORTARR)
        subs_mod.add_subscription(db_path, HANDLE_URL)
        sub = c.get("/api/v1/subscriptions").json()["subscriptions"][0]
        assert sub["holders"] == ["shortarr", "user"]
        subs_mod.remove_subscription(db_path, HANDLE_URL)
        assert c.get("/api/v1/subscriptions").json()["subscriptions"][0]["holders"] == ["shortarr"]


def test_the_cli_alone_never_creates_an_index(db_path):
    subs_mod.add_subscription(db_path, HANDLE_URL)
    assert not indexdb.index_path(db_path).exists()
