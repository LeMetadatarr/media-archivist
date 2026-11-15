# SPDX-License-Identifier: Apache-2.0
"""The download format: request body, web UI form, default selector."""
from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import streams  # noqa: E402
from media_archivist.cli import build_parser  # noqa: E402
from media_archivist.models.api import DownloadRequest  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def client(tmp_path):
    db = EnvelopeJsonStorage(str(tmp_path / "db.json"))
    db[URL] = {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ", "title": "T"}
    db.store()
    with TestClient(create_app(str(tmp_path / "db.json"))) as c:
        yield c


@pytest.fixture
def formats(monkeypatch):
    """Records the ``format`` every queued download reaches yt-dlp with."""
    seen = []

    def _download(url, dest_dir, *, format=None, progress_hook=None, timeout=None):
        seen.append(format)
        return Path(dest_dir) / "f.mkv"

    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "download", _download)
    return seen


def _entry_id(client):
    return client.get("/entries").json()["entries"][0]["id"]


def _wait_terminal(client, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/tasks/{task_id}").json()
        if task["status"] in ("ok", "error"):
            return task
        time.sleep(0.02)
    pytest.fail("task never finished")


def test_default_format_merges_video_and_audio():
    assert streams.DEFAULT_DOWNLOAD_FORMAT == "bv*+ba/b"
    assert streams.NO_FFMPEG_DOWNLOAD_FORMAT == "b"
    assert DownloadRequest(entry_id="x").format is None


def test_route_without_body_uses_default(client, formats):
    r = client.post(f"/entries/{_entry_id(client)}/download")
    assert r.status_code == 200, r.text
    assert r.json()["request"]["format"] is None
    _wait_terminal(client, r.json()["id"])
    assert formats == [None]


def test_route_with_empty_object_uses_default(client, formats):
    r = client.post(f"/entries/{_entry_id(client)}/download", json={})
    assert r.status_code == 200, r.text
    _wait_terminal(client, r.json()["id"])
    assert formats == [None]


def test_route_accepts_format_in_body(client, formats):
    r = client.post(f"/entries/{_entry_id(client)}/download",
                    json={"format": "bestaudio/best"})
    assert r.status_code == 200, r.text
    assert r.json()["request"]["format"] == "bestaudio/best"
    _wait_terminal(client, r.json()["id"])
    assert formats == ["bestaudio/best"]


def test_route_strips_whitespace_around_format(client, formats):
    r = client.post(f"/entries/{_entry_id(client)}/download",
                    json={"format": "  bestaudio/best \n"})
    assert r.status_code == 200, r.text
    assert r.json()["request"]["format"] == "bestaudio/best"
    _wait_terminal(client, r.json()["id"])
    assert formats == ["bestaudio/best"]


@pytest.mark.parametrize("body", [
    {"format": ""},
    {"format": "   "},
    {"format": "x" * 201},
    {"format": 3},
    {"dest_dir": "/etc"},
])
def test_route_rejects_bad_body(client, formats, body):
    r = client.post(f"/entries/{_entry_id(client)}/download", json=body)
    assert r.status_code == 422, r.text
    assert formats == []


def test_ui_form_format_is_used(client, formats):
    r = client.post(f"/ui/entries/{_entry_id(client)}/download",
                    data={"format": " worst "})
    assert r.status_code == 200, r.text
    time.sleep(0.3)
    assert formats == ["worst"]


def test_ui_blank_form_format_uses_default(client, formats):
    r = client.post(f"/ui/entries/{_entry_id(client)}/download", data={"format": "  "})
    assert r.status_code == 200, r.text
    time.sleep(0.3)
    assert formats == [None]


def test_ui_oversized_format_is_422_not_500(client, formats):
    r = client.post(f"/ui/entries/{_entry_id(client)}/download",
                    data={"format": "x" * 201})
    assert r.status_code == 422
    assert formats == []


def test_ui_button_offers_format_field(client, formats):
    html = client.get(f"/ui/entries/{_entry_id(client)}").text
    assert 'name="format"' in html


class _CapturingYDL:
    """A yt_dlp module double that records the options it was built with."""

    def __init__(self, tmp_path, captured):
        class _YDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download):
                return {}

            def prepare_filename(self, info):
                return str(tmp_path / "f.mkv")

        self.YoutubeDL = _YDL


def _download_format(tmp_path, monkeypatch, ffmpeg, **kwargs):
    captured = {}
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: _CapturingYDL(tmp_path, captured))
    monkeypatch.setattr(streams, "ffmpeg_available", lambda: ffmpeg)
    streams.download(URL, str(tmp_path), **kwargs)
    return captured["format"]


def test_streams_download_default_with_ffmpeg_merges(tmp_path, monkeypatch):
    assert _download_format(tmp_path, monkeypatch, True) == "bv*+ba/b"


def test_streams_download_default_without_ffmpeg_is_single_stream(tmp_path, monkeypatch):
    assert _download_format(tmp_path, monkeypatch, False) == "b"


@pytest.mark.parametrize("ffmpeg", [True, False])
@pytest.mark.parametrize("fmt", ["bv*+ba/b", "worst", "bestaudio/best"])
def test_streams_download_explicit_format_is_unchanged(tmp_path, monkeypatch, ffmpeg, fmt):
    assert _download_format(tmp_path, monkeypatch, ffmpeg, format=fmt) == fmt


def test_binary_download_without_ffmpeg_gets_single_stream(tmp_path, monkeypatch):
    seen = []

    def _run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=str(tmp_path / "f.mkv") + "\n", stderr="")

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    monkeypatch.setattr(streams, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(streams.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(streams.subprocess, "run", _run)
    streams.download(URL, str(tmp_path))
    assert seen[0][seen[0].index("-f") + 1] == "b"


def test_ffmpeg_available_follows_ytdlp_when_installed(monkeypatch):
    class _Merger:
        available = False

    fake = types.ModuleType("yt_dlp.postprocessor")
    fake.FFmpegMergerPP = _Merger
    monkeypatch.setitem(sys.modules, "yt_dlp.postprocessor", fake)
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: object())
    monkeypatch.setattr(streams.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert streams.ffmpeg_available() is False
    _Merger.available = True
    assert streams.ffmpeg_available() is True


@pytest.mark.parametrize("found,expected", [("/usr/bin/ffmpeg", True), (None, False)])
def test_ffmpeg_available_falls_back_to_path(monkeypatch, found, expected):
    monkeypatch.setitem(sys.modules, "yt_dlp.postprocessor", None)  # import fails
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: object())
    monkeypatch.setattr(streams.shutil, "which", lambda name: found)
    assert streams.ffmpeg_available() is expected
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    assert streams.ffmpeg_available() is expected


def test_cli_download_default_format():
    args = build_parser().parse_args(["download", "--url", URL, "--output-dir", "/tmp/x"])
    assert args.format is None


def test_merged_download_returns_the_merged_file_not_a_part(tmp_path, monkeypatch):
    merged = tmp_path / "Title [id].webm"

    class _YDL:
        def __init__(self, opts):
            self.hooks = opts["progress_hooks"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            for part in ("Title [id].f399.mp4", "Title [id].f251.webm"):
                for hook in self.hooks:
                    hook({"status": "finished", "filename": str(tmp_path / part)})
            return {"requested_downloads": [{"filepath": str(merged)}]}

    class _Mod:
        YoutubeDL = _YDL

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: _Mod)
    assert streams.download(URL, str(tmp_path)) == merged
