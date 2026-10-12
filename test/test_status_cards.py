# SPDX-License-Identifier: Apache-2.0
"""A play that cannot be resolved is answered with a short status card video."""
from __future__ import annotations

import threading
import time
import tomllib
import types
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_archivist import indexdb, streams, ytclient
from media_archivist.models.canonical import stable_id
from media_archivist.models.raw import Source
from media_archivist.server import auth
from media_archivist.server.api_v1 import deps, stream
from media_archivist.server.app import create_app
from media_archivist.storage import EnvelopeJsonStorage

KEY = "k" * 32
WATCH = "https://www.youtube.com/watch?v=vidAAAAAAA"
BOT_CHECK = (Path(__file__).parent / "fixtures" / "yt_dlp_bot_check_error.txt").read_text().strip()
REASONS = ("rate-limited", "unavailable", "age-restricted", "could-not-play")


def card_bytes(reason):
    return (resources.files("media_archivist.server.api_v1") / "cards" / f"{reason}.ts").read_bytes()


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, KEY)
    monkeypatch.setenv(auth.ENV_EXEMPT, "")
    monkeypatch.setattr(ytclient, "CLIENT", ytclient.YouTubeClient(sleep=lambda s: None))
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    for n in range(6):
        url = WATCH + str(n)
        db[url] = {"source": "youtube", "url": url, "videoId": f"vidAAAAAAA{n}", "title": "t",
                   "author": "c"}
    db.store()
    with TestClient(create_app(str(path))) as c:
        def link(n=0):
            entry_id = stable_id(Source.YOUTUBE, WATCH + str(n))
            r = c.post(f"/api/v1/entries/{entry_id}/link", headers={"X-Api-Key": KEY})
            return r.json()["url"].split("/api/v1", 1)[1]

        c.link = link
        yield c
    indexdb.close_all()


def failing(monkeypatch, message):
    calls = []

    def resolve(url, **kw):
        calls.append(url)
        raise streams.StreamResolveError(message)

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    return calls


def play(c, headers=None, n=0):
    return c.get("/api/v1" + c.link(n), headers=headers or {}, follow_redirects=False)


def assert_card(r, reason):
    """A card: the whole transport stream, with nothing that gives a length or a range."""
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp2t"
    assert "content-length" not in r.headers
    assert "accept-ranges" not in r.headers
    assert "content-range" not in r.headers
    assert "no-store" in r.headers["cache-control"]
    assert r.content == card_bytes(reason)


def assert_busy(r):
    assert r.status_code == 503
    assert int(r.headers["retry-after"]) > 0
    assert "content-length" not in r.headers or r.headers["content-length"] == "0"
    assert r.content == b""
    assert "video" not in r.headers.get("content-type", "")


def snapshot(c):
    index = deps.get_index(types.SimpleNamespace(app=c.app))
    names = [r["name"] for r in index.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {n: [tuple(r) for r in index.execute(f"SELECT * FROM {n}").fetchall()] for n in names}


# the packaged files


@pytest.mark.parametrize("reason", REASONS)
def test_the_card_files_resolve_as_package_data(reason):
    data = card_bytes(reason)
    assert 50_000 < len(data) < 200_000
    assert len(data) % 188 == 0
    assert all(data[i] == 0x47 for i in range(0, len(data), 188))


def test_there_is_no_busy_card():
    names = {p.name for p in (resources.files("media_archivist.server.api_v1") / "cards").iterdir()}
    assert names == {f"{r}.ts" for r in REASONS}


def test_the_cards_are_declared_as_package_data():
    root = Path(__file__).parent.parent
    patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"][
        "media_archivist"]
    declared = {p for pattern in patterns for p in (root / "media_archivist").glob(pattern)}
    for reason in REASONS:
        assert root / "media_archivist/server/api_v1/cards" / f"{reason}.ts" in declared


# each failure class gets its card


def test_a_paused_details_kind_plays_the_rate_limited_card(served, monkeypatch):
    calls = failing(monkeypatch, "never")
    ytclient.CLIENT.backoff.mark_blocked("details")
    assert_card(play(served), "rate-limited")
    assert calls == []


@pytest.mark.parametrize("message", [BOT_CHECK, "ERROR: HTTP Error 429: Too Many Requests",
                                     "ERROR: unable to download: HTTP Error 403: Forbidden"])
def test_a_bot_check_or_429_or_403_plays_the_rate_limited_card(served, monkeypatch, message):
    failing(monkeypatch, message)
    assert_card(play(served), "rate-limited")


@pytest.mark.parametrize("message", ["ERROR: Video unavailable. This video is private",
                                     "ERROR: This video has been removed by the uploader",
                                     "ERROR: Join this channel to get access to members-only content",
                                     "ERROR: The uploader has not made this video available in your country"])
def test_a_permanent_failure_plays_the_unavailable_card(served, monkeypatch, message):
    failing(monkeypatch, message)
    assert_card(play(served), "unavailable")


def test_an_age_gate_plays_the_age_restricted_card(served, monkeypatch):
    failing(monkeypatch, "ERROR: Sign in to confirm your age. This video may be inappropriate")
    assert_card(play(served), "age-restricted")


def test_a_resolver_timeout_is_a_503_and_no_card(served, monkeypatch):
    failing(monkeypatch, "yt-dlp binary timed out resolving https://www.youtube.com/watch?v=x")
    assert_busy(play(served))


@pytest.mark.parametrize("message", ["ERROR: Premieres in 3 hours",
                                     "ERROR: Requested format is not available",
                                     "ERROR: unable to extract uploader id",
                                     "ERROR: [Errno -2] Name or service not known"])
def test_any_other_failure_plays_the_could_not_play_card(served, monkeypatch, message):
    failing(monkeypatch, message)
    assert_card(play(served), "could-not-play")


def test_no_free_slot_within_the_wait_is_a_503_and_no_card(served, monkeypatch):
    monkeypatch.setattr(stream, "PLAY_WAIT_S", 0.4)
    release = threading.Event()
    inside = threading.Semaphore(0)

    def resolve(url, **kw):
        inside.release()
        release.wait(10)
        return types.SimpleNamespace(url="https://cdn.example/" + url[-11:])

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    threads = [threading.Thread(target=lambda n=n: play(served, n=n))
               for n in range(stream.PLAY_CONCURRENCY)]
    for t in threads:
        t.start()
    try:
        for _ in threads:
            assert inside.acquire(timeout=5)
        assert_busy(play(served, n=5))
    finally:
        release.set()
        for t in threads:
            t.join(10)


def test_a_play_beyond_the_waiting_cap_is_a_503_at_once(served, monkeypatch):
    monkeypatch.setattr(stream, "PLAY_WAIT_S", 5.0)
    monkeypatch.setattr(stream, "PLAY_MAX_WAITING", 1)
    release = threading.Event()
    inside = threading.Semaphore(0)

    def resolve(url, **kw):
        inside.release()
        release.wait(10)
        return types.SimpleNamespace(url="https://cdn.example/" + url[-11:])

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    threads = [threading.Thread(target=lambda n=n: play(served, n=n))
               for n in range(stream.PLAY_CONCURRENCY + 1)]
    try:
        for t in threads[:stream.PLAY_CONCURRENCY]:
            t.start()
        for _ in range(stream.PLAY_CONCURRENCY):
            assert inside.acquire(timeout=5)
        threads[-1].start()
        deadline = time.monotonic() + 5
        while stream._WAITING < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        assert_busy(play(served, n=5))
        assert time.monotonic() - started < 1.5
    finally:
        release.set()
        for t in threads:
            t.join(10)


def test_a_paused_details_kind_plays_the_rate_limited_card_for_youtube_music_too(monkeypatch):
    from media_archivist.models.canonical import MediaEntry

    monkeypatch.setattr(ytclient, "CLIENT", ytclient.YouTubeClient(sleep=lambda s: None))
    calls = failing(monkeypatch, "never")
    entry = MediaEntry(id="m1", source=Source.YOUTUBE_MUSIC,
                       url="https://music.youtube.com/watch?v=aaaaaaaaaaa", title="t")
    ytclient.CLIENT.backoff.mark_blocked("details")
    assert stream._play(None, entry) == (None, "rate-limited")
    assert calls == []


def test_a_waiting_play_gets_the_card_of_the_resolution_it_waited_for(served, monkeypatch):
    release = threading.Event()
    inside = threading.Event()
    calls = []

    def resolve(url, **kw):
        calls.append(url)
        inside.set()
        release.wait(10)
        raise streams.StreamResolveError("ERROR: Video unavailable. This video is private")

    monkeypatch.setattr(streams, "resolve_stream", resolve)
    results = []
    first = threading.Thread(target=lambda: results.append(play(served)))
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=lambda: results.append(play(served)))
    second.start()
    deadline = time.monotonic() + 5
    while stream._WAITING < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stream._WAITING == 1
    release.set()
    first.join(10)
    second.join(10)
    assert len(results) == 2 and len(calls) == 1
    for r in results:
        assert_card(r, "unavailable")


def test_a_card_is_sent_chunked_by_a_real_server(served, monkeypatch):
    import http.client
    import socket

    import uvicorn

    failing(monkeypatch, "ERROR: Video unavailable")
    path = served.link()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(served.app, host="127.0.0.1", port=port,
                                           log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        for headers in ({}, {"Range": "bytes=0-"}, {"Range": "bytes=1000-"}):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/api/v1" + path, headers=headers)
            r = conn.getresponse()
            body = r.read()
            assert r.status == 200
            assert r.getheader("Transfer-Encoding") == "chunked"
            assert r.getheader("Content-Length") is None
            assert r.getheader("Accept-Ranges") is None
            assert r.getheader("Content-Type") == "video/mp2t"
            assert body == card_bytes("unavailable")
            conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("HEAD", "/api/v1" + path)
        r = conn.getresponse()
        r.read()
        assert r.status == 503 and r.getheader("Retry-After")
        conn.close()
    finally:
        server.should_exit = True
        thread.join(10)


# play or probe


@pytest.mark.parametrize("range_header", ["bytes=0-", "bytes=0-99", "bytes=100-", "bytes=-500",
                                          "bytes=0-1,5-9", "items=0-", "garbage"])
def test_a_range_is_ignored_for_a_card(served, monkeypatch, range_header):
    failing(monkeypatch, "ERROR: Video unavailable")
    assert_card(play(served, {"Range": range_header}), "unavailable")


def test_head_during_a_failure_is_503_with_retry_after(served, monkeypatch):
    failing(monkeypatch, BOT_CHECK)
    assert_busy(served.head("/api/v1" + served.link(), follow_redirects=False))


def test_head_of_a_resolved_play_is_still_a_redirect(served, monkeypatch):
    monkeypatch.setattr(streams, "resolve_stream",
                        lambda url, **kw: types.SimpleNamespace(url="https://cdn.example/v"))
    r = served.head("/api/v1" + served.link(), follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://cdn.example/v"


# switching off, signatures, caching, records


def test_status_cards_off_restores_the_watch_page_redirect(served, monkeypatch):
    failing(monkeypatch, "ERROR: Video unavailable")
    r = served.patch("/api/v1/settings", json={"status_cards": "off"}, headers={"X-Api-Key": KEY})
    assert r.status_code == 200 and r.json()["status_cards"] == "off"
    path = served.link()
    for method in (served.get, served.head):
        r = method("/api/v1" + path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == WATCH + "0"
    failing(monkeypatch, "yt-dlp binary timed out resolving x")
    assert_busy(play(served, n=1))
    served.patch("/api/v1/settings", json={"status_cards": "on"}, headers={"X-Api-Key": KEY})
    failing(monkeypatch, "ERROR: Video unavailable")
    assert_card(play(served, n=2), "unavailable")


def test_status_cards_setting_defaults_on_and_rejects_other_values(served):
    assert served.get("/api/v1/settings", headers={"X-Api-Key": KEY}).json()["status_cards"] == "on"
    r = served.patch("/api/v1/settings", json={"status_cards": "probe-safe"}, headers={"X-Api-Key": KEY})
    assert r.status_code == 400


def test_a_bad_signature_is_401_and_never_a_card(served, monkeypatch):
    calls = failing(monkeypatch, "ERROR: Video unavailable")
    path = served.link()
    bad = path[:-4] + ("0000" if not path.endswith("0000") else "1111")
    for method in (served.get, served.head):
        r = method("/api/v1" + bad, follow_redirects=False)
        assert r.status_code == 401
        assert "video" not in r.headers.get("content-type", "")
    r = served.get("/api/v1" + path.split("?")[0], follow_redirects=False)
    assert r.status_code == 401
    assert calls == []


def test_a_failure_is_not_cached_and_the_next_play_resolves_again(served, monkeypatch):
    calls = failing(monkeypatch, BOT_CHECK)
    assert_card(play(served), "rate-limited")
    ytclient.CLIENT.backoff.mark_clear("details")
    monkeypatch.setattr(streams, "resolve_stream",
                        lambda url, **kw: types.SimpleNamespace(url="https://cdn.example/v"))
    r = play(served)
    assert r.status_code == 302 and r.headers["location"] == "https://cdn.example/v"
    assert len(calls) == 1


def test_a_card_writes_no_rows_and_marks_no_entry(served, monkeypatch):
    failing(monkeypatch, "ERROR: Video unavailable")
    path = served.link()
    before = snapshot(served)
    assert before["settings"]
    assert_card(served.get("/api/v1" + path, follow_redirects=False), "unavailable")
    assert snapshot(served) == before


def test_meta_names_the_card_reasons(served):
    meta = served.get("/api/v1/meta", headers={"X-Api-Key": KEY}).json()
    assert "status_cards" in meta["features"]
    assert meta["status_cards"] == list(REASONS)
