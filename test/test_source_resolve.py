# SPDX-License-Identifier: Apache-2.0
"""Source-aware stream resolution.

media_archivist.streams.resolve_stream dispatches by ``source``:
soundcloud/bandcamp use their native archivist libs (nuvem_de_som /
py_bandcamp), internet_archive urls are already direct, and youtube/
generic/None fall back to yt-dlp (the pre-existing behavior). All
external libs are mocked -- no network in this test module.
"""
from __future__ import annotations

import sys
import types

import pytest

from media_archivist import streams


def _fake_ytdlp_module():
    """A minimal fake yt_dlp module so the yt-dlp path is exercised
    without touching the real package or the network."""
    calls = {"extract_info": None}

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            calls["extract_info"] = url
            return {
                "title": "yt title",
                "duration": 42,
                "thumbnail": None,
                "ext": "mp4",
                "formats": [
                    {"url": "https://cdn.example.com/yt.mp4", "ext": "mp4",
                     "format_id": "18", "protocol": "https",
                     "vcodec": "h264", "acodec": "aac", "tbr": 500},
                ],
            }

    mod = types.SimpleNamespace(YoutubeDL=_FakeYDL)
    return mod, calls


def test_resolve_soundcloud_uses_nuvem_de_som(monkeypatch):
    called = {}

    class _FakeSoundCloud:
        def resolve_stream(self, url, prefer="progressive"):
            called["url"] = url
            return "https://sc-cdn.example.com/direct.mp3"

    fake_mod = types.SimpleNamespace(SoundCloud=_FakeSoundCloud)
    monkeypatch.setitem(sys.modules, "nuvem_de_som", fake_mod)

    def _boom_ytdlp(*a, **kw):
        raise AssertionError("yt-dlp path must not be used for soundcloud")

    monkeypatch.setattr(streams, "_resolve_via_ytdlp", _boom_ytdlp)

    result = streams.resolve_stream(
        "https://soundcloud.com/artist/track", source="soundcloud"
    )
    assert result.url == "https://sc-cdn.example.com/direct.mp3"
    assert result.is_direct is True
    assert called["url"] == "https://soundcloud.com/artist/track"


def test_resolve_bandcamp_uses_py_bandcamp(monkeypatch):
    class _FakeTrack:
        stream = "https://bc-cdn.example.com/direct.mp3"
        title = "bc title"
        duration = 123.0
        image = "https://bc-cdn.example.com/art.jpg"

    class _FakeBandcampTrack:
        @staticmethod
        def from_url(url):
            assert url == "https://x.bandcamp.com/track/y"
            return _FakeTrack()

    fake_mod = types.SimpleNamespace(BandcampTrack=_FakeBandcampTrack)
    monkeypatch.setitem(sys.modules, "py_bandcamp", fake_mod)

    def _boom_ytdlp(*a, **kw):
        raise AssertionError("yt-dlp path must not be used for bandcamp")

    monkeypatch.setattr(streams, "_resolve_via_ytdlp", _boom_ytdlp)

    result = streams.resolve_stream(
        "https://x.bandcamp.com/track/y", source="bandcamp"
    )
    assert result.url == "https://bc-cdn.example.com/direct.mp3"
    assert result.title == "bc title"
    assert result.is_direct is True


def test_resolve_internet_archive_is_already_direct(monkeypatch):
    def _boom_ytdlp(*a, **kw):
        raise AssertionError("yt-dlp path must not be used for internet_archive")

    monkeypatch.setattr(streams, "_resolve_via_ytdlp", _boom_ytdlp)

    ia_url = "https://archive.org/download/item/file.mp3"
    result = streams.resolve_stream(ia_url, source="internet_archive")
    assert result.url == ia_url
    assert result.is_direct is True


def test_resolve_youtube_uses_ytdlp_path(monkeypatch):
    fake_mod, calls = _fake_ytdlp_module()
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: fake_mod)

    result = streams.resolve_stream(
        "https://www.youtube.com/watch?v=abc", source="youtube"
    )
    assert result.url == "https://cdn.example.com/yt.mp4"
    assert calls["extract_info"] == "https://www.youtube.com/watch?v=abc"


def test_resolve_no_source_uses_ytdlp_path_backcompat(monkeypatch):
    fake_mod, calls = _fake_ytdlp_module()
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: fake_mod)

    result = streams.resolve_stream("https://www.youtube.com/watch?v=abc")
    assert result.url == "https://cdn.example.com/yt.mp4"
    assert calls["extract_info"] == "https://www.youtube.com/watch?v=abc"


def test_resolve_soundcloud_falls_back_to_ytdlp_on_import_error(monkeypatch):
    # Simulate nuvem_de_som not being installed.
    monkeypatch.delitem(sys.modules, "nuvem_de_som", raising=False)

    real_import = __import__

    def _blocked_import(name, *a, **kw):
        if name == "nuvem_de_som":
            raise ImportError("no module named nuvem_de_som")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _blocked_import)

    fake_mod, calls = _fake_ytdlp_module()
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: fake_mod)

    result = streams.resolve_stream(
        "https://soundcloud.com/artist/track", source="soundcloud"
    )
    assert result.url == "https://cdn.example.com/yt.mp4"
    assert calls["extract_info"] == "https://soundcloud.com/artist/track"


def test_resolve_soundcloud_falls_back_to_ytdlp_on_native_error(monkeypatch):
    class _FakeSoundCloud:
        def resolve_stream(self, url, prefer="progressive"):
            raise RuntimeError("soundcloud api exploded")

    fake_mod = types.SimpleNamespace(SoundCloud=_FakeSoundCloud)
    monkeypatch.setitem(sys.modules, "nuvem_de_som", fake_mod)

    ytdlp_mod, calls = _fake_ytdlp_module()
    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: ytdlp_mod)

    result = streams.resolve_stream(
        "https://soundcloud.com/artist/track", source="soundcloud"
    )
    assert result.url == "https://cdn.example.com/yt.mp4"
    assert calls["extract_info"] == "https://soundcloud.com/artist/track"


def test_resolve_rejects_non_http_scheme():
    with pytest.raises(streams.StreamResolveError):
        streams.resolve_stream("ftp://example.com/x", source="soundcloud")
