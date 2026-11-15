# SPDX-License-Identifier: Apache-2.0
"""The app API end to end, against a fake YouTube.

One server on a temporary database: an app subscribes to a channel, the
indexer lists it through tutubo (a fake ``tutubo.channel``) and the YouTube
client, the app queries the entries by length and title words, exports one
to a library folder, plays it through a signed link, downloads it with
yt-dlp (a fake ``yt-dlp`` program), and after a restart finds the
download pause and the queue where it left them. Nothing leaves the
machine: connections to anything but the loopback are refused.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from media_archivist import downloads, indexdb, links, strm, streams
from media_archivist.server import auth
from media_archivist.server.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "0123456789abcdef0123456789abcdef"
OUTSIDE = ("203.0.113.7", 50000)
APP = {"X-Api-Key": KEY, "X-Archivist-App": "shortarr"}
UC = "UC" + "e" * 22
SID = f"youtube:channel:{UC}"
CHANNEL_URL = f"https://www.youtube.com/channel/{UC}"

VIDEOS = [
    ("bunny000001", "Big Buck Bunny", 596),
    ("clip0000002", "A short clip", 30),
    ("replay00003", "Live stream replay", 3600),
    ("sintel00004", "Sintel trailer", 52),
    ("tears000005", "Tears of Steel trailer", 734),
    ("spring00006", "Spring", 464),
]


class _Video:
    def __init__(self, video_id, title, seconds):
        self.video_id, self.title, self.duration_seconds = video_id, title, seconds


class FakeChannel:
    """``tutubo.channel.Channel`` for one channel; records the sessions it gets."""

    sessions = []

    def __init__(self, url, language=None, session=None, family=None):
        FakeChannel.sessions.append(session)
        self.channel_id = UC
        self.channel_name = "Blender Studio"
        self.thumbnail_url = "https://yt3.example/blender.jpg"
        self.videos = [_Video(*v) for v in VIDEOS]
        self.playlist_urls = []


@pytest.fixture
def world(tmp_path, monkeypatch):
    real_connect = socket.socket.connect

    def loopback_only(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") \
                and sock.family in (socket.AF_INET, socket.AF_INET6):
            raise ConnectionRefusedError(f"the test refused a connection to {host}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)

    import tutubo.channel

    FakeChannel.sessions = []
    monkeypatch.setattr(tutubo.channel, "Channel", FakeChannel)
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    monkeypatch.setattr(streams, "ffmpeg_available", lambda: True)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "yt-dlp"
    exe.write_text(f"#!{sys.executable}\n" + (FIXTURES / "fake_yt_dlp.py").read_text())
    exe.chmod(0o755)
    control = tmp_path / "fake" / "control.json"
    control.parent.mkdir()
    titles = {vid: title for vid, title, _ in VIDEOS}

    def answer(**modes):
        control.write_text(json.dumps({"mode": "ok", "modes": modes, "titles": titles}))

    answer()
    log = tmp_path / "fake" / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_YTDLP_CONTROL", str(control))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(log))
    monkeypatch.setenv("FAKE_YTDLP_FIXTURES", str(FIXTURES))

    library = tmp_path / "library"
    media = tmp_path / "downloads"
    library.mkdir()
    media.mkdir()
    for var in (auth.ENV_EXEMPT, links.ENV_SECRET, "MEDIA_ARCHIVIST_JELLYFIN_URL",
                "MEDIA_ARCHIVIST_JELLYFIN_API_KEY", "MEDIA_ARCHIVIST_SYNC_INTERVAL_HOURS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    monkeypatch.setenv(links.ENV_PUBLIC_URL, "http://archivist.test")
    monkeypatch.setenv(strm.ENV_LIBRARY_ROOTS, str(library))
    monkeypatch.setenv(downloads.ENV_ROOTS, str(media))
    monkeypatch.setenv("MEDIA_ARCHIVIST_REENRICH", "0")

    def calls():
        return [json.loads(line)["argv"] for line in log.read_text().splitlines()] \
            if log.exists() else []

    return {"db": str(tmp_path / "db.json"), "library": library, "media": media,
            "answer": answer, "calls": calls}


def server(db):
    return TestClient(create_app(db), client=OUTSIDE, follow_redirects=False)


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("condition not met in time")


def tasks_idle(c):
    body = c.get("/tasks", headers=APP).json()
    return body["queued"] == 0 and body["running"] == 0


def download_in(c, download_id, *statuses):
    record = c.get(f"/api/v1/downloads/{download_id}", headers=APP).json()
    return record if record["status"] in statuses else None


def test_subscribe_index_query_export_play_download_and_restart(world):
    db = world["db"]
    with server(db) as c:
        assert c.get("/api/v1/subscriptions").status_code == 401
        r = c.patch("/api/v1/settings", json={"youtube_request_spacing": 0}, headers=APP)
        assert r.status_code == 200

        r = c.post("/api/v1/subscriptions", headers=APP, json={
            "url": CHANNEL_URL, "label": "Blender", "index_playlists": False,
            "export": {"automatic": True, "target_dir": str(world["library"] / "auto"),
                       "layout": "movies"}})
        assert r.status_code == 201, r.text
        assert r.json()["id"] == SID and r.json()["holders"] == ["shortarr"]
        wait_for(lambda: tasks_idle(c))
        sub = c.get(f"/api/v1/subscriptions/{SID}", headers=APP).json()
        assert sub["entry_count"] == len(VIDEOS) and sub["title"] == "Blender Studio"
        assert FakeChannel.sessions and all(s is not None for s in FakeChannel.sessions)

        # The automatic export wrote every newly indexed video into the library.
        auto = sorted(p.name for p in (world["library"] / "auto").rglob("*.strm"))
        assert len(auto) == len(VIDEOS)

        r = c.get("/api/v1/entries", headers=APP, params={
            "subscription": SID, "min_duration": 60, "max_duration": 1200, "exclude": "trailer"})
        assert r.status_code == 200, r.text
        page = r.json()
        assert sorted(e["title"] for e in page["entries"]) == ["Big Buck Bunny", "Spring"]
        bunny = next(e for e in page["entries"] if e["title"] == "Big Buck Bunny")

        r = c.post("/api/v1/exports", headers=APP, json={
            "scope": "entry", "id": bunny["id"], "target_dir": str(world["library"] / "movies"),
            "layout": "movies", "links": "signed"})
        assert r.status_code == 201, r.text
        export = r.json()
        written = list((world["library"] / "movies").rglob("*.strm"))
        assert len(written) == 1
        played = urlsplit(written[0].read_text().strip())
        assert played.netloc == "archivist.test" and "sig=" in played.query
        assert played.path == f"/api/v1/stream/{bunny['id']}"

        r = c.post(f"/api/v1/entries/{bunny['id']}/link", headers=APP, json={"expires_in": 3600})
        assert r.status_code == 200, r.text
        link = urlsplit(r.json()["url"])
        r = c.get(f"{link.path}?{link.query}")
        assert r.status_code == 302
        assert r.headers["location"] == "https://media.example/videoplayback/bunny000001.mp4"
        assert c.get(link.path).status_code == 401
        resolved = [a for a in world["calls"]() if "-g" in a]
        assert resolved and "--force-ipv4" in resolved[-1]

        r = c.post("/api/v1/downloads", headers=APP, json={
            "entry": bunny["id"], "target_dir": str(world["media"] / "bunny")})
        assert r.status_code == 201, r.text
        done = wait_for(lambda: download_in(c, r.json()["id"], "completed", "failed"))
        assert done["status"] == "completed", done
        assert [Path(f).name for f in done["files"]] == ["Big Buck Bunny [bunny000001].mp4"]
        assert Path(done["files"][0]).read_bytes() == b"video bytes"

        # YouTube answers the next download with its bot check: the download
        # goes back to the queue and downloads pause.
        world["answer"](spring00006="bot")
        spring = next(e for e in page["entries"] if e["title"] == "Spring")
        r = c.post("/api/v1/downloads", headers=APP, json={
            "entry": spring["id"], "target_dir": str(world["media"] / "spring")})
        held = r.json()["id"]
        wait_for(lambda: c.get("/api/v1/youtube/backoff", headers=APP).json()
                 ["kinds"]["download"]["paused"])
        record = wait_for(lambda: download_in(c, held, "queued"))
        assert record["waiting_for"]["reason"]

    index_file = indexdb.index_path(db)
    con = sqlite3.connect(f"file:{index_file}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT kind, channel_id, export_automatic, export_target FROM subscriptions"
                          " WHERE id = ?", (SID,)).fetchone()
        assert tuple(row) == ("channel", UC, 1, str(world["library"] / "auto"))
        assert con.execute("SELECT COUNT(*) FROM memberships WHERE subscription_id = ?",
                           (SID,)).fetchone()[0] == len(VIDEOS)
        durations = dict(con.execute("SELECT key, duration FROM entries_meta").fetchall())
        assert durations["youtube:bunny000001"] == 596
        assert len(durations) == len(VIDEOS)
        kinds = sorted((r["scope_kind"], r["automatic"]) for r in con.execute("SELECT * FROM exports"))
        assert kinds == [("entry", 0), ("subscription", 1)]
        assert export["id"] in [r[0] for r in con.execute("SELECT id FROM exports")]
        statuses = dict(con.execute("SELECT entry_id, status FROM downloads").fetchall())
        assert statuses == {bunny["id"]: "completed", spring["id"]: "queued"}
        assert con.execute("SELECT kind FROM backoff").fetchall()[0][0] == "download"
        assert con.execute("SELECT value FROM settings WHERE key = 'youtube_request_spacing'"
                           ).fetchone()[0] == "0"
    finally:
        con.close()

    runs = len(world["calls"]())
    world["answer"]()
    with server(db) as c:
        pause = c.get("/api/v1/youtube/backoff", headers=APP).json()["kinds"]["download"]
        assert pause["paused"] and pause["wait"] == 1800
        record = c.get(f"/api/v1/downloads/{held}", headers=APP).json()
        assert record["status"] == "queued" and record["waiting_for"]["reason"]
        time.sleep(0.5)
        assert len(world["calls"]()) == runs

        assert c.delete("/api/v1/youtube/backoff/download", headers=APP).status_code == 200
        done = wait_for(lambda: download_in(c, held, "completed", "failed"), timeout=10)
        assert done["status"] == "completed", done
        assert [Path(f).name for f in done["files"]] == ["Spring [spring00006].mp4"]
