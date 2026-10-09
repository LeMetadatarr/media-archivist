# SPDX-License-Identifier: Apache-2.0
"""Inline media player in the entry-detail drawer."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://x.bandcamp.com/track/y"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/y",
        "title": "Bandcamp Track",
        "artist": "Foo",
        "duration": 200,
        "stream": "https://x.bandcamp.com/stream.mp3",
    }
    db["https://soundcloud.com/foo/bar"] = {
        "source": "soundcloud",
        "url": "https://soundcloud.com/foo/bar",
        "title": "Soundcloud Track",
        "stream": "https://cf-media.sndcdn.com/bar.mp3",
    }
    db["https://archive.org/details/vid1"] = {
        "source": "internet_archive",
        "url": "https://archive.org/details/vid1",
        "title": "IA Video",
        "streams": ["https://archive.org/download/vid1/vid1.mp4"],
    }
    db["https://archive.org/details/aud1"] = {
        "source": "internet_archive",
        "url": "https://archive.org/details/aud1",
        "title": "IA Audio",
        "streams": ["https://archive.org/download/aud1/aud1.mp3"],
    }
    db["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "videoId": "dQw4w9WgXcQ",
        "title": "YouTube Video",
    }
    db["https://www.youtube.com/watch?v=abcdefghijk"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "title": "YouTube Video (from url only)",
    }
    db["https://x.bandcamp.com/track/evil"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/evil",
        "title": "Evil Stream",
        "stream": "javascript:alert(1)",
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def _entry_id(client, url_substring):
    listing = client.get("/entries").json()["entries"]
    for e in listing:
        if url_substring in e["url"]:
            return e["id"]
    raise AssertionError(f"no entry with url containing {url_substring!r}")


def test_bandcamp_entry_renders_audio_player(client):
    eid = _entry_id(client, "x.bandcamp.com/track/y")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "<audio" in r.text
    assert "https://x.bandcamp.com/stream.mp3" in r.text


def test_soundcloud_entry_renders_audio_player(client):
    eid = _entry_id(client, "soundcloud.com")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "<audio" in r.text
    assert "https://cf-media.sndcdn.com/bar.mp3" in r.text


def test_internet_archive_mp4_renders_video_player(client):
    eid = _entry_id(client, "archive.org/details/vid1")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "<video" in r.text
    assert "vid1.mp4" in r.text
    assert "<audio" not in r.text


def test_internet_archive_mp3_renders_audio_player(client):
    eid = _entry_id(client, "archive.org/details/aud1")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "<audio" in r.text
    assert "aud1.mp3" in r.text
    assert "<video" not in r.text


def test_youtube_entry_has_lazy_play_affordance_and_no_eager_iframe(client):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "Play" in r.text
    assert "hx-get" in r.text
    assert f"/ui/entries/{eid}/player" in r.text
    # No iframe should be preloaded in the detail view itself.
    assert "<iframe" not in r.text


def test_youtube_player_fragment_returns_nocookie_iframe_with_correct_id(client):
    eid = _entry_id(client, "v=dQw4w9WgXcQ")
    r = client.get(f"/ui/entries/{eid}/player")
    assert r.status_code == 200
    assert "<iframe" in r.text
    assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in r.text


def test_youtube_id_parsed_from_url_when_raw_videoid_absent(client):
    eid = _entry_id(client, "v=abcdefghijk")
    r = client.get(f"/ui/entries/{eid}/player")
    assert r.status_code == 200
    assert "youtube-nocookie.com/embed/abcdefghijk" in r.text


def test_javascript_stream_never_becomes_a_player_src(client):
    eid = _entry_id(client, "x.bandcamp.com/track/evil")
    r = client.get(f"/ui/entries/{eid}")
    assert r.status_code == 200
    assert "<audio" not in r.text
    assert "<video" not in r.text
    # The stream is displayed as plain text in the metadata table (existing
    # behavior — not an attribute value), but must never land in a src=/href=
    # attribute where a browser would act on it.
    assert 'src="javascript:' not in r.text
    assert 'href="javascript:' not in r.text
    # Fallback link to the original entry URL must still be present.
    assert "Open original" in r.text


def test_open_original_link_always_present(client):
    for substr in (
        "x.bandcamp.com/track/y", "soundcloud.com", "archive.org/details/vid1",
        "v=dQw4w9WgXcQ",
    ):
        eid = _entry_id(client, substr)
        r = client.get(f"/ui/entries/{eid}")
        assert "Open original" in r.text
