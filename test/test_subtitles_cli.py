"""``media-archivist subtitles`` CLI wiring."""
from __future__ import annotations

import media_archivist.commands.subtitles as subs_cmd
from media_archivist.cli import main
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.subtitles import SubtitleResult


def _seed_db(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=a"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=a",
        "videoId": "a", "title": "First", "author": "Chan",
    }
    db.store()
    return db_path


def test_cli_subtitles_calls_fetch_library_subtitles(monkeypatch, tmp_path, capsys):
    db_path = _seed_db(tmp_path)
    out = tmp_path / "out"
    calls = {}

    def _fake(db, out_dir, **kwargs):
        calls["db"] = db
        calls["out_dir"] = out_dir
        calls["kwargs"] = kwargs
        return [SubtitleResult(entry_id="x", status="written", langs=["en"],
                               files=[str(out_dir) + "/x.en.vtt"])]

    monkeypatch.setattr(subs_cmd, "fetch_library_subtitles", _fake)

    rc = main(["subtitles", "--db-file", str(db_path), "--output-dir", str(out)])
    assert rc == 0
    assert calls["db"] == str(db_path)
    assert calls["out_dir"] == str(out)
    assert calls["kwargs"]["langs"] == ["en"]
    assert calls["kwargs"]["auto"] is True

    err = capsys.readouterr().err
    assert "fetched subtitles for 1 entries" in err
    assert "written=1" in err


def test_cli_subtitles_dry_run_and_lang_flag(monkeypatch, tmp_path):
    db_path = _seed_db(tmp_path)
    out = tmp_path / "out"
    calls = {}

    def _fake(db, out_dir, **kwargs):
        calls["kwargs"] = kwargs
        return [SubtitleResult(entry_id="x", status="dry-run", langs=["es"])]

    monkeypatch.setattr(subs_cmd, "fetch_library_subtitles", _fake)

    rc = main(["subtitles", "--db-file", str(db_path), "--output-dir", str(out),
              "--lang", "es", "--no-auto", "--dry-run"])
    assert rc == 0
    assert calls["kwargs"]["dry_run"] is True
    assert calls["kwargs"]["auto"] is False
    assert calls["kwargs"]["langs"] == ["es"]


def test_cli_subtitles_reports_error_exit_code(monkeypatch, tmp_path, capsys):
    db_path = _seed_db(tmp_path)
    out = tmp_path / "out"

    def _fake(db, out_dir, **kwargs):
        return [SubtitleResult(entry_id="x", status="error", error="boom")]

    monkeypatch.setattr(subs_cmd, "fetch_library_subtitles", _fake)

    rc = main(["subtitles", "--db-file", str(db_path), "--output-dir", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "boom" in err
