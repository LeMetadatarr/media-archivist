# SPDX-License-Identifier: Apache-2.0
"""Jellyfin library refresh and collections, against a fake Jellyfin server."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from media_archivist import indexdb, jellyfin, links, strm
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.server import auth
from media_archivist.server.app import create_app
from media_archivist.storage import EnvelopeJsonStorage

TOKEN = "jellyfin-token"


class FakeJellyfin:
    """Just enough of Jellyfin's API: items, a refresh that scans a folder, BoxSets."""

    def __init__(self):
        self.items = {}
        self.boxsets = {}
        self.calls = []
        self.scan = None
        self.lock = threading.Lock()
        self.next_id = 1

    def new_id(self):
        with self.lock:
            self.next_id += 1
            return f"{self.next_id:032x}"

    def add_movie(self, path, provider_ids=None):
        item_id = self.new_id()
        self.items[item_id] = {"Id": item_id, "Type": "Movie", "Path": path,
                               "ProviderIds": provider_ids or {}}
        return item_id

    def handle(self, method, path, query):
        self.calls.append((method, path, query))
        if path == "/Library/Refresh" and method == "POST":
            if self.scan:
                self.scan()
            return 204, None
        if path == "/Items" and method == "GET":
            kinds = (query.get("IncludeItemTypes") or "").split(",")
            parent = query.get("ParentId")
            if parent:
                found = [self.items[i] for i in self.boxsets[parent]["items"] if i in self.items]
            elif "BoxSet" in kinds:
                found = [{"Id": k, "Name": v["Name"], "Type": "BoxSet"} for k, v in self.boxsets.items()]
            else:
                found = [i for i in self.items.values() if i["Type"] in kinds]
            start, limit = int(query.get("StartIndex", 0)), int(query.get("Limit", 1000))
            return 200, {"Items": found[start:start + limit], "TotalRecordCount": len(found)}
        if path == "/Collections" and method == "POST":
            box = self.new_id()
            ids = [i for i in (query.get("Ids") or "").split(",") if i]
            self.boxsets[box] = {"Name": query["Name"], "items": ids}
            return 200, {"Id": box}
        if path.startswith("/Collections/") and path.endswith("/Items"):
            box = self.boxsets[path.split("/")[2]]
            ids = [i for i in query["Ids"].split(",") if i]
            if method == "POST":
                box["items"] += [i for i in ids if i not in box["items"]]
            else:
                box["items"] = [i for i in box["items"] if i not in ids]
            return 204, None
        return 404, {"error": path}


@pytest.fixture
def fake_jellyfin():
    fake = FakeJellyfin()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            if self.headers.get("X-Emby-Token") != TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            status, body = fake.handle(self.command, url.path, query)
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = _serve

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield fake
    server.shutdown()
    server.server_close()


def watch(vid):
    return f"https://www.youtube.com/watch?v={vid}"


@pytest.fixture
def env(tmp_path, monkeypatch, fake_jellyfin):
    for var in (auth.ENV_KEY, auth.ENV_EXEMPT, links.ENV_SECRET, links.ENV_PUBLIC_URL):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(jellyfin.ENV_URL, fake_jellyfin.url)
    monkeypatch.setenv(jellyfin.ENV_API_KEY, TOKEN)
    monkeypatch.setattr(jellyfin, "POLL_S", 0.02)
    monkeypatch.setattr(jellyfin, "WAIT_S", 2.0)
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv(strm.ENV_LIBRARY_ROOTS, str(library))
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    for vid, title in (("vidAAAAAAAA", "First"), ("vidBBBBBBBB", "Second"), ("vidCCCCCCCC", "Third")):
        db[watch(vid)] = {"source": "youtube", "url": watch(vid), "videoId": vid, "title": title,
                          "author": "Some Channel", "published": "2024-05-03"}
    db.store()
    index = indexdb.open_index(str(path))
    with index.write() as conn:
        conn.execute("INSERT INTO playlists (playlist_id, title) VALUES ('youtube:playlist:PLyyyy', 'Animation')")
        conn.executemany("INSERT INTO playlist_items (playlist_id, entry_id, position) VALUES (?, ?, ?)",
                         [("youtube:playlist:PLyyyy", stable_id(Source.YOUTUBE, watch(v)), n)
                          for n, v in enumerate(("vidAAAAAAAA", "vidBBBBBBBB"))])
    yield {"db": str(path), "target": library / "youtube"}
    indexdb.close_all()


def wait_done(c, export_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        rec = c.get(f"/api/v1/exports/{export_id}").json()
        if rec["posters_pending"] == 0 and rec["jellyfin"].get("collection") != "pending":
            return rec
        assert time.monotonic() < deadline, rec
        time.sleep(0.05)


def scan_into(fake, target, by_provider=False):
    """Make the fake's refresh pick up every .strm under ``target``, as Jellyfin's path."""
    def scan():
        known = {i["Path"] for i in fake.items.values()}
        for strm_file in target.rglob("*.strm"):
            jf_path = "/media/streams/youtube/" + strm_file.parent.name + "/" + strm_file.name
            if jf_path in known:
                continue
            vid = strm_file.stem.rsplit("[", 1)[1].rstrip("]")
            if by_provider:
                fake.add_movie("/elsewhere/movie.strm", {"YouTube": vid})
            else:
                fake.add_movie(jf_path)
    fake.scan = scan


def export(c, **body):
    return c.post("/api/v1/exports", json=body)


def test_video_id_of_an_item():
    assert jellyfin.video_id_of({"Path": "/m/A (2024) [abc_DEF-123]/A (2024) [abc_DEF-123].strm"}) == "abc_DEF-123"
    assert jellyfin.video_id_of({"ProviderIds": {"YouTube": "xyz"}, "Path": "/m/a.strm"}) == "xyz"
    assert jellyfin.video_id_of({"Path": "/m/plain.mkv"}) is None
    assert jellyfin.video_id_of({}) is None


def test_playlist_export_refreshes_and_creates_a_collection(env, fake_jellyfin):
    scan_into(fake_jellyfin, env["target"])
    with TestClient(create_app(env["db"])) as c:
        body = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                      refresh_jellyfin=True, collection=True).json()
        assert body["jellyfin"] == {"refresh": "requested", "collection": "pending"}
        rec = wait_done(c, body["id"])
    assert rec["jellyfin"]["refresh"] == "requested"
    assert rec["jellyfin"]["collection"] == "created"
    [(box_id, box)] = fake_jellyfin.boxsets.items()
    assert box["Name"] == "Animation"
    paths = [fake_jellyfin.items[i]["Path"] for i in box["items"]]
    assert [p.rsplit("[", 1)[1] for p in paths] == ["vidAAAAAAAA].strm", "vidBBBBBBBB].strm"]
    rows = indexdb.open_index(env["db"]).execute("SELECT * FROM jellyfin_collections").fetchall()
    assert [(r["playlist_id"], r["jellyfin_id"]) for r in rows] == [("youtube:playlist:PLyyyy", box_id)]


def test_items_are_matched_by_the_youtube_unique_id(env, fake_jellyfin):
    scan_into(fake_jellyfin, env["target"], by_provider=True)
    with TestClient(create_app(env["db"])) as c:
        body = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                      jellyfin_refresh=True, collection=True).json()
        rec = wait_done(c, body["id"])
    assert rec["jellyfin"]["collection"] == "created"
    [box] = fake_jellyfin.boxsets.values()
    assert len(box["items"]) == 2


def test_a_second_export_updates_only_its_own_collection(env, fake_jellyfin):
    scan_into(fake_jellyfin, env["target"])
    stranger = fake_jellyfin.add_movie("/media/other/film.mkv")
    with TestClient(create_app(env["db"])) as c:
        first = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                       refresh_jellyfin=True, collection=True).json()
        wait_done(c, first["id"])
        [box_id] = fake_jellyfin.boxsets
        fake_jellyfin.boxsets[box_id]["items"].append(stranger)
        index = indexdb.open_index(env["db"])
        with index.write() as conn:
            conn.execute("DELETE FROM playlist_items WHERE entry_id = ?",
                         (stable_id(Source.YOUTUBE, watch("vidAAAAAAAA")),))
            conn.execute("INSERT INTO playlist_items (playlist_id, entry_id, position) VALUES (?, ?, 5)",
                         ("youtube:playlist:PLyyyy", stable_id(Source.YOUTUBE, watch("vidCCCCCCCC"))))
        second = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                        refresh_jellyfin=True, collection=True).json()
        rec = wait_done(c, second["id"])
    assert rec["jellyfin"]["collection"] == "updated"
    assert list(fake_jellyfin.boxsets) == [box_id]
    vids = sorted(jellyfin.video_id_of(fake_jellyfin.items[i]) for i in fake_jellyfin.boxsets[box_id]["items"])
    assert vids == ["vidBBBBBBBB", "vidCCCCCCCC"]


def test_a_collection_it_did_not_make_is_never_changed(env, fake_jellyfin):
    scan_into(fake_jellyfin, env["target"])
    mine = fake_jellyfin.add_movie("/media/mine.mkv")
    fake_jellyfin.boxsets["f" * 32] = {"Name": "Animation", "items": [mine]}
    with TestClient(create_app(env["db"])) as c:
        body = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                      refresh_jellyfin=True, collection=True).json()
        rec = wait_done(c, body["id"])
    assert rec["jellyfin"]["collection"].startswith("skipped")
    assert fake_jellyfin.boxsets == {"f" * 32: {"Name": "Animation", "items": [mine]}}
    assert not any(m != "GET" and p.startswith("/Collections") for m, p, _ in fake_jellyfin.calls)


def test_items_jellyfin_never_scans_leave_no_collection(env, fake_jellyfin):
    with TestClient(create_app(env["db"])) as c:
        body = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                      refresh_jellyfin=True, collection=True).json()
        rec = wait_done(c, body["id"])
    assert rec["jellyfin"]["collection"] == "not made: Jellyfin has not scanned the exported items"
    assert fake_jellyfin.boxsets == {}


def test_collection_applies_only_to_playlists(env, fake_jellyfin):
    with TestClient(create_app(env["db"])) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"]),
                      collection=True).json()
        rec = wait_done(c, body["id"])
    assert rec["jellyfin"] == {"refresh": "not requested", "collection": "not a playlist export"}


def test_a_jellyfin_failure_is_reported_not_raised(env, fake_jellyfin, monkeypatch):
    monkeypatch.setenv(jellyfin.ENV_API_KEY, "wrong")
    with TestClient(create_app(env["db"])) as c:
        body = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(env["target"]),
                      refresh_jellyfin=True, collection=True).json()
        rec = wait_done(c, body["id"])
    assert rec["jellyfin"]["refresh"].startswith("failed")
    assert rec["jellyfin"]["collection"].startswith("failed")
    assert len(rec["written"]) == 4
