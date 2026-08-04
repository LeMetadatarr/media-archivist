# SPDX-License-Identifier: Apache-2.0
"""yt-dlp stream resolution wired into /strm and the WebUI player.

media-archivist archives streams, not bytes: source URLs (especially
YouTube) expire, so /strm and the player can optionally re-resolve a
fresh direct URL on demand via media_archivist.streams. All network
calls into yt-dlp are mocked -- no network in this test module.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import streams  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "videoId": "dQw4w9WgXcQ",
        "title": "YouTube Video",
    }
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "Bandcamp Track",
        "artist": "Foo",
        "stream": "https://x.bandcamp.com/stream.mp3",
    }
    db.store()
    return path


@pytest.fixture
def client(db_path):
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def _entry_id(client, url_substring):
    listing = client.get("/entries").json()["entries"]
    for e in listing:
        if url_substring in e["url"]:
            return e["id"]
    raise AssertionError(f"no entry with url containing {url_substring!r}")


def _resolved(url="https://cdn.example.com/fresh.mp4", ext="mp4"):
    return streams.ResolvedStream(url=url, ext=ext, format_id="18",
                                   protocol="https", is_direct=True,
                                   title="t")


# ---------------------------------------------------------------------
# /strm/{id}?resolve=1
# ---------------------------------------------------------------------

def test_strm_without_resolve_returns_entry_stream_unchanged(client):
    eid = _entry_id(client, "x.bandcamp.com")
    r = client.get(f"/strm/{eid}")
    assert r.status_code == 200
    assert r.text.strip() == "https://x.bandcamp.com/stream.mp3"


def test_strm_resolve_param_returns_resolved_url(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    monkeypatch.setattr(streams, "resolve_stream",
                         lambda url, **kw: _resolved())
    r = client.get(f"/strm/{eid}?resolve=1")
    assert r.status_code == 200
    assert r.text.strip() == "https://cdn.example.com/fresh.mp4"


def test_strm_resolve_falls_back_to_unresolved_on_error(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")

    def _boom(url, **kw):
        raise streams.StreamResolveError("no formats")

    monkeypatch.setattr(streams, "resolve_stream", _boom)
    r = client.get(f"/strm/{eid}?resolve=1")
    # Never 500 -- Jellyfin needs a body back.
    assert r.status_code == 200
    assert r.text.strip() == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_strm_env_default_enables_resolve(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    monkeypatch.setattr(streams, "resolve_stream",
                         lambda url, **kw: _resolved())
    monkeypatch.setenv("MEDIA_ARCHIVIST_STRM_RESOLVE", "1")
    r = client.get(f"/strm/{eid}")
    assert r.status_code == 200
    assert r.text.strip() == "https://cdn.example.com/fresh.mp4"


def test_strm_missing_entry_still_404s(client):
    r = client.get("/strm/does-not-exist?resolve=1")
    assert r.status_code == 404


# ---------------------------------------------------------------------
# GET /ui/entries/{id}/resolve
# ---------------------------------------------------------------------

def test_resolve_fragment_returns_video_player_for_youtube_entry(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    monkeypatch.setattr(streams, "resolve_stream",
                         lambda url, **kw: _resolved())
    r = client.get(f"/ui/entries/{eid}/resolve")
    assert r.status_code == 200
    assert "<video" in r.text
    assert "https://cdn.example.com/fresh.mp4" in r.text


def test_resolve_fragment_audio_ext_renders_audio_player(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    monkeypatch.setattr(
        streams, "resolve_stream",
        lambda url, **kw: _resolved(url="https://cdn.example.com/fresh.mp3", ext="mp3"),
    )
    r = client.get(f"/ui/entries/{eid}/resolve")
    assert r.status_code == 200
    assert "<audio" in r.text
    assert "https://cdn.example.com/fresh.mp3" in r.text


def test_resolve_fragment_error_is_inline_not_500(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")

    def _boom(url, **kw):
        raise streams.StreamResolveError("unsupported url")

    monkeypatch.setattr(streams, "resolve_stream", _boom)
    r = client.get(f"/ui/entries/{eid}/resolve")
    assert r.status_code == 200
    assert "Could not resolve a direct stream" in r.text
    assert "Open original" in r.text
    assert "<video" not in r.text
    assert "<audio" not in r.text


def test_resolve_fragment_blocks_javascript_url_via_safe_url(client, monkeypatch):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    monkeypatch.setattr(
        streams, "resolve_stream",
        lambda url, **kw: _resolved(url="javascript:alert(1)", ext="mp4"),
    )
    r = client.get(f"/ui/entries/{eid}/resolve")
    assert r.status_code == 200
    assert 'src="javascript:' not in r.text


def test_resolve_fragment_missing_entry_404s(client):
    r = client.get("/ui/entries/does-not-exist/resolve")
    assert r.status_code == 404


# ---------------------------------------------------------------------
# entry_detail: yt-dlp affordance gated on ytdlp_available()
# ---------------------------------------------------------------------

def test_entry_detail_shows_play_ytdlp_when_available(client, monkeypatch):
    monkeypatch.setattr(
        "media_archivist.server.web.ytdlp_available", lambda: True, raising=False,
    )
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "Play (yt-dlp)" in r.text
    assert f"/ui/entries/{eid}/resolve" in r.text


def test_entry_detail_hides_play_ytdlp_when_unavailable(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: False)
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "Play (yt-dlp)" not in r.text


def test_entry_detail_shows_refresh_stream_for_stale_direct_stream(client, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    eid = _entry_id(client, "x.bandcamp.com")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "refresh stream" in r.text
    assert f"/ui/entries/{eid}/resolve" in r.text
