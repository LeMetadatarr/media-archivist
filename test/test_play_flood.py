# SPDX-License-Identifier: Apache-2.0
"""A flood of plays on one signed link must not make the rest of the API slow."""
from __future__ import annotations

import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from media_archivist import indexdb, streams, ytclient
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.server import auth
from media_archivist.server.api_v1 import stream
from media_archivist.server.app import create_app
from media_archivist.storage import EnvelopeJsonStorage

KEY = "k" * 32
WATCH = "https://www.youtube.com/watch?v=vidAAAAAAAA"


VIDEOS = ["vidAAAAAAAA"] + [f"vid{n:08d}" for n in range(1, 7)]
MANY = [f"many{n:07d}" for n in range(120)]


def watch(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    monkeypatch.setenv(auth.ENV_EXEMPT, "")
    monkeypatch.setattr(ytclient, "CLIENT", ytclient.YouTubeClient())
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    for video_id in VIDEOS + MANY:
        db[watch(video_id)] = {"source": "youtube", "url": watch(video_id), "videoId": video_id,
                               "title": "t", "author": "c"}
    db.store()
    with TestClient(create_app(str(path))) as c:
        def link(video_id):
            entry_id = stable_id(Source.YOUTUBE, watch(video_id))
            url = c.post(f"/api/v1/entries/{entry_id}/link", headers={"X-Api-Key": KEY}).json()["url"]
            return url.split("/api/v1", 1)[1]

        yield c, link
    indexdb.close_all()


def _play_all(c, paths):
    results = []

    def play(path):
        started = time.monotonic()
        r = c.get("/api/v1" + path, follow_redirects=False)
        results.append((r.status_code, r.headers.get("location"), time.monotonic() - started))

    threads = [threading.Thread(target=play, args=(p,)) for p in paths]
    for t in threads:
        t.start()
    return threads, results


def test_a_flood_of_plays_on_one_link_makes_one_resolution_and_leaves_routes_fast(served, monkeypatch):
    c, link = served
    resolved = []

    def resolve(url, **kw):
        resolved.append(url)
        time.sleep(0.3)
        return types.SimpleNamespace(url="https://cdn.example/v")

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    threads, results = _play_all(c, [link("vidAAAAAAAA")] * 70)
    time.sleep(0.1)
    started = time.monotonic()
    assert c.get("/api/v1/meta", headers={"X-Api-Key": KEY}).status_code == 200
    latency = time.monotonic() - started
    for t in threads:
        t.join(30)
    assert len(results) == 70
    assert all(code == 302 and loc in ("https://cdn.example/v", WATCH) for code, loc, _ in results)
    assert any(loc == "https://cdn.example/v" for _, loc, _ in results)
    assert len(resolved) == 1
    assert latency < 1.5, latency
    again = c.get("/api/v1" + link("vidAAAAAAAA"), follow_redirects=False)
    assert again.headers["location"] == "https://cdn.example/v" and len(resolved) == 1


def test_plays_of_other_entries_wait_for_a_slot_only_up_to_the_bound(served, monkeypatch):
    c, link = served
    monkeypatch.setattr(stream, "PLAY_WAIT_S", 0.4)
    release = threading.Event()
    inside = threading.Semaphore(0)

    def resolve(url, **kw):
        inside.release()
        release.wait(10)
        return types.SimpleNamespace(url="https://cdn.example/" + url[-11:])

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    held = VIDEOS[: stream.PLAY_CONCURRENCY]
    threads, results = _play_all(c, [link(v) for v in held])
    try:
        for _ in held:
            assert inside.acquire(timeout=5)
        started = time.monotonic()
        r = c.get("/api/v1" + link(VIDEOS[5]), follow_redirects=False)
        took = time.monotonic() - started
        assert r.status_code == 302 and r.headers["location"] == watch(VIDEOS[5])
        assert 0.3 < took < 1.5, took
        assert not inside.acquire(timeout=0.2)
    finally:
        release.set()
        for t in threads:
            t.join(10)
    assert sorted(loc for _, loc, _ in results) == sorted("https://cdn.example/" + v for v in held)
    r = c.get("/api/v1" + link(VIDEOS[5]), follow_redirects=False)
    assert r.headers["location"] == "https://cdn.example/" + VIDEOS[5]


def test_a_play_resolves_while_background_requests_hold_the_spacing(served, monkeypatch):
    c, link = served
    monkeypatch.setattr(streams, "resolve_stream",
                        lambda url, **kw: types.SimpleNamespace(url="https://cdn.example/v"))
    ytclient.CLIENT.call("listing", lambda: None)
    r = c.get("/api/v1" + link("vidAAAAAAAA"), follow_redirects=False)
    assert r.headers["location"] == "https://cdn.example/v"


def test_many_plays_of_different_entries_leave_other_routes_fast(served, monkeypatch):
    c, link = served
    release = threading.Event()
    resolved = []

    def resolve(url, **kw):
        resolved.append(url)
        release.wait(10)
        return types.SimpleNamespace(url="https://cdn.example/v")

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    paths = [link(v) for v in MANY]
    threads, results = _play_all(c, paths)
    try:
        time.sleep(0.5)
        started = time.monotonic()
        assert c.get("/api/v1/meta", headers={"X-Api-Key": KEY}).status_code == 200
        latency = time.monotonic() - started
        assert latency < 1.5, latency
        assert len(resolved) <= stream.PLAY_CONCURRENCY
    finally:
        release.set()
        for t in threads:
            t.join(30)
    assert len(results) == len(MANY) and all(code == 302 for code, _, _ in results)


def test_a_youtube_music_play_is_gated_like_a_youtube_play(tmp_path, monkeypatch):
    from media_archivist.models.canonical import MediaEntry

    monkeypatch.setattr(ytclient, "CLIENT", ytclient.YouTubeClient())
    calls = []

    def resolver(url, **kw):
        calls.append(url)
        return types.SimpleNamespace(url="https://cdn.example/m")

    monkeypatch.setattr(streams, "resolve_stream", resolver)
    entry = MediaEntry(id="m1", source=Source.YOUTUBE_MUSIC,
                       url="https://music.youtube.com/watch?v=aaaaaaaaaaa", title="t")
    ytclient.CLIENT.backoff.mark_blocked("details")
    assert stream._resolve(None, entry) is None and calls == []
    ytclient.CLIENT.backoff.mark_clear("details")

    def bot(url, **kw):
        raise streams.StreamResolveError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(streams, "resolve_stream", bot)
    assert stream._resolve(None, entry) is None
    assert ytclient.CLIENT.backoff.blocked_until("details") is not None
