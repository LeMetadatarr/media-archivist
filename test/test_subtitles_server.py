"""POST /entries/{id}/subtitles — exercised via FastAPI's TestClient (no network)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist import streams as streams_mod  # noqa: E402
from media_archivist import subtitles as subs_mod  # noqa: E402
from media_archivist.server.app import create_app  # noqa: E402
from media_archivist.storage import EnvelopeJsonStorage  # noqa: E402


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=a"] = {
        "source": "youtube",
        "url": "https://www.youtube.com/watch?v=a",
        "videoId": "a",
        "title": "Hello YouTube",
        "duration": 240,
    }
    db.store()
    app = create_app(str(db_path))
    with TestClient(app) as c:
        yield c


def test_post_subtitles_returns_written_result(monkeypatch, client):
    monkeypatch.setattr(streams_mod, "ytdlp_available", lambda: True)

    def _fake(entry, out_dir, **kwargs):
        return subs_mod.SubtitleResult(
            entry_id=entry.id, status="written", langs=["en"],
            files=[f"{out_dir}/Hello YouTube.en.vtt"],
        )

    monkeypatch.setattr(subs_mod, "fetch_subtitles", _fake)

    entry_id = client.get("/entries").json()["entries"][0]["id"]
    r = client.post(f"/entries/{entry_id}/subtitles")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "written"
    assert body["langs"] == ["en"]
    assert body["files"][0].endswith("Hello YouTube.en.vtt")


def test_post_subtitles_404_for_unknown_entry(monkeypatch, client):
    monkeypatch.setattr(streams_mod, "ytdlp_available", lambda: True)
    r = client.post("/entries/does-not-exist/subtitles")
    assert r.status_code == 404


def test_post_subtitles_503_when_ytdlp_unavailable(monkeypatch, client):
    monkeypatch.setattr(streams_mod, "ytdlp_available", lambda: False)
    entry_id = client.get("/entries").json()["entries"][0]["id"]
    r = client.post(f"/entries/{entry_id}/subtitles")
    assert r.status_code == 503
