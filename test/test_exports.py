# SPDX-License-Identifier: Apache-2.0
"""On-request .strm exports through /api/v1/exports."""
from __future__ import annotations

import os
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_archivist import indexdb, links, strm
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.server import auth
from media_archivist.server.app import create_app
from media_archivist.storage import EnvelopeJsonStorage

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def watch(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def eid(vid):
    return stable_id(Source.YOUTUBE, watch(vid))


@pytest.fixture
def thumbs():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch, thumbs):
    for var in (auth.ENV_KEY, auth.ENV_EXEMPT, links.ENV_SECRET, links.ENV_PUBLIC_URL,
                strm.ENV_LIBRARY_ROOTS, "MEDIA_ARCHIVIST_JELLYFIN_URL",
                "MEDIA_ARCHIVIST_JELLYFIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv(strm.ENV_LIBRARY_ROOTS, str(library))
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    rows = [
        ("vidAAAAAAAA", "A Short Film", "2024-05-03", "A film about films."),
        ("vidBBBBBBBB", "Second / Film", "2023-01-15", None),
        ("vidCCCCCCCC", "Undated", None, None),
    ]
    for vid, title, published, description in rows:
        row = {"source": "youtube", "url": watch(vid), "videoId": vid, "title": title,
               "author": "Some Channel", "channelId": "UCxxxxxxxxxxxxxxxxxxxxxx",
               "duration": 754, "thumbnail": f"{thumbs}/{vid}.png"}
        if published:
            row["published"] = published
        if description:
            row["description"] = description
        db[watch(vid)] = row
    db["https://x.bandcamp.com/track/y"] = {"source": "bandcamp", "url": "https://x.bandcamp.com/track/y",
                                            "title": "Song", "artist": "Band",
                                            "stream": "https://x/y.mp3"}
    db.store()
    yield {"db": str(path), "library": library, "target": library / "youtube", "tmp": tmp_path}
    indexdb.close_all()


def client(env):
    return TestClient(create_app(env["db"]), base_url="http://archivist.local:8000")


def export(c, **body):
    return c.post("/api/v1/exports", json=body)


def wait_done(c, export_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        rec = c.get(f"/api/v1/exports/{export_id}").json()
        if rec["posters_pending"] == 0 and rec["jellyfin"].get("collection") != "pending":
            return rec
        assert time.monotonic() < deadline, rec
        time.sleep(0.05)


def test_entry_export_writes_the_movies_layout(env):
    target = env["target"]
    with client(env) as c:
        r = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target))
        assert r.status_code == 201, r.text
        body = r.json()
        folder = target / "A Short Film (2024) [vidAAAAAAAA]"
        strm_file = folder / "A Short Film (2024) [vidAAAAAAAA].strm"
        assert body["id"].startswith("exp_")
        assert sorted(body["written"]) == sorted([str(strm_file), str(folder / "movie.nfo")])
        assert body["skipped"] == []
        assert body["posters_pending"] == 1
        assert body["jellyfin"] == {"refresh": "not requested", "collection": "not requested"}
        assert strm_file.read_text() == \
            f"http://archivist.local:8000/api/v1/stream/{eid('vidAAAAAAAA')}\n"
        nfo = ET.parse(folder / "movie.nfo").getroot()
        assert nfo.findtext("title") == "A Short Film"
        assert nfo.findtext("plot") == "A film about films."
        assert nfo.findtext("studio") == "Some Channel"
        assert nfo.findtext("premiered") == "2024-05-03"
        assert {u.get("type"): u.text for u in nfo.findall("uniqueid")}["youtube"] == "vidAAAAAAAA"
        rec = wait_done(c, body["id"])
    assert (folder / "poster.png").read_bytes() == PNG
    assert str(folder / "poster.png") in rec["written"]
    assert rec["scope"] == "entry" and rec["layout"] == "movies" and rec["links"] == "plain"


def test_exporting_again_rewrites_in_place(env):
    target = env["target"]
    with client(env) as c:
        first = export(c, scope="entry", id=eid("vidAAAAAAAA"), target_dir=str(target)).json()
        wait_done(c, first["id"])
        strm_file = Path(next(p for p in first["written"] if p.endswith(".strm")))
        strm_file.write_text("stale\n")
        second = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                        links="signed").json()
        wait_done(c, second["id"])
    assert sorted(second["written"]) == sorted(first["written"])
    assert [p.name for p in target.iterdir()] == ["A Short Film (2024) [vidAAAAAAAA]"]
    assert "?sig=" in strm_file.read_text()


def test_signed_strm_links_verify(env):
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"]),
                      links="signed").json()
        wait_done(c, body["id"])
    text = Path(next(p for p in body["written"] if p.endswith(".strm"))).read_text().strip()
    from urllib.parse import parse_qs, urlparse
    url = urlparse(text)
    q = parse_qs(url.query)
    assert "exp" not in q
    secret = links.secret(indexdb.open_index(env["db"]))
    assert links.verify(secret, url.path.rsplit("/", 1)[1], q["sig"][0], None)


@pytest.mark.parametrize("target", [
    "/etc",
    "{tmp}/library/../outside",
    "{tmp}/library-evil",
    "library/youtube",
    "",
])
def test_target_dir_outside_the_library_roots_is_refused(env, target):
    target = target.format(tmp=env["tmp"])
    with client(env) as c:
        r = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=target)
    assert r.status_code == 400, r.text
    assert not (env["tmp"] / "outside").exists()
    assert not (env["tmp"] / "library-evil").exists()


def test_a_symlink_out_of_the_root_is_refused(env):
    outside = env["tmp"] / "outside"
    outside.mkdir()
    (env["library"] / "escape").symlink_to(outside)
    with client(env) as c:
        r = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["library"] / "escape"))
        assert r.status_code == 400
        r = export(c, scope="entry", id="youtube:vidAAAAAAAA",
                   target_dir=str(env["library"] / "escape" / "deeper"))
        assert r.status_code == 400
    assert list(outside.iterdir()) == []


def test_no_library_roots_refuses_every_export(env, monkeypatch):
    monkeypatch.delenv(strm.ENV_LIBRARY_ROOTS)
    with client(env) as c:
        r = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"]))
    assert r.status_code == 400
    assert not env["target"].exists()


def test_a_symlinked_entry_folder_is_not_written_through(env):
    outside = env["tmp"] / "outside"
    outside.mkdir()
    env["target"].mkdir()
    (env["target"] / "A Short Film (2024) [vidAAAAAAAA]").symlink_to(outside)
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"])).json()
    assert body["written"] == []
    assert body["skipped"][0]["key"] == "youtube:vidAAAAAAAA"
    assert list(outside.iterdir()) == []


def test_a_symlinked_file_is_neither_followed_nor_replaced(env):
    outside = env["tmp"] / "outside.txt"
    outside.write_text("keep")
    folder = env["target"] / "A Short Film (2024) [vidAAAAAAAA]"
    link = folder / "A Short Film (2024) [vidAAAAAAAA].strm"
    with client(env) as c:
        first = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"])).json()
        wait_done(c, first["id"])
        link.unlink()
        link.symlink_to(outside)
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"])).json()
        wait_done(c, body["id"])
    assert str(link) not in body["written"]
    assert body["skipped"][0]["key"] == "youtube:vidAAAAAAAA"
    assert link.is_symlink() and outside.read_text() == "keep"


def test_a_folder_of_another_entry_is_left_alone(env):
    folder = env["target"] / "A Short Film (2024) [vidAAAAAAAA]"
    folder.mkdir(parents=True)
    nfo = ('<movie><title>x</title><uniqueid type="media-archivist">someoneelse</uniqueid></movie>')
    (folder / "movie.nfo").write_text(nfo)
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"])).json()
    assert body["written"] == []
    assert body["skipped"] == [{"key": "youtube:vidAAAAAAAA", "reason": "folder belongs to another entry"}]
    assert (folder / "movie.nfo").read_text() == nfo


def test_episodes_layout(env):
    target = env["target"]
    show = target / "Some Channel"
    with client(env) as c:
        for vid in ("vidAAAAAAAA", "vidBBBBBBBB", "vidCCCCCCCC"):
            body = export(c, scope="entry", id=f"youtube:{vid}", target_dir=str(target),
                          layout="episodes").json()
            assert body["posters_pending"] == 0
            if vid == "vidCCCCCCCC":
                assert body["written"] == []
                assert body["skipped"] == [{"key": "youtube:vidCCCCCCCC", "reason": "upload date unknown"}]
            elif vid == "vidAAAAAAAA":
                base = show / "Season 2024" / "Some Channel - S2024E0503 - A Short Film [vidAAAAAAAA]"
                assert sorted(body["written"]) == sorted(map(str, [
                    show / "tvshow.nfo", show / "Season 2024" / "season.nfo",
                    base.with_suffix(".strm"), base.with_suffix(".nfo")]))
    ep = ET.parse(show / "Season 2024" / "Some Channel - S2024E0503 - A Short Film [vidAAAAAAAA].nfo").getroot()
    assert ep.tag == "episodedetails"
    assert ep.findtext("season") == "2024" and ep.findtext("episode") == "503"
    assert ep.findtext("aired") == "2024-05-03"
    assert {u.get("type"): u.text for u in ep.findall("uniqueid")}["youtube"] == "vidAAAAAAAA"
    assert ET.parse(show / "tvshow.nfo").getroot().findtext("title") == "Some Channel"
    assert ET.parse(show / "Season 2023" / "season.nfo").getroot().findtext("seasonnumber") == "2023"
    assert (show / "Season 2023" / "Some Channel - S2023E0115 - Second _ Film [vidBBBBBBBB].strm").is_file()


def test_episodes_layout_keeps_a_foreign_tvshow_nfo(env):
    show = env["target"] / "Some Channel"
    show.mkdir(parents=True)
    (show / "tvshow.nfo").write_text("<tvshow><title>Mine</title></tvshow>")
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"]),
                      layout="episodes").json()
        assert str(show / "tvshow.nfo") not in body["written"]
        c.delete(f"/api/v1/exports/{body['id']}")
    assert (show / "tvshow.nfo").read_text() == "<tvshow><title>Mine</title></tvshow>"


def test_episodes_layout_leaves_a_file_no_export_wrote_alone(env):
    season = env["target"] / "Some Channel" / "Season 2024"
    season.mkdir(parents=True)
    stem = "Some Channel - S2024E0503 - A Short Film [vidAAAAAAAA]"
    mine = season / f"{stem}.nfo"
    mine.write_text("edited by hand")
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"]),
                      layout="episodes").json()
        assert body["skipped"] == [{"key": "youtube:vidAAAAAAAA",
                                    "reason": f"{mine} exists and no export wrote it"}]
        assert not (season / f"{stem}.strm").exists()
        c.delete(f"/api/v1/exports/{body['id']}")
    assert mine.read_text() == "edited by hand"


def test_episodes_layout_rewrites_the_files_an_export_wrote(env):
    target = env["target"]
    with client(env) as c:
        first = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                       layout="episodes").json()
        strm_file = Path(next(p for p in first["written"] if p.endswith(".strm")))
        second = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                        layout="episodes", links="signed").json()
    assert second["skipped"] == [] and str(strm_file) in second["written"]
    assert "?sig=" in strm_file.read_text()


def test_music_entries_are_not_movies(env):
    music = stable_id(Source.BANDCAMP, "https://x.bandcamp.com/track/y")
    with client(env) as c:
        body = export(c, scope="entry", id=music, target_dir=str(env["target"])).json()
    assert body["written"] == []
    assert body["skipped"][0]["reason"] == "music entries are not exported as movies"


def test_delete_removes_exactly_its_own_files(env):
    target = env["target"]
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target)).json()
        rec = wait_done(c, body["id"])
        folder = target / "A Short Film (2024) [vidAAAAAAAA]"
        (folder / "notes.txt").write_text("mine")
        (target / "unrelated.strm").parent.mkdir(exist_ok=True)
        (target / "unrelated.strm").write_text("x")
        r = c.delete(f"/api/v1/exports/{body['id']}")
        assert r.status_code == 200, r.text
        assert sorted(r.json()["removed"]) == sorted(rec["written"])
        assert r.json()["kept"] == []
        assert c.get(f"/api/v1/exports/{body['id']}").status_code == 404
        assert c.delete(f"/api/v1/exports/{body['id']}").status_code == 404
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]
    assert (target / "unrelated.strm").read_text() == "x"


def test_delete_removes_the_folders_it_made_when_empty(env):
    target = env["target"]
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target)).json()
        wait_done(c, body["id"])
        c.delete(f"/api/v1/exports/{body['id']}")
    assert not target.exists()
    assert list(env["library"].iterdir()) == []


def test_delete_keeps_changed_files_and_files_another_export_holds(env):
    target = env["target"]
    with client(env) as c:
        a = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target)).json()
        wait_done(c, a["id"])
        b = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target)).json()
        wait_done(c, b["id"])
        r = c.delete(f"/api/v1/exports/{a['id']}").json()
        assert r["removed"] == []
        assert {k["reason"] for k in r["kept"]} == {"written by another export"}
        nfo = target / "A Short Film (2024) [vidAAAAAAAA]" / "movie.nfo"
        nfo.write_text("edited by hand")
        r = c.delete(f"/api/v1/exports/{b['id']}").json()
    assert {"path": str(nfo), "reason": "changed since it was written"} in r["kept"]
    assert nfo.read_text() == "edited by hand"
    assert not (target / "A Short Film (2024) [vidAAAAAAAA]" / "A Short Film (2024) [vidAAAAAAAA].strm").exists()


def test_delete_never_follows_a_swapped_in_symlink(env):
    target = env["target"]
    outside = env["tmp"] / "outside"
    outside.mkdir()
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target)).json()
        wait_done(c, body["id"])
        folder = target / "A Short Film (2024) [vidAAAAAAAA]"
        for p in folder.iterdir():
            (outside / p.name).write_bytes(p.read_bytes())
            p.unlink()
        folder.rmdir()
        folder.symlink_to(outside)
        r = c.delete(f"/api/v1/exports/{body['id']}").json()
    assert r["removed"] == []
    assert len(r["kept"]) == 3
    assert len(list(outside.iterdir())) == 3


def test_delete_by_scope_and_folder(env):
    target = env["target"]
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target)).json()
        wait_done(c, body["id"])
        r = c.delete("/api/v1/exports", params={"scope": "entry", "id": "youtube:vidAAAAAAAA",
                                                "target_dir": str(target)})
        assert r.status_code == 200, r.text
        assert r.json()["exports"] == [body["id"]]
        assert c.delete("/api/v1/exports", params={"scope": "entry", "id": "youtube:vidAAAAAAAA",
                                                   "target_dir": str(target)}).status_code == 404
    assert not (target / "A Short Film (2024) [vidAAAAAAAA]").exists()


def _seed_playlist(db, pid, vids, title="Animation"):
    index = indexdb.open_index(db)
    with index.write() as conn:
        conn.execute("INSERT INTO playlists (playlist_id, title) VALUES (?, ?)", (pid, title))
        conn.executemany("INSERT INTO playlist_items (playlist_id, entry_id, position) VALUES (?, ?, ?)",
                         [(pid, eid(v), n) for n, v in enumerate(vids)])


def test_playlist_and_subscription_scopes(env):
    target = env["target"]
    with client(env) as c:
        _seed_playlist(env["db"], "youtube:playlist:PLyyyy", ["vidBBBBBBBB", "vidAAAAAAAA"])
        index = indexdb.open_index(env["db"])
        with index.write() as conn:
            conn.execute("INSERT INTO subscriptions (id, url, kind, created_at) VALUES (?, ?, 'channel', ?)",
                         ("youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx",
                          "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx", "2024-01-01T00:00:00Z"))
            conn.execute("INSERT INTO memberships (subscription_id, entry_id, position, first_seen)"
                         " VALUES (?, ?, 0, ?)", ("youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx",
                                                  eid("vidCCCCCCCC"), "2024-01-01T00:00:00Z"))
            conn.execute("INSERT INTO memberships (subscription_id, entry_id, position, first_seen)"
                         " VALUES (?, ?, 1, ?)", ("youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx",
                                                  "0" * 40, "2024-01-01T00:00:00Z"))
        pl = export(c, scope="playlist", id="youtube:playlist:PLyyyy", target_dir=str(target)).json()
        assert [Path(p).name for p in pl["written"] if p.endswith(".strm")] == [
            "Second _ Film (2023) [vidBBBBBBBB].strm", "A Short Film (2024) [vidAAAAAAAA].strm"]
        sub = export(c, scope="subscription", id="youtube:channel:UCxxxxxxxxxxxxxxxxxxxxxx",
                     target_dir=str(target)).json()
        assert [Path(p).name for p in sub["written"] if p.endswith(".strm")] == ["Undated [vidCCCCCCCC].strm"]
        assert sub["skipped"] == [{"key": "0" * 40, "reason": "not in the database"}]
        assert export(c, scope="playlist", id="youtube:playlist:PLnope",
                      target_dir=str(target)).status_code == 404
        assert export(c, scope="subscription", id="youtube:channel:UCnope",
                      target_dir=str(target)).status_code == 404
        wait_done(c, pl["id"])
        wait_done(c, sub["id"])


def test_bad_requests(env):
    target = str(env["target"])
    with client(env) as c:
        assert export(c, scope="entry", id="youtube:missing", target_dir=target).status_code == 404
        assert export(c, scope="channel", id="x", target_dir=target).status_code == 422
        assert export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=target,
                      layout="music").status_code == 422
        assert export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=target,
                      links="secret").status_code == 422
        assert c.get("/api/v1/exports/exp_nothing").status_code == 404
        meta = c.get("/api/v1/meta").json()
    assert {"exports", "stream", "links"} <= set(meta["features"])
    assert 300 in meta["migrations"]


def test_jellyfin_not_configured_is_reported(env):
    with client(env) as c:
        body = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(env["target"]),
                      refresh_jellyfin=True, collection=True).json()
        rec = wait_done(c, body["id"])
    assert body["jellyfin"]["refresh"] == "not configured"
    assert rec["jellyfin"] == {"refresh": "not configured", "collection": "not configured"}


def test_layout_writers_return_their_paths(env):
    from media_archivist.index import Index

    entry = Index(env["db"]).get(eid("vidAAAAAAAA"))
    root = env["library"]
    first = strm.write_movie(entry, root, "http://x/1\n")
    again = strm.write_movie(entry, root, "http://x/1\n")
    assert first.files == again.files and first.dirs and again.dirs == []
    assert all(os.path.isabs(p) for p in first.files)


def _strm_files(target):
    return sorted(target.rglob("*.strm"))


def test_undoing_two_exports_of_one_entry_newest_first_leaves_nothing_stuck(env):
    target = env["target"]
    with client(env) as c:
        a = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                   layout="episodes").json()
        b = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                   layout="episodes", links="signed").json()
        assert len(_strm_files(target)) == 1
        assert "sig=" in _strm_files(target)[0].read_text()
        c.delete(f"/api/v1/exports/{b['id']}")
        r = c.delete(f"/api/v1/exports/{a['id']}").json()
        assert r["removed"], r
        assert _strm_files(target) == []
        again = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                       layout="episodes").json()
        assert again["skipped"] == [] and len(_strm_files(target)) == 1


def test_undoing_two_exports_oldest_first_removes_the_file_with_the_last(env):
    target = env["target"]
    with client(env) as c:
        a = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                   layout="episodes").json()
        b = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                   layout="episodes", links="signed").json()
        c.delete(f"/api/v1/exports/{a['id']}")
        assert len(_strm_files(target)) == 1
        c.delete(f"/api/v1/exports/{b['id']}")
        assert _strm_files(target) == []


def test_an_orphaned_strm_of_this_server_is_claimed_by_a_new_export(env):
    target = env["target"]
    with client(env) as c:
        first = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                       layout="episodes", base_url="http://other.host:9").json()
        (path,) = _strm_files(target)
        with indexdb.open_index(env["db"]).write() as conn:
            conn.execute("DELETE FROM exports")
        assert path.read_text().startswith("http://other.host:9/api/v1/stream/")
        again = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                       layout="episodes").json()
        assert again["skipped"] == [], again
        assert path.read_text().startswith("http://archivist.local:8000/api/v1/stream/")
        assert first["id"] != again["id"]


def test_a_strm_with_foreign_content_is_still_refused(env):
    target = env["target"]
    with client(env) as c:
        first = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                       layout="episodes").json()
        (path,) = _strm_files(target)
        with indexdb.open_index(env["db"]).write() as conn:
            conn.execute("DELETE FROM exports")
        path.write_text("http://someone.else/api/v1/stream/other-id\n")
        again = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                       layout="episodes").json()
        assert [s["key"] for s in again["skipped"]] == ["youtube:vidAAAAAAAA"]
        assert path.read_text() == "http://someone.else/api/v1/stream/other-id\n"


def test_undoing_the_older_export_first_leaves_no_empty_folders(env):
    target = env["target"]
    with client(env) as c:
        a = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                   layout="episodes").json()
        b = export(c, scope="entry", id="youtube:vidAAAAAAAA", target_dir=str(target),
                   layout="episodes", links="signed").json()
        c.delete(f"/api/v1/exports/{a['id']}")
        assert target.exists()
        c.delete(f"/api/v1/exports/{b['id']}")
    assert not target.exists(), sorted(str(p) for p in target.rglob("*"))
