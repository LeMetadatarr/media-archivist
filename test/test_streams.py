"""yt-dlp stream core: resolve_stream / download / ytdlp_available + CLI wiring.

No real network calls — ``yt_dlp.YoutubeDL`` and ``subprocess.run`` are
always mocked.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from media_archivist import streams
from media_archivist.streams import (
    ResolvedStream,
    StreamDownloadError,
    StreamResolveError,
    default_download_dir,
    download,
    resolve_stream,
    ytdlp_available,
)


# ---------------------------------------------------------------------------
# resolve_stream — python API path
# ---------------------------------------------------------------------------

def _fake_yt_dlp_module(info):
    """Build a fake ``yt_dlp`` module whose YoutubeDL().extract_info returns info."""
    fake_ydl_instance = MagicMock()
    fake_ydl_instance.__enter__.return_value = fake_ydl_instance
    fake_ydl_instance.__exit__.return_value = False
    fake_ydl_instance.extract_info.return_value = info

    fake_module = MagicMock()
    fake_module.YoutubeDL.return_value = fake_ydl_instance
    return fake_module, fake_ydl_instance


def test_resolve_stream_picks_best_and_fills_fields():
    info = {
        "title": "Some Video",
        "duration": 123.0,
        "thumbnail": "https://x/thumb.jpg",
        "formats": [
            {"format_id": "worst1", "url": "https://x/worst.mp4", "ext": "mp4",
             "vcodec": "avc1", "acodec": "aac", "tbr": 100, "protocol": "https"},
            {"format_id": "best1", "url": "https://x/best.mp4?expire=1999999999",
             "ext": "mp4", "vcodec": "avc1", "acodec": "aac", "tbr": 5000,
             "protocol": "https"},
        ],
    }
    fake_module, _ = _fake_yt_dlp_module(info)
    with patch.object(streams, "_import_yt_dlp", return_value=fake_module):
        result = resolve_stream("https://youtube.com/watch?v=abc", prefer="best")

    assert isinstance(result, ResolvedStream)
    assert result.url == "https://x/best.mp4?expire=1999999999"
    assert result.format_id == "best1"
    assert result.ext == "mp4"
    assert result.protocol == "https"
    assert result.is_direct is True
    assert result.title == "Some Video"
    assert result.duration == 123.0
    assert result.thumbnail == "https://x/thumb.jpg"
    assert result.expires == 1999999999


def test_resolve_stream_respects_prefer_bestaudio():
    info = {
        "title": "T",
        "formats": [
            {"format_id": "v1", "url": "https://x/v.mp4", "ext": "mp4",
             "vcodec": "avc1", "acodec": "aac", "tbr": 5000},
            {"format_id": "a1", "url": "https://x/a.m4a", "ext": "m4a",
             "vcodec": "none", "acodec": "aac", "abr": 128},
        ],
    }
    fake_module, _ = _fake_yt_dlp_module(info)
    with patch.object(streams, "_import_yt_dlp", return_value=fake_module):
        result = resolve_stream("https://youtube.com/watch?v=abc", prefer="bestaudio")

    assert result.url == "https://x/a.m4a"
    assert result.format_id == "a1"


def test_resolve_stream_direct_play_result_without_formats():
    info = {
        "title": "Direct",
        "url": "https://x/direct.mp3",
        "ext": "mp3",
        "protocol": "https",
    }
    fake_module, _ = _fake_yt_dlp_module(info)
    with patch.object(streams, "_import_yt_dlp", return_value=fake_module):
        result = resolve_stream("https://example.com/audio", prefer="best")

    assert result.url == "https://x/direct.mp3"
    assert result.ext == "mp3"


def test_resolve_stream_raises_on_extractor_failure():
    fake_module = MagicMock()
    fake_ydl_instance = MagicMock()
    fake_ydl_instance.__enter__.return_value = fake_ydl_instance
    fake_ydl_instance.__exit__.return_value = False
    fake_ydl_instance.extract_info.side_effect = RuntimeError("boom")
    fake_module.YoutubeDL.return_value = fake_ydl_instance

    with patch.object(streams, "_import_yt_dlp", return_value=fake_module):
        with pytest.raises(StreamResolveError):
            resolve_stream("https://youtube.com/watch?v=abc")


def test_resolve_stream_raises_on_empty_formats():
    info = {"title": "T", "formats": []}
    fake_module, _ = _fake_yt_dlp_module(info)
    with patch.object(streams, "_import_yt_dlp", return_value=fake_module):
        with pytest.raises(StreamResolveError):
            resolve_stream("https://youtube.com/watch?v=abc")


def test_resolve_stream_rejects_non_http_urls():
    with pytest.raises(StreamResolveError):
        resolve_stream("file:///etc/passwd")
    with pytest.raises(StreamResolveError):
        resolve_stream("ftp://example.com/file")


# ---------------------------------------------------------------------------
# resolve_stream — binary fallback path
# ---------------------------------------------------------------------------

def test_resolve_stream_binary_fallback_no_shell_true():
    with patch.object(streams, "_import_yt_dlp", return_value=None), \
         patch("shutil.which", return_value="/usr/bin/yt-dlp") as which_mock, \
         patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["yt-dlp"], returncode=0,
            stdout="https://x/resolved.mp4?expire=1700000000\n", stderr="",
        )
        result = resolve_stream("https://youtube.com/watch?v=abc", prefer="best")

    assert result.url == "https://x/resolved.mp4?expire=1700000000"
    assert result.expires == 1700000000
    which_mock.assert_called_with("yt-dlp")
    run_mock.assert_called_once()
    call_args = run_mock.call_args
    cmd = call_args.args[0] if call_args.args else call_args.kwargs["args"]
    assert isinstance(cmd, list)
    assert cmd[0] == "yt-dlp"
    assert "-g" in cmd
    # never shell out with shell=True
    assert call_args.kwargs.get("shell") is not True


def test_resolve_stream_binary_fallback_missing_raises():
    with patch.object(streams, "_import_yt_dlp", return_value=None), \
         patch("shutil.which", return_value=None):
        with pytest.raises(StreamResolveError):
            resolve_stream("https://youtube.com/watch?v=abc")


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

def test_download_invokes_progress_hook_and_returns_path(tmp_path):
    dest = tmp_path / "out"
    fake_module = MagicMock()
    fake_ydl_instance = MagicMock()
    fake_ydl_instance.__enter__.return_value = fake_ydl_instance
    fake_ydl_instance.__exit__.return_value = False

    events = []

    def fake_extract_info(url, download=True):
        # Simulate yt-dlp firing progress hooks, then completion.
        for hook in fake_ydl_instance._opts["progress_hooks"]:
            hook({"status": "downloading", "_percent_str": "50%"})
            hook({"status": "finished", "filename": str(dest / "Title [id].mp4")})
        return {"id": "id", "title": "Title", "ext": "mp4"}

    def fake_youtube_dl_ctor(opts):
        fake_ydl_instance._opts = opts
        return fake_ydl_instance

    fake_ydl_instance.extract_info.side_effect = fake_extract_info
    fake_module.YoutubeDL.side_effect = fake_youtube_dl_ctor

    hook_calls = []

    with patch.object(streams, "_import_yt_dlp", return_value=fake_module):
        result_path = download(
            "https://youtube.com/watch?v=abc", str(dest),
            progress_hook=lambda d: hook_calls.append(d),
        )

    assert dest.is_dir()
    assert str(result_path) == str(dest / "Title [id].mp4")
    assert any(c.get("status") == "downloading" for c in hook_calls)
    assert any(c.get("status") == "finished" for c in hook_calls)


def test_download_rejects_non_http_url(tmp_path):
    with pytest.raises(StreamDownloadError):
        download("file:///etc/passwd", str(tmp_path))


def test_download_binary_fallback_prints_filepath(tmp_path):
    dest = tmp_path / "out2"
    with patch.object(streams, "_import_yt_dlp", return_value=None), \
         patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["yt-dlp"], returncode=0,
            stdout=f"{dest}/Title [id].mp4\n", stderr="",
        )
        result_path = download("https://youtube.com/watch?v=abc", str(dest))

    assert dest.is_dir()
    assert str(result_path) == f"{dest}/Title [id].mp4"
    cmd = run_mock.call_args.args[0]
    assert isinstance(cmd, list)
    assert run_mock.call_args.kwargs.get("shell") is not True


# ---------------------------------------------------------------------------
# ytdlp_available
# ---------------------------------------------------------------------------

def test_ytdlp_available_true_via_python_module():
    with patch.object(streams, "_import_yt_dlp", return_value=MagicMock()):
        assert ytdlp_available() is True


def test_ytdlp_available_true_via_binary():
    with patch.object(streams, "_import_yt_dlp", return_value=None), \
         patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        assert ytdlp_available() is True


def test_ytdlp_available_false():
    with patch.object(streams, "_import_yt_dlp", return_value=None), \
         patch("shutil.which", return_value=None):
        assert ytdlp_available() is False


def test_default_download_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ARCHIVIST_DOWNLOAD_DIR", str(tmp_path / "custom"))
    assert default_download_dir() == tmp_path / "custom"


def test_default_download_dir_falls_back_to_xdg(monkeypatch):
    monkeypatch.delenv("MEDIA_ARCHIVIST_DOWNLOAD_DIR", raising=False)
    d = default_download_dir()
    assert str(d).endswith("media_archivist/downloads") or \
        str(d).endswith("media_archivist\\downloads")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def test_cli_resolve_parses_and_calls_streams(capsys):
    from media_archivist.cli import main

    fake_result = ResolvedStream(url="https://x/resolved.mp4", ext="mp4",
                                  format_id="1", protocol="https")
    with patch("media_archivist.commands.streams._streams.resolve_stream",
               return_value=fake_result) as resolve_mock:
        rc = main(["resolve", "https://youtube.com/watch?v=abc", "--format", "bestaudio"])

    assert rc == 0
    resolve_mock.assert_called_once_with(
        "https://youtube.com/watch?v=abc", prefer="bestaudio"
    )
    out = capsys.readouterr().out.strip()
    assert out == "https://x/resolved.mp4"


def test_cli_resolve_json_flag(capsys):
    from media_archivist.cli import main
    import json as _json

    fake_result = ResolvedStream(url="https://x/resolved.mp4", ext="mp4")
    with patch("media_archivist.commands.streams._streams.resolve_stream",
               return_value=fake_result):
        rc = main(["resolve", "https://youtube.com/watch?v=abc", "--json"])

    assert rc == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["url"] == "https://x/resolved.mp4"
    assert payload["ext"] == "mp4"


def test_cli_download_parses_and_calls_streams(tmp_path, capsys):
    from media_archivist.cli import main

    out_dir = tmp_path / "dl"

    def fake_download(url, dest_dir, *, format="best", progress_hook=None, timeout=None):
        return tmp_path / "file.mp4"

    with patch("media_archivist.commands.streams._streams.download",
               side_effect=fake_download) as dl_mock:
        rc = main([
            "download", "--url", "https://youtube.com/watch?v=abc",
            "--output-dir", str(out_dir),
        ])

    assert rc == 0
    dl_mock.assert_called_once()
    call_kwargs = dl_mock.call_args.kwargs
    assert call_kwargs["format"] == "best"
    printed = capsys.readouterr().out
    assert "file.mp4" in printed
