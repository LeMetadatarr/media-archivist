"""Canonical view, fingerprint, and dedupe behaviour."""
from __future__ import annotations

import json

from media_archivist.canon import (
    DEFAULT_PREFERENCE,
    build_links,
    dedupe,
    durations_match,
    fingerprint,
    link,
)
from media_archivist.index import Index, WhereError, evaluate_where
from media_archivist.models.canonical import MediaEntry, stable_id
from media_archivist.models.raw import Source
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.views import to_media_entry


def _yt_music(url: str, title: str, artist: str, dur: float) -> dict:
    return {"source": "youtube_music", "url": url, "videoId": url[-3:],
            "title": title, "artist": artist, "duration": dur}


def _bandcamp(url: str, title: str, artist: str, dur: float, stream: str | None = None) -> dict:
    return {"source": "bandcamp", "url": url, "title": title,
            "artist": artist, "duration": dur, "stream": stream}


def test_view_adapter_dispatches_per_source():
    e = to_media_entry({"source": "youtube",
                        "url": "https://www.youtube.com/watch?v=a",
                        "videoId": "a", "title": "T", "duration": 60,
                        "author": "Foo"})
    assert e.source == Source.YOUTUBE
    assert e.duration == 60.0
    assert e.artist == "Foo"


def test_stable_id_is_deterministic():
    assert stable_id(Source.YOUTUBE, "u") == stable_id(Source.YOUTUBE, "u")
    assert stable_id(Source.YOUTUBE, "u") != stable_id(Source.BANDCAMP, "u")


def test_durations_match():
    assert durations_match(240, 241.5)
    assert not durations_match(240, 250)
    assert durations_match(None, 100)  # missing → match
    assert durations_match(None, None)


def test_fingerprint_normalizes_artist_title():
    a = MediaEntry.build(source=Source.BANDCAMP, url="u1",
                         title="Hello World!", raw={}, artist="Foo")
    b = MediaEntry.build(source=Source.YOUTUBE_MUSIC, url="u2",
                         title="hello   world", raw={}, artist="foo")
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_ignores_feat_and_parens():
    a = MediaEntry.build(source=Source.BANDCAMP, url="u1",
                         title="Hello (feat. Bar)", raw={}, artist="Foo")
    b = MediaEntry.build(source=Source.YOUTUBE_MUSIC, url="u2",
                         title="Hello", raw={}, artist="Foo")
    assert fingerprint(a) == fingerprint(b)


def test_build_links_clusters_by_duration(tmp_path):
    db = EnvelopeJsonStorage(str(tmp_path / "db.json"))
    db["a"] = _yt_music("a", "Hello", "Foo", 240)
    db["b"] = _bandcamp("b", "Hello", "Foo", 241.5)
    db["c"] = _yt_music("c", "Hello", "Foo", 600)  # different cut, same name
    entries = [to_media_entry(v) for v in db.values()]
    links = build_links(entries, duration_tolerance=2.0)
    assert any(len(ids) == 2 for ids in links.values())
    # The 600s entry should not be in the same cluster as the 240s entries
    a_id = stable_id(Source.YOUTUBE_MUSIC, "a")
    c_id = stable_id(Source.YOUTUBE_MUSIC, "c")
    for ids in links.values():
        if a_id in ids:
            assert c_id not in ids


def test_dedupe_picks_preferred_source(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _yt_music("a", "Hello", "Foo", 240)
    db["b"] = _bandcamp("b", "Hello", "Foo", 241.5, stream="https://...mp3")
    db.store()

    deduped = dedupe(str(db_path), preference=("bandcamp", "youtube_music"))
    canonical = [e for e in deduped if e.raw.get("alternates")]
    assert len(canonical) == 1
    winner = canonical[0]
    assert winner.source == Source.BANDCAMP
    assert any(alt["source"] == "youtube_music" for alt in winner.raw["alternates"])


def test_link_writes_sidecar(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _yt_music("a", "Hello", "Foo", 240)
    db["b"] = _bandcamp("b", "Hello", "Foo", 241.5)
    db.store()

    links = link(str(db_path))
    sidecar = tmp_path / "db.links.json"
    assert sidecar.exists()
    on_disk = json.loads(sidecar.read_text())
    assert on_disk == links


def test_evaluate_where_supports_basic_ops():
    e = MediaEntry.build(source=Source.BANDCAMP, url="u",
                         title="t", raw={}, artist="Foo", duration=200)
    assert evaluate_where('artist=="Foo" and duration>180', e)
    assert not evaluate_where('duration<100', e)
    assert evaluate_where('len(title)>0', e)


def test_evaluate_where_none_safe():
    """Ordering comparisons with a None field fail closed (do not raise)."""
    e = MediaEntry.build(source=Source.YOUTUBE, url="u", title="t", raw={})
    assert e.duration is None
    assert not evaluate_where('duration>0', e)
    assert evaluate_where('artist==None', e)


def test_evaluate_where_rejects_bad_syntax():
    e = MediaEntry.build(source=Source.YOUTUBE, url="u", title="t", raw={})
    import pytest
    with pytest.raises(WhereError):
        evaluate_where("import os", e)
    with pytest.raises(WhereError):
        evaluate_where("title.upper()", e)  # attribute access denied


def test_index_view_filters_chain(tmp_path):
    db_path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(db_path))
    db["a"] = _bandcamp("a", "Hello", "Foo", 240, stream="s")
    db["b"] = _bandcamp("b", "Hello", "Bar", 240)
    db["c"] = _yt_music("c", "Hello", "Foo", 240)
    db.store()
    idx = Index(str(db_path))
    got = idx.to_list(source="bandcamp", has_stream=True,
                      where='artist=="Foo"')
    assert len(got) == 1 and got[0].url == "a"
