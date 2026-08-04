"""Tests for media_archivist.library — local media-folder tagger.

No real network access: metadatarr.resolve is always monkeypatched.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from media_archivist import library
from mediavocab import MediaType
from mediavocab.models.signals import Signals


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------

def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scan_finds_video_and_music_and_ignores_other_files(tmp_path):
    _touch(tmp_path / "Movies" / "Big Buck Bunny (2008).mp4")
    _touch(tmp_path / "Music" / "Aphex Twin - Avril 14th.mp3")
    _touch(tmp_path / "Movies" / "poster.jpg")
    _touch(tmp_path / "Movies" / "Big Buck Bunny (2008).srt")

    found = {f.path.name: f.kind for f in library.scan(str(tmp_path))}
    assert found == {
        "Big Buck Bunny (2008).mp4": "video",
        "Aphex Twin - Avril 14th.mp3": "music",
    }


def test_scan_respects_media_filter(tmp_path):
    _touch(tmp_path / "a.mkv")
    _touch(tmp_path / "b.flac")

    video_only = list(library.scan(str(tmp_path), media="video"))
    music_only = list(library.scan(str(tmp_path), media="music"))
    assert [f.path.name for f in video_only] == ["a.mkv"]
    assert [f.path.name for f in music_only] == ["b.flac"]


# ---------------------------------------------------------------------------
# extract_signals() — filename fallback (guessit/mutagen absent)
# ---------------------------------------------------------------------------

def test_extract_signals_movie_filename_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    f = library.LocalMediaFile(path=_touch(tmp_path / "Inception (2010).mkv"), kind="video")
    signals = library.extract_signals(f)
    assert signals.title == "Inception"
    assert signals.year == 2010
    assert signals.medium == MediaType.MOVIE


def test_extract_signals_tv_filename_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    f = library.LocalMediaFile(path=_touch(tmp_path / "Show.Name.S01E02.mkv"), kind="video")
    signals = library.extract_signals(f)
    assert signals.season == 1
    assert signals.episode == 2
    assert signals.medium == MediaType.EPISODIC_SERIES
    assert "Show" in (signals.title or "")


def test_extract_signals_music_filename_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_mutagen", None)
    f = library.LocalMediaFile(path=_touch(tmp_path / "Artist - Song.mp3"), kind="music")
    signals = library.extract_signals(f)
    assert signals.artist == "Artist"
    assert signals.title == "Song"
    assert signals.medium == MediaType.MUSIC


def test_extract_signals_music_reads_embedded_tags_when_mutagen_present(tmp_path, monkeypatch):
    fake_tags = {"title": ["Real Title"], "artist": ["Real Artist"], "date": ["2015-01-01"]}

    class _FakeAudio:
        tags = fake_tags

    class _FakeMutagenModule:
        @staticmethod
        def File(path, easy=True):
            return _FakeAudio()

    monkeypatch.setattr(library, "_mutagen", _FakeMutagenModule())
    f = library.LocalMediaFile(path=_touch(tmp_path / "whatever.mp3"), kind="music")
    signals = library.extract_signals(f)
    assert signals.title == "Real Title"
    assert signals.artist == "Real Artist"
    assert signals.year == 2015


# ---------------------------------------------------------------------------
# tag_file()
# ---------------------------------------------------------------------------

def _fake_resolve_result(signals, external_ids):
    return SimpleNamespace(signals=signals, external_ids=external_ids)


class _FakeExternalIds:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


def test_tag_file_writes_nfo_on_match(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    path = _touch(tmp_path / "Big Buck Bunny (2008).mp4")
    f = library.LocalMediaFile(path=path, kind="video")

    def fake_resolve(signals, *, max_workers=8):
        merged = Signals(title="Big Buck Bunny", year=2008, medium=MediaType.MOVIE)
        return _fake_resolve_result(merged, _FakeExternalIds({"imdb": "tt1254207"}))

    monkeypatch.setattr(library, "resolve", fake_resolve)
    result = library.tag_file(f, write_nfo=True, dry_run=False)

    assert result.action == "wrote"
    assert result.matched is True
    assert result.external_ids == {"imdb": "tt1254207"}
    nfo_path = path.with_suffix(".nfo")
    assert nfo_path.exists()
    root = ET.fromstring(nfo_path.read_text())
    assert root.tag == "movie"
    assert root.findtext("title") == "Big Buck Bunny"
    ids = {el.get("type"): el.text for el in root.findall("uniqueid")}
    assert ids.get("imdb") == "tt1254207"


def test_tag_file_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    path = _touch(tmp_path / "Big Buck Bunny (2008).mp4")
    f = library.LocalMediaFile(path=path, kind="video")

    def fake_resolve(signals, *, max_workers=8):
        merged = Signals(title="Big Buck Bunny", year=2008, medium=MediaType.MOVIE)
        return _fake_resolve_result(merged, _FakeExternalIds({"imdb": "tt1254207"}))

    monkeypatch.setattr(library, "resolve", fake_resolve)
    result = library.tag_file(f, write_nfo=True, dry_run=True)

    assert result.action == "would-write"
    assert not path.with_suffix(".nfo").exists()


def test_tag_file_resolve_exception_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    path = _touch(tmp_path / "Inception (2010).mkv")
    f = library.LocalMediaFile(path=path, kind="video")

    def fake_resolve(signals, *, max_workers=8):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(library, "resolve", fake_resolve)
    result = library.tag_file(f, write_nfo=True, dry_run=False)

    # Never crashes; falls back to filename-only nfo (has a title so it's
    # written, just unmatched) — no external ids, matched=False.
    assert result.action in ("wrote", "error")
    if result.action == "wrote":
        assert result.matched is False
        assert result.external_ids is None


def test_tag_file_resolve_hard_error_before_signals_never_crashes(tmp_path, monkeypatch):
    """If extract_signals itself blows up, tag_file reports 'error', not a crash."""
    path = _touch(tmp_path / "whatever.mkv")
    f = library.LocalMediaFile(path=path, kind="video")

    def boom(_file):
        raise ValueError("boom")

    monkeypatch.setattr(library, "extract_signals", boom)
    result = library.tag_file(f, write_nfo=True, dry_run=False)
    assert result.action == "error"
    assert not path.with_suffix(".nfo").exists()


def test_tag_file_low_match_falls_back_to_minimal_nfo(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    path = _touch(tmp_path / "Some Obscure Home Video (2019).mkv")
    f = library.LocalMediaFile(path=path, kind="video")

    def fake_resolve(signals, *, max_workers=8):
        return _fake_resolve_result(None, None)

    monkeypatch.setattr(library, "resolve", fake_resolve)
    result = library.tag_file(f, write_nfo=True, dry_run=False)

    assert result.action == "wrote"
    assert result.matched is False
    assert result.external_ids is None
    nfo_path = path.with_suffix(".nfo")
    assert nfo_path.exists()
    root = ET.fromstring(nfo_path.read_text())
    assert root.findtext("title") == "Some Obscure Home Video"


def test_tag_file_never_modifies_media_file_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    content = b"not really a video but bytes must survive"
    path = _touch(tmp_path / "Movie (2001).mkv", content)
    f = library.LocalMediaFile(path=path, kind="video")

    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))
    library.tag_file(f, write_nfo=True, dry_run=False)

    assert path.read_bytes() == content
    # Only the .nfo sidecar was created alongside it.
    siblings = {p.name for p in path.parent.iterdir()}
    assert siblings == {"Movie (2001).mkv", "Movie (2001).nfo"}


def test_tag_file_never_writes_outside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    root = tmp_path / "library"
    path = _touch(root / "Movie (2001).mkv")
    f = library.LocalMediaFile(path=path, kind="video")

    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))
    library.tag_file(f, write_nfo=True, dry_run=False)

    nfo = path.with_suffix(".nfo")
    assert nfo.parent == root
    assert nfo.exists()


def test_episodedetails_nfo_for_tv_file(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    path = _touch(tmp_path / "Show.Name.S01E02.mkv")
    f = library.LocalMediaFile(path=path, kind="video")

    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))
    result = library.tag_file(f, write_nfo=True, dry_run=False)

    assert result.action == "wrote"
    root = ET.fromstring(result.nfo_path.read_text())
    assert root.tag == "episodedetails"
    assert root.findtext("season") == "1"
    assert root.findtext("episode") == "2"


def test_musicvideo_nfo_for_music_file(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_mutagen", None)
    path = _touch(tmp_path / "Artist - Song.mp3")
    f = library.LocalMediaFile(path=path, kind="music")

    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))
    result = library.tag_file(f, write_nfo=True, dry_run=False)

    assert result.action == "wrote"
    root = ET.fromstring(result.nfo_path.read_text())
    assert root.tag == "musicvideo"
    assert root.findtext("artist") == "Artist"


# ---------------------------------------------------------------------------
# tag_library() end-to-end (mocked resolve)
# ---------------------------------------------------------------------------

def test_tag_library_scans_and_tags_a_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    monkeypatch.setattr(library, "_mutagen", None)
    _touch(tmp_path / "Big Buck Bunny (2008).mp4")
    _touch(tmp_path / "Aphex Twin - Avril 14th.mp3")

    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))
    results = library.tag_library(str(tmp_path), dry_run=False)

    assert len(results) == 2
    assert all(r.action == "wrote" for r in results)
    assert (tmp_path / "Big Buck Bunny (2008).nfo").exists()
    assert (tmp_path / "Aphex Twin - Avril 14th.nfo").exists()


def test_tag_library_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_guessit", None)
    _touch(tmp_path / "Big Buck Bunny (2008).mp4")

    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))
    results = library.tag_library(str(tmp_path), dry_run=True)

    assert results[0].action == "would-write"
    assert not (tmp_path / "Big Buck Bunny (2008).nfo").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_tag_library_dry_run(tmp_path, monkeypatch, capsys):
    from media_archivist import cli

    monkeypatch.setattr(library, "_guessit", None)
    _touch(tmp_path / "Big Buck Bunny (2008).mp4")
    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))

    rc = cli.main(["tag-library", "-p", str(tmp_path), "--dry-run"])
    out = capsys.readouterr()
    assert rc == 0
    assert "DRY RUN" in out.err
    assert "would-write" in out.out
    assert "scanned 1" in out.err
    assert not (tmp_path / "Big Buck Bunny (2008).nfo").exists()


def test_cli_tag_library_real_run_writes_nfo(tmp_path, monkeypatch, capsys):
    from media_archivist import cli

    monkeypatch.setattr(library, "_guessit", None)
    _touch(tmp_path / "Big Buck Bunny (2008).mp4")
    monkeypatch.setattr(library, "resolve",
                        lambda signals, **kw: _fake_resolve_result(None, None))

    rc = cli.main(["tag-library", "-p", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "Big Buck Bunny (2008).nfo").exists()
