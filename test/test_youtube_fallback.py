# SPDX-License-Identifier: Apache-2.0
"""Playlist / channel indexing when tutubo lists nothing.

tutubo parses YouTube's page markup and returns an empty listing, without
raising, when that markup is outdated. The archivist retries through
``yt-dlp --flat-playlist -J`` and the server task reports an error when a
source lists no videos at all. tutubo and yt-dlp are mocked; the yt-dlp
payload is a recorded ``--flat-playlist -J`` response.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

import media_archivist.youtube as youtube_mod
from media_archivist import streams
from media_archivist.streams import StreamResolveError
from media_archivist.youtube import YoutubeArchivist

FIXTURE = Path(__file__).parent / "fixtures" / "yt_dlp_flat_playlist.json"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLrEnWoR732-BHrPp_Pm8_VleD68f9s14-"


@pytest.fixture
def flat_json():
    return json.loads(FIXTURE.read_text())


class _EmptyTutubo:
    """tutubo Playlist / Channel reading an outdated page: no videos."""

    title = None

    def __init__(self, url):
        self.videos = iter(())


@pytest.fixture
def empty_tutubo(monkeypatch):
    monkeypatch.setattr(youtube_mod, "Playlist", _EmptyTutubo)
    monkeypatch.setattr(youtube_mod, "Channel", _EmptyTutubo)


def test_empty_playlist_falls_back_to_ytdlp(tmp_path, empty_tutubo, flat_json, monkeypatch):
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: flat_json)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive(PLAYLIST_URL)

    rows = {r["videoId"]: r for r in archivist.db.values()}
    assert set(rows) == {"ftaXMKV3ffE", "lO38tZoA0l8", "aSZt3WC1vB4"}
    row = rows["lO38tZoA0l8"]
    assert row["url"] == "https://www.youtube.com/watch?v=lO38tZoA0l8"
    assert row["duration"] == 1262
    assert row["playlist"] == "Popular Right Now"
    assert row["author"]
    assert archivist.entries_seen == 3


def test_empty_channel_falls_back_to_ytdlp(tmp_path, empty_tutubo, flat_json, monkeypatch):
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: flat_json)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@somechannel")

    assert len(archivist.db) == 3


def test_tutubo_exception_falls_back_to_ytdlp(tmp_path, flat_json, monkeypatch):
    class _Boom:
        def __init__(self, url):
            pass

        @property
        def videos(self):
            raise KeyError("contents")

    monkeypatch.setattr(youtube_mod, "Playlist", _Boom)
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: flat_json)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive(PLAYLIST_URL)

    assert len(archivist.db) == 3


def test_both_sources_empty_indexes_nothing(tmp_path, empty_tutubo, monkeypatch):
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: {"entries": []})
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive(PLAYLIST_URL)

    assert len(archivist.db) == 0
    assert archivist.entries_seen == 0


def test_ytdlp_failure_is_not_fatal_to_archive(tmp_path, empty_tutubo, monkeypatch):
    def _fail(url, **kw):
        raise StreamResolveError("boom")

    monkeypatch.setattr(streams, "list_playlist", _fail)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive(PLAYLIST_URL)

    assert archivist.entries_seen == 0


def test_tutubo_result_is_preferred_over_ytdlp(tmp_path, monkeypatch):
    class _Video:
        video_id = "abcdefghijk"
        watch_url = "https://www.youtube.com/watch?v=abcdefghijk"
        title = "From tutubo"
        thumbnail_url = ""

    class _Playlist:
        title = "pl"

        def __init__(self, url):
            self.videos = iter([_Video()])

    def _never(url, **kw):
        raise AssertionError("yt-dlp must not be consulted when tutubo lists videos")

    monkeypatch.setattr(youtube_mod, "Playlist", _Playlist)
    monkeypatch.setattr(streams, "list_playlist", _never)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive(PLAYLIST_URL)

    assert [r["title"] for r in archivist.db.values()] == ["From tutubo"]


def test_list_playlist_uses_binary_when_module_missing(flat_json, monkeypatch):
    calls = []

    def _run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(flat_json), stderr="")

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    monkeypatch.setattr(streams.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(streams.subprocess, "run", _run)

    info = streams.list_playlist(PLAYLIST_URL)

    assert calls[0][:3] == ["yt-dlp", "--flat-playlist", "-J"]
    assert len(info["entries"]) == 3


def test_list_playlist_rejects_non_http():
    with pytest.raises(StreamResolveError):
        streams.list_playlist("file:///etc/passwd")


# --- server task reporting -------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "db.json"))
    with TestClient(app) as c:
        yield c


def _wait_terminal(client, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/tasks/{task_id}").json()
        if task["status"] in ("ok", "error"):
            return task
        time.sleep(0.02)
    pytest.fail("task never finished")


def test_task_for_empty_playlist_is_error_not_ok(client, empty_tutubo, monkeypatch):
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: {"entries": []})

    r = client.post("/archive", json={"url": PLAYLIST_URL})
    final = _wait_terminal(client, r.json()["id"])

    assert final["status"] == "error", final
    assert final["rows_added"] == 0
    assert "listed no videos" in final["error"]


def test_task_for_playlist_uses_fallback_and_reports_rows(client, empty_tutubo, flat_json, monkeypatch):
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: flat_json)

    r = client.post("/archive", json={"url": PLAYLIST_URL})
    final = _wait_terminal(client, r.json()["id"])

    assert final["status"] == "ok", final
    assert final["rows_added"] == 3


def test_task_with_already_indexed_playlist_stays_ok(client, empty_tutubo, flat_json, monkeypatch):
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: flat_json)
    for _ in range(2):
        r = client.post("/archive", json={"url": PLAYLIST_URL})
        final = _wait_terminal(client, r.json()["id"])

    assert final["status"] == "ok", final
    assert final["rows_added"] == 0


# --- channel URLs ------------------------------------------------------

CHANNEL_FIXTURE = Path(__file__).parent / "fixtures" / "yt_dlp_flat_channel.json"
CHANNEL_URL = "https://www.youtube.com/@NASA"
CHANNEL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"
VIDEOS_TAB_IDS = {"ErAqN6gXqZQ", "IwZVXmQdX1E", "90Kgw_SvK4w"}


@pytest.fixture
def channel_json():
    """Recorded ``yt-dlp --flat-playlist -J https://www.youtube.com/@NASA``, trimmed to three entries per tab."""
    return json.loads(CHANNEL_FIXTURE.read_text())


def _serve_channel(monkeypatch, channel_json, requested):
    """Answer like yt-dlp: the bare channel lists its tabs, ``/videos`` lists videos."""
    videos_tab = channel_json["entries"][0]

    def _list(url, **kw):
        requested.append(url)
        if url.rstrip("/").endswith("/videos"):
            return videos_tab
        return channel_json

    monkeypatch.setattr(streams, "list_playlist", _list)


def test_channel_fallback_lists_the_videos_tab(tmp_path, empty_tutubo, channel_json, monkeypatch):
    requested = []
    _serve_channel(monkeypatch, channel_json, requested)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive(CHANNEL_URL)

    assert requested == [CHANNEL_URL + "/videos"]
    assert {r["videoId"] for r in archivist.db.values()} == VIDEOS_TAB_IDS
    assert archivist.entries_seen == 3


def test_bare_channel_listing_never_yields_a_channel_id_row(tmp_path, empty_tutubo, channel_json, monkeypatch):
    # Even when yt-dlp returns the tab structure for the URL asked, no tab
    # becomes a row.
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: channel_json)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@NASA/featured")

    ids = {r["videoId"] for r in archivist.db.values()}
    assert CHANNEL_ID not in ids
    assert all(len(i) == 11 and i != CHANNEL_ID for i in ids)
    assert len(ids) == 9


def test_tab_entries_of_type_url_are_followed(tmp_path, empty_tutubo, channel_json, monkeypatch):
    tabs = [
        {"_type": "url", "ie_key": "YoutubeTab", "id": CHANNEL_ID,
         "url": f"https://www.youtube.com/@NASA/{name}", "title": t["title"]}
        for name, t in zip(("videos", "live", "shorts"), channel_json["entries"])
    ]
    listing = dict(channel_json, entries=tabs)
    by_tab = {t["title"]: t for t in channel_json["entries"]}
    requested = []

    def _list(url, **kw):
        requested.append(url)
        if url.endswith("/featured"):
            return listing
        name = url.rsplit("/", 1)[-1]
        return by_tab[f"NASA - {name.capitalize()}"]

    monkeypatch.setattr(streams, "list_playlist", _list)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@NASA/featured")

    ids = {r["videoId"] for r in archivist.db.values()}
    assert CHANNEL_ID not in ids
    assert len(ids) == 9
    assert VIDEOS_TAB_IDS <= ids


def test_channel_with_only_tabs_and_no_videos_indexes_nothing(tmp_path, empty_tutubo, monkeypatch):
    tab = {"_type": "url", "ie_key": "YoutubeTab", "id": CHANNEL_ID, "title": "Videos",
           "url": "https://www.youtube.com/@x/videos"}
    monkeypatch.setattr(streams, "list_playlist",
                        lambda url, **kw: {"entries": [tab]} if url.endswith("/x") else {"entries": []})
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@x/featured")

    assert len(archivist.db) == 0
    assert archivist.entries_seen == 0


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/@NASA", "https://www.youtube.com/@NASA/videos"),
    ("https://www.youtube.com/@NASA/", "https://www.youtube.com/@NASA/videos"),
    ("https://www.youtube.com/channel/UCLA_DiR1FfKNvjuUpBHmylQ",
     "https://www.youtube.com/channel/UCLA_DiR1FfKNvjuUpBHmylQ/videos"),
    ("https://www.youtube.com/c/NASA", "https://www.youtube.com/c/NASA/videos"),
    ("https://www.youtube.com/@NASA/shorts", "https://www.youtube.com/@NASA/shorts"),
    ("https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/playlist?list=PL1"),
])
def test_channel_videos_url(url, expected):
    assert youtube_mod._channel_videos_url(url) == expected
