"""Subtitle sidecar fetching (media_archivist.subtitles).

All yt-dlp interaction is mocked — no network. Verifies sidecar
filenames match the entry basename (same as .strm export) so
Jellyfin/Kodi pick them up automatically.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from media_archivist import subtitles as subs_mod
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source
from media_archivist.storage import EnvelopeJsonStorage


def _entry(title="Hello World", url="https://www.youtube.com/watch?v=abc",
          artist=None, source=Source.YOUTUBE) -> MediaEntry:
    return MediaEntry.build(source=source, url=url, title=title, raw={}, artist=artist)


def _fake_fetch_factory(lang_files):
    """Build a fake fetch_subtitle_files() that writes ``lang_files`` into out_dir."""
    def _fake(url, out_dir, *, languages, auto, sub_format="vtt", timeout=60):
        written = []
        for lang in lang_files:
            f = Path(out_dir) / f"video.{lang}.{sub_format}"
            f.write_text(f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi ({lang})\n")
            written.append(f)
        return written
    return _fake


def test_fetch_subtitles_writes_sidecar_matching_entry_basename(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", _fake_fetch_factory(["en"]))

    entry = _entry(title="Hello World")
    out = tmp_path / "out"
    result = subs_mod.fetch_subtitles(entry, out)

    assert result.status == "written"
    assert result.langs == ["en"]
    dest = out / "Hello World.en.vtt"
    assert dest.exists()
    assert str(dest) in result.files
    assert "hi (en)" in dest.read_text()


def test_fetch_subtitles_dry_run_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    called = []
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files",
                        lambda *a, **k: called.append(1) or [])

    entry = _entry()
    out = tmp_path / "out"
    result = subs_mod.fetch_subtitles(entry, out, dry_run=True)

    assert result.status == "dry-run"
    assert not called  # fetch_subtitle_files never invoked
    assert not out.exists() or not list(out.iterdir())


def test_fetch_subtitles_ytdlp_unavailable_is_skipped_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: False)

    entry = _entry()
    result = subs_mod.fetch_subtitles(entry, tmp_path / "out")

    assert result.status == "skipped"
    assert result.error


def test_fetch_subtitles_no_subs_available_reports_none(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", lambda *a, **k: [])

    entry = _entry()
    result = subs_mod.fetch_subtitles(entry, tmp_path / "out")

    assert result.status == "none"
    assert not (tmp_path / "out").exists()


def test_fetch_subtitles_multiple_langs(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files",
                        _fake_fetch_factory(["en", "es"]))

    entry = _entry(title="Multi")
    out = tmp_path / "out"
    result = subs_mod.fetch_subtitles(entry, out, langs=["en", "es"])

    assert result.status == "written"
    assert sorted(result.langs) == ["en", "es"]
    assert (out / "Multi.en.vtt").exists()
    assert (out / "Multi.es.vtt").exists()


def test_fetch_subtitles_passes_auto_flag_through(monkeypatch, tmp_path):
    seen = {}

    def _fake(url, out_dir, *, languages, auto, sub_format="vtt", timeout=60):
        seen["auto"] = auto
        seen["languages"] = list(languages)
        return []

    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", _fake)

    entry = _entry()
    subs_mod.fetch_subtitles(entry, tmp_path / "out", langs=["fr"], auto=False)

    assert seen["auto"] is False
    assert seen["languages"] == ["fr"]


def test_fetch_subtitles_manual_only_when_auto_true_default(monkeypatch, tmp_path):
    seen = {}

    def _fake(url, out_dir, *, languages, auto, sub_format="vtt", timeout=60):
        seen["auto"] = auto
        return []

    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", _fake)

    entry = _entry()
    subs_mod.fetch_subtitles(entry, tmp_path / "out")

    assert seen["auto"] is True


def test_fetch_subtitles_never_raises_on_fetch_error(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("yt-dlp exploded")

    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", _boom)

    entry = _entry()
    result = subs_mod.fetch_subtitles(entry, tmp_path / "out")

    assert result.status == "error"
    assert "exploded" in result.error


def _seed_db(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["https://www.youtube.com/watch?v=a"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=a",
        "videoId": "a", "title": "First", "author": "Chan",
    }
    db["https://www.youtube.com/watch?v=b"] = {
        "source": "youtube", "url": "https://www.youtube.com/watch?v=b",
        "videoId": "b", "title": "Second", "author": "Chan",
    }
    db.store()
    return db_path


def test_fetch_library_subtitles_per_entry_results(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", _fake_fetch_factory(["en"]))

    db_path = _seed_db(tmp_path)
    out = tmp_path / "out"
    results = subs_mod.fetch_library_subtitles(db_path, out, max_workers=1)

    assert len(results) == 2
    assert all(r.status == "written" for r in results)
    assert (out / "youtube" / "Chan" / "First.en.vtt").exists()
    assert (out / "youtube" / "Chan" / "Second.en.vtt").exists()


def test_fetch_library_subtitles_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    called = []
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files",
                        lambda *a, **k: called.append(1) or [])

    db_path = _seed_db(tmp_path)
    out = tmp_path / "out"
    results = subs_mod.fetch_library_subtitles(db_path, out, dry_run=True, max_workers=1)

    assert len(results) == 2
    assert all(r.status == "dry-run" for r in results)
    assert not called
    assert not list(out.rglob("*.vtt"))


def test_fetch_library_subtitles_rejects_unknown_layout(tmp_path):
    db_path = _seed_db(tmp_path)
    with pytest.raises(ValueError):
        subs_mod.fetch_library_subtitles(db_path, tmp_path / "out", layout="nonsense")


def test_fetch_library_subtitles_filters_by_source(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(subs_mod, "fetch_subtitle_files", _fake_fetch_factory(["en"]))

    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = {"source": "bandcamp", "url": "a", "title": "BC", "artist": "X",
               "stream": "s"}
    db["b"] = {"source": "youtube", "url": "https://www.youtube.com/watch?v=b",
               "videoId": "b", "title": "YT", "author": "Y"}
    db.store()

    out = tmp_path / "out"
    results = subs_mod.fetch_library_subtitles(db_path, out, source="youtube", max_workers=1)
    assert len(results) == 1
    assert results[0].entry_id
