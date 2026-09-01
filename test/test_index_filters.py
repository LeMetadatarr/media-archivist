"""Index.view() filter combinations — chained predicates."""
from __future__ import annotations

import pytest

from media_archivist.index import Index, WhereError, evaluate_where
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source
from media_archivist.storage import EnvelopeJsonStorage


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "db.json"
    s = EnvelopeJsonStorage(str(p))
    s["a"] = {"source": "youtube", "url": "a", "videoId": "a", "title": "Cats",
              "duration": 60, "tags": ["pets"]}
    s["b"] = {"source": "youtube", "url": "b", "videoId": "b", "title": "Dogs",
              "duration": 600, "tags": ["pets", "long"]}
    s["c"] = {"source": "bandcamp", "url": "c", "title": "Cats Symphony",
              "artist": "Foo", "duration": 240,
              "stream": "https://x/c.mp3"}
    s["d"] = {"source": "youtube_music", "url": "d", "videoId": "d",
              "title": "Cats Anthem", "artist": "Foo", "duration": 180,
              "explicit": True}
    s.store()
    return str(p)


def test_grep_substring(db):
    idx = Index(db)
    out = [e.title for e in idx.view(grep="cats")]
    assert sorted(out) == ["Cats", "Cats Anthem", "Cats Symphony"]


def test_source_filter(db):
    idx = Index(db)
    assert {e.url for e in idx.view(source="youtube")} == {"a", "b"}


def test_has_stream(db):
    idx = Index(db)
    assert [e.url for e in idx.view(has_stream=True)] == ["c"]
    assert "c" not in {e.url for e in idx.view(has_stream=False)}


def test_explicit_flag(db):
    idx = Index(db)
    assert [e.url for e in idx.view(explicit=True)] == ["d"]


def test_where_chains_with_other_filters(db):
    idx = Index(db)
    out = list(idx.view(source="youtube",
                        where="duration < 120 or duration > 500"))
    assert {e.url for e in out} == {"a", "b"}


def test_where_handles_tag_in(db):
    idx = Index(db)
    out = list(idx.view(where='"long" in tags'))
    assert [e.url for e in out] == ["b"]


def test_where_len_function(db):
    idx = Index(db)
    out = list(idx.view(where="len(tags) >= 2"))
    assert [e.url for e in out] == ["b"]


def test_where_attribute_access_denied(db):
    idx = Index(db)
    with pytest.raises(WhereError):
        list(idx.view(where="title.upper() == 'CATS'"))


def test_where_unknown_name(db):
    idx = Index(db)
    with pytest.raises(WhereError):
        list(idx.view(where="frobnicate > 0"))


def test_limit_applies(db):
    idx = Index(db)
    assert len(idx.to_list(limit=2)) == 2


def test_where_arithmetic_all_ops():
    e = MediaEntry.build(source=Source.BANDCAMP, url="u", title="t",
                         raw={}, duration=200)
    assert evaluate_where("duration + 1 == 201", e)
    assert evaluate_where("duration - 1 == 199", e)
    assert evaluate_where("duration * 2 == 400", e)
    assert evaluate_where("duration / 2 == 100", e)
    assert evaluate_where("duration % 3 == 2", e)
    assert evaluate_where("duration // 3 == 66", e)


def test_where_div_by_zero_raises():
    e = MediaEntry.build(source=Source.BANDCAMP, url="u", title="t",
                         raw={}, duration=200)
    with pytest.raises(ZeroDivisionError):
        evaluate_where("duration / 0 == 0", e)


def test_where_type_error_propagates():
    e = MediaEntry.build(source=Source.BANDCAMP, url="u", title="t",
                         raw={}, artist="Foo", duration=200)
    with pytest.raises(TypeError):
        evaluate_where("artist + duration == 0", e)


def test_where_still_rejects_unknown_names(db):
    idx = Index(db)
    with pytest.raises(WhereError):
        list(idx.view(where="frobnicate > 0"))
