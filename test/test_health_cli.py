# SPDX-License-Identifier: Apache-2.0
"""``media-archivist health`` CLI — requests + resolve_stream mocked, no network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from media_archivist import streams
from media_archivist.cli import main
from media_archivist.index import Index
from media_archivist.storage import EnvelopeJsonStorage


def _seed(path):
    db = EnvelopeJsonStorage(str(path))
    db["https://x.bandcamp.com/track/dead"] = {
        "source": "bandcamp",
        "url": "https://x.bandcamp.com/track/dead",
        "title": "Dead",
        "stream": "https://cdn.example/dead.mp4",
    }
    db.store()
    return str(path)


def _resp(status_code):
    r = MagicMock()
    r.status_code = status_code
    r.close = MagicMock()
    return r


def test_health_dry_run_reports_but_changes_nothing(tmp_path, capsys):
    db_path = _seed(tmp_path / "db.json")
    fresh = streams.ResolvedStream(url="https://cdn.example/dead-fresh.mp4")
    with patch("requests.head", return_value=_resp(500)), \
         patch("requests.get", return_value=_resp(500)), \
         patch.object(streams, "resolve_stream", return_value=fresh):
        rc = main(["health", "--db-file", db_path, "--reresolve", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr()
    assert "dead" in out.out.lower()

    idx = Index(db_path)
    entry = next(iter(idx.view()))
    assert entry.stream == "https://cdn.example/dead.mp4"  # unchanged


def test_health_reresolve_updates_db(tmp_path):
    db_path = _seed(tmp_path / "db.json")
    fresh = streams.ResolvedStream(url="https://cdn.example/dead-fresh.mp4")
    with patch("requests.head", return_value=_resp(500)), \
         patch("requests.get", return_value=_resp(500)), \
         patch.object(streams, "resolve_stream", return_value=fresh):
        rc = main(["health", "--db-file", db_path, "--reresolve"])

    assert rc == 0
    idx = Index(db_path)
    entry = next(iter(idx.view()))
    assert entry.stream == "https://cdn.example/dead-fresh.mp4"


def test_health_no_reresolve_flag_only_reports(tmp_path):
    db_path = _seed(tmp_path / "db.json")
    with patch("requests.head", return_value=_resp(500)), \
         patch("requests.get", return_value=_resp(500)):
        rc = main(["health", "--db-file", db_path])

    assert rc == 0
    idx = Index(db_path)
    entry = next(iter(idx.view()))
    assert entry.stream == "https://cdn.example/dead.mp4"  # unchanged
