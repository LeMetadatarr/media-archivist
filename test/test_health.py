# SPDX-License-Identifier: Apache-2.0
"""media_archivist.health — no real network: requests + resolve_stream mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from media_archivist import health, streams
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.index import Index


def _seed_db(path):
    db = EnvelopeJsonStorage(str(path))
    db["https://x.bandcamp.com/track/live"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/live",
        "title": "Alive",
        "stream": "https://cdn.example/live.mp4",
    }
    db["https://x.bandcamp.com/track/dead"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/dead",
        "title": "Dead",
        "stream": "https://cdn.example/dead.mp4",
    }
    db["https://x.bandcamp.com/track/stale"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/stale",
        "title": "Stale",
        "stream": "https://cdn.example/stale.mp4?expire=1",
    }
    db["https://soundcloud.com/x/nostream"] = {
        "source": "soundcloud",
        "url": "https://soundcloud.com/x/nostream",
        "title": "No direct stream",
    }
    db.store()
    return str(path)


@pytest.fixture
def db_path(tmp_path):
    return _seed_db(tmp_path / "db.json")


def _resp(status_code):
    r = MagicMock()
    r.status_code = status_code
    r.close = MagicMock()
    return r


# ---------------------------------------------------------------------------
# check_entry
# ---------------------------------------------------------------------------

def test_check_entry_ok_on_200():
    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream="https://cdn/x.mp4",
    )
    with patch("requests.head", return_value=_resp(200)):
        result = health.check_entry(idx_entry)
    assert result.status == "ok"
    assert result.status_code == 200


def test_check_entry_dead_on_connection_error():
    import requests

    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream="https://cdn/x.mp4",
    )
    with patch("requests.head", side_effect=requests.ConnectionError("boom")):
        result = health.check_entry(idx_entry)
    assert result.status == "dead"
    assert result.status_code is None
    assert "boom" in (result.reason or "")


def test_check_entry_expired_on_403():
    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream="https://cdn/x.mp4",
    )
    with patch("requests.head", return_value=_resp(403)), \
         patch("requests.get", return_value=_resp(403)):
        result = health.check_entry(idx_entry)
    assert result.status == "expired"
    assert result.status_code == 403


def test_check_entry_dead_on_500():
    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream="https://cdn/x.mp4",
    )
    with patch("requests.head", return_value=_resp(500)), \
         patch("requests.get", return_value=_resp(500)):
        result = health.check_entry(idx_entry)
    assert result.status == "dead"
    assert result.status_code == 500


def test_check_entry_no_stream():
    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream=None,
    )
    result = health.check_entry(idx_entry)
    assert result.status == "no-stream"


def test_check_entry_head_405_falls_back_to_get():
    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream="https://cdn/x.mp4",
    )
    with patch("requests.head", return_value=_resp(405)), \
         patch("requests.get", return_value=_resp(200)) as mock_get:
        result = health.check_entry(idx_entry)
    assert result.status == "ok"
    assert mock_get.called


def test_check_entry_never_raises_on_unexpected_error():
    idx_entry = MagicMock(
        id="e1", url="https://x", source=MagicMock(value="bandcamp"),
        title="T", stream="https://cdn/x.mp4",
    )
    with patch("requests.head", side_effect=RuntimeError("kaboom")):
        result = health.check_entry(idx_entry)  # must not raise
    assert result.status == "dead"


# ---------------------------------------------------------------------------
# check_library
# ---------------------------------------------------------------------------

def _mixed_get(url, **kwargs):
    if "live" in url:
        return _resp(200)
    if "dead" in url:
        return _resp(500)
    if "stale" in url:
        return _resp(410)
    return _resp(200)


def test_check_library_classifies_and_counts(db_path):
    with patch("requests.head", side_effect=_mixed_get), \
         patch("requests.get", side_effect=_mixed_get):
        results = health.check_library(db_path)

    by_title = {r.title: r.status for r in results}
    assert by_title["Alive"] == "ok"
    assert by_title["Dead"] == "dead"
    assert by_title["Stale"] == "expired"
    assert by_title["No direct stream"] == "no-stream"
    assert len(results) == 4


def test_check_library_respects_source_filter(db_path):
    with patch("requests.head", side_effect=_mixed_get):
        results = health.check_library(db_path, source="soundcloud")
    assert len(results) == 1
    assert results[0].title == "No direct stream"


def test_check_library_respects_limit(db_path):
    with patch("requests.head", side_effect=_mixed_get):
        results = health.check_library(db_path, limit=2)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# reresolve_entry
# ---------------------------------------------------------------------------

def _entry_for(db_path, entry_id):
    idx = Index(db_path)
    return idx.get(entry_id)


def test_reresolve_entry_updates_stream_when_not_dry_run(db_path):
    idx = Index(db_path)
    entry = next(e for e in idx.view() if e.title == "Dead")

    fresh = streams.ResolvedStream(url="https://cdn.example/dead-fresh.mp4")
    with patch.object(streams, "resolve_stream", return_value=fresh) as mock_resolve:
        result = health.reresolve_entry(db_path, entry, dry_run=False)

    assert result.ok is True
    assert result.new_stream == "https://cdn.example/dead-fresh.mp4"
    mock_resolve.assert_called_once()
    _, kwargs = mock_resolve.call_args
    assert kwargs.get("source") == "bandcamp"

    # Persisted to disk.
    idx2 = Index(db_path)
    updated = next(e for e in idx2.view() if e.title == "Dead")
    assert updated.stream == "https://cdn.example/dead-fresh.mp4"


def test_reresolve_entry_dry_run_does_not_update_db(db_path):
    idx = Index(db_path)
    entry = next(e for e in idx.view() if e.title == "Dead")
    original_stream = entry.stream

    fresh = streams.ResolvedStream(url="https://cdn.example/dead-fresh.mp4")
    with patch.object(streams, "resolve_stream", return_value=fresh):
        result = health.reresolve_entry(db_path, entry, dry_run=True)

    assert result.ok is True
    assert result.new_stream == "https://cdn.example/dead-fresh.mp4"
    assert result.dry_run is True

    idx2 = Index(db_path)
    unchanged = next(e for e in idx2.view() if e.title == "Dead")
    assert unchanged.stream == original_stream


# ---------------------------------------------------------------------------
# YouTube oEmbed-based deleted-video detection
# ---------------------------------------------------------------------------

def _youtube_entry(url="https://www.youtube.com/watch?v=abc12345678"):
    return MagicMock(
        id="yt1", url=url, source=MagicMock(value="youtube"),
        title="Some Video", stream=None,
    )


def test_check_entry_youtube_gone_on_oembed_404():
    entry = _youtube_entry()
    with patch("requests.get", return_value=_resp(404)) as mock_get:
        result = health.check_entry(entry)
    assert result.status == "gone"
    assert result.status_code == 404
    # Must have hit the oEmbed endpoint with the *watch* url, not a plain
    # probe of the watch page itself.
    called_url = mock_get.call_args[0][0]
    assert "oembed" in called_url
    assert "youtube.com/watch" in called_url or "%2F" in called_url


def test_check_entry_youtube_ok_on_oembed_200():
    entry = _youtube_entry()
    with patch("requests.get", return_value=_resp(200)):
        result = health.check_entry(entry)
    assert result.status == "ok"


def test_check_entry_youtube_ignores_watch_page_200_uses_oembed():
    """A deleted video's watch PAGE still 200s -- only oEmbed tells the truth.

    We never call requests.head/get against the watch url itself for a
    youtube entry (no entry.stream to probe) -- only the oEmbed endpoint
    is hit, and its 404 is authoritative regardless of what the watch
    page would have returned.
    """
    entry = _youtube_entry()
    with patch("requests.get", return_value=_resp(404)) as mock_get:
        result = health.check_entry(entry)
    assert result.status == "gone"
    assert mock_get.call_count == 1  # only the oEmbed call, nothing else


def test_check_entry_youtube_dead_on_oembed_network_error():
    import requests

    entry = _youtube_entry()
    with patch("requests.get", side_effect=requests.ConnectionError("no route")):
        result = health.check_entry(entry)
    assert result.status == "dead"
    assert "no route" in (result.reason or "")


def test_check_entry_youtube_detected_by_url_even_without_source():
    entry = MagicMock(
        id="yt2", url="https://youtu.be/abc12345678",
        source=MagicMock(value="unknown"), title="T", stream=None,
    )
    with patch("requests.get", return_value=_resp(404)):
        result = health.check_entry(entry)
    assert result.status == "gone"


def test_reresolve_entry_refuses_gone_youtube_entry(db_path):
    # Seed a youtube entry directly.
    from media_archivist.storage import EnvelopeJsonStorage as _Storage
    import tempfile
    d = tempfile.mkdtemp()
    p = f"{d}/db.json"
    db = _Storage(p)
    db["https://www.youtube.com/watch?v=deleted123"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=deleted123",
        "videoId": "deleted123", "title": "Deleted Video",
    }
    db.store()
    idx = Index(p)
    entry = next(iter(idx.view()))

    with patch("requests.get", return_value=_resp(404)) as mock_oembed, \
         patch.object(streams, "resolve_stream") as mock_resolve:
        result = health.reresolve_entry(p, entry, dry_run=False)

    assert result.ok is False
    assert "deleted" in (result.error or "").lower() or "gone" in (result.error or "").lower() \
        or "unavailable" in (result.error or "").lower()
    mock_resolve.assert_not_called()
    assert mock_oembed.called


def test_check_library_classifies_gone_youtube_entries(db_path):
    """check_library over a mixed DB: a deleted youtube entry -> "gone"."""
    import tempfile
    from media_archivist.storage import EnvelopeJsonStorage as _Storage

    d = tempfile.mkdtemp()
    p = f"{d}/db.json"
    db = _Storage(p)
    db["https://www.youtube.com/watch?v=alive1"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=alive1",
        "videoId": "alive1", "title": "Alive Video",
    }
    db["https://www.youtube.com/watch?v=deleted1"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=deleted1",
        "videoId": "deleted1", "title": "Deleted Video",
    }
    db.store()

    def _oembed_get(url, **kwargs):
        return _resp(404) if "deleted1" in url else _resp(200)

    with patch("requests.get", side_effect=_oembed_get):
        results = health.check_library(p)

    by_title = {r.title: r.status for r in results}
    assert by_title["Alive Video"] == "ok"
    assert by_title["Deleted Video"] == "gone"


def test_reresolve_entry_reports_failure_without_raising(db_path):
    idx = Index(db_path)
    entry = next(e for e in idx.view() if e.title == "Dead")
    original_stream = entry.stream

    with patch.object(streams, "resolve_stream",
                       side_effect=streams.StreamResolveError("nope")):
        result = health.reresolve_entry(db_path, entry, dry_run=False)  # must not raise

    assert result.ok is False
    assert "nope" in (result.error or "")

    idx2 = Index(db_path)
    unchanged = next(e for e in idx2.view() if e.title == "Dead")
    assert unchanged.stream == original_stream


# ---------------------------------------------------------------------------
# remove_entry
# ---------------------------------------------------------------------------

def test_remove_entry_drops_row(db_path):
    idx = Index(db_path)
    entry = next(e for e in idx.view() if e.title == "Dead")

    ok = health.remove_entry(db_path, entry)
    assert ok is True

    idx2 = Index(db_path)
    assert idx2.get(entry.id) is None


def test_remove_entry_returns_false_when_already_gone(db_path):
    idx = Index(db_path)
    entry = next(e for e in idx.view() if e.title == "Dead")
    health.remove_entry(db_path, entry)

    ok = health.remove_entry(db_path, entry)  # second call: already removed
    assert ok is False
