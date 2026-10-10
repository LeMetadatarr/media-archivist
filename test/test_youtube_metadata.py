# SPDX-License-Identifier: Apache-2.0
"""YouTube rows: no junk from listings, gaps filled from yt-dlp, bot check respected.

tutubo's channel listing puts whatever text sat next to the view count into
``published_time`` ("DUST", "121K", "3 years ago") and has no duration or
channel per video. Those values must never be stored or served; the gaps are
filled from a single-video yt-dlp read. ``yt_dlp_video_dQw4w9WgXcQ.json`` is
a recorded ``extract_info(process=False)`` response trimmed to the fields
used; ``yt_dlp_bot_check_error.txt`` is the error text yt-dlp builds when
YouTube answers with its bot check (reason plus yt-dlp's cookie hint).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import media_archivist.youtube as youtube_mod
from media_archivist import streams, ytmeta
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.views import to_media_entry
from media_archivist.youtube import YoutubeArchivist

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_INFO = json.loads((FIXTURES / "yt_dlp_video_dQw4w9WgXcQ.json").read_text())
BOT_CHECK = (FIXTURES / "yt_dlp_bot_check_error.txt").read_text().strip()
FLAT_CHANNEL = json.loads((FIXTURES / "yt_dlp_flat_channel.json").read_text())


class _TutuboVideo:
    """A video as tutubo's channel page parser builds it."""

    def __init__(self, video_id, title, published_time="", view_count=""):
        self.video_id = video_id
        self.watch_url = f"https://www.youtube.com/watch?v={video_id}"
        self.title = title
        self.thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        self.published_time = published_time
        self.view_count = view_count
        self.description = ""
        self.keywords = []
        self.is_live = False


def _tutubo_listing(videos, channel_name="Some Channel"):
    class _Listing:
        title = None

        def __init__(self, url):
            self.videos = list(videos)
            self.channel_name = channel_name

    return _Listing


def _row(video_id="dQw4w9WgXcQ", **fields):
    row = {
        "source": "youtube",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "videoId": video_id,
        "title": "t",
        "published": "",
        "duration": None,
        "author": None,
    }
    row.update(fields)
    return row


def _db(tmp_path, *rows):
    path = str(tmp_path / "db.json")
    db = EnvelopeJsonStorage(path)
    for r in rows:
        db[r["url"]] = r
    db.store()
    return path


def _rows(path):
    return {r["videoId"]: r for r in EnvelopeJsonStorage(path).values()}


class _Limiter(ytmeta.MetadataLimiter):
    """No spacing between reads; a fixed clock for the back-off."""

    def __init__(self):
        self.now = 1000.0
        super().__init__(0.0, clock=lambda: self.now, sleep=lambda s: None)


# --- listing values -------------------------------------------------------

@pytest.mark.parametrize("junk", ["DUST", "121K", "121K views", "3 years ago",
                                  "Streamed 2 days ago", "2024-13-40", "  "])
def test_tutubo_junk_published_is_not_stored(tmp_path, monkeypatch, junk):
    video = _TutuboVideo("aaaaaaaaaaa", "A", published_time=junk, view_count="121K views")
    monkeypatch.setattr(youtube_mod, "Channel", _tutubo_listing([video]))
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@somechannel")

    row = _rows(archivist.db.path)["aaaaaaaaaaa"]
    assert row["published"] == ""
    assert row["duration"] is None


def test_tutubo_channel_listing_names_the_channel(tmp_path, monkeypatch):
    video = _TutuboVideo("aaaaaaaaaaa", "A", published_time="DUST")
    monkeypatch.setattr(youtube_mod, "Channel", _tutubo_listing([video], "NASA"))
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@NASA")

    row = _rows(archivist.db.path)["aaaaaaaaaaa"]
    assert row["author"] == "NASA"
    assert "author" not in row["extra"]


def test_real_dates_from_a_listing_are_kept(tmp_path, monkeypatch):
    video = _TutuboVideo("aaaaaaaaaaa", "A", published_time="2021-05-04")
    monkeypatch.setattr(youtube_mod, "Channel", _tutubo_listing([video]))
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@somechannel")

    assert _rows(archivist.db.path)["aaaaaaaaaaa"]["published"] == "2021-05-04"


def test_flat_channel_listing_fills_channel_from_the_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube_mod, "Channel", _tutubo_listing([]))
    monkeypatch.setattr(streams, "list_playlist", lambda url, **kw: FLAT_CHANNEL)
    archivist = YoutubeArchivist(db_path=str(tmp_path / "db.json"))

    archivist.archive("https://www.youtube.com/@NASA")

    rows = _rows(archivist.db.path).values()
    assert rows
    assert {r["author"] for r in rows} == {"NASA"}
    assert any(r["duration"] for r in rows)
    assert all(r["duration"] is None or r["duration"] > 0 for r in rows)


def test_stored_junk_is_not_served():
    entry = to_media_entry(_row(published="121K", author="  ", duration=0))
    assert entry.published is None
    assert entry.artist is None
    assert entry.duration is None
    assert to_media_entry(_row(published="20091025")).published == "2009-10-25"


@pytest.mark.parametrize("value,expected", [
    ("2009-10-25", "2009-10-25"),
    ("2009-10-25T06:57:33", "2009-10-25T06:57:33"),
    ("20091025", "2009-10-25"),
    ("2009-02-30", ""),
    ("DUST", ""),
    ("121K", ""),
    ("5 hours ago", ""),
    (None, ""),
    (20091025, ""),
])
def test_clean_published(value, expected):
    assert ytmeta.clean_published(value) == expected


@pytest.mark.parametrize("value,expected", [
    (213, 213.0), (1.5, 1.5), (0, None), (-3, None), (True, None),
    ("213", None), (float("nan"), None), (None, None),
])
def test_clean_duration(value, expected):
    assert ytmeta.clean_duration(value) == expected


# --- single-video extraction ---------------------------------------------

class _FakeYDL:
    calls = []
    error = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False, process=True):
        type(self).calls.append((url, download, process, dict(self.opts)))
        if type(self).error:
            raise Exception(type(self).error)
        return dict(VIDEO_INFO)


@pytest.fixture
def fake_ytdlp(monkeypatch):
    _FakeYDL.calls = []
    _FakeYDL.error = None

    class _Module:
        YoutubeDL = _FakeYDL

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: _Module)
    return _FakeYDL


def test_extract_video_reads_one_video_without_cookies(fake_ytdlp):
    info = ytmeta.extract_video("dQw4w9WgXcQ")

    assert ytmeta.fields_from_info(info) == {
        "duration": 213.0, "author": "Rick Astley", "published": "2009-10-25",
    }
    (url, download, process, opts), = fake_ytdlp.calls
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert download is False and process is False
    assert opts["noplaylist"] is True
    assert not {"cookiefile", "cookiesfrombrowser", "username", "password"} & set(opts)


def test_extract_video_reports_the_bot_check(fake_ytdlp):
    fake_ytdlp.error = BOT_CHECK
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("dQw4w9WgXcQ")


def test_extract_video_other_failures_are_not_bot_checks(fake_ytdlp):
    fake_ytdlp.error = "ERROR: [youtube] dQw4w9WgXcQ: Video unavailable"
    with pytest.raises(ytmeta.MetadataError) as e:
        ytmeta.extract_video("dQw4w9WgXcQ")
    assert not isinstance(e.value, ytmeta.BotCheckError)


def test_published_falls_back_to_timestamp():
    assert ytmeta.published_from_info({"timestamp": 1256453853}) == "2009-10-25"
    assert ytmeta.published_from_info({"upload_date": "x", "timestamp": None}) == ""


# --- limiter ------------------------------------------------------------

def test_limiter_spaces_reads_and_backs_off_doubling():
    clock = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        clock["now"] += s

    lim = ytmeta.MetadataLimiter(2.0, backoff=60, max_backoff=200,
                                 clock=lambda: clock["now"], sleep=sleep)
    assert lim.try_acquire()
    assert not lim.try_acquire()
    assert lim.acquire()
    assert slept == [2.0]

    assert lim.bot_check() == 60
    assert lim.blocked and not lim.try_acquire() and not lim.acquire()
    clock["now"] += 61
    assert not lim.blocked
    assert lim.bot_check() == 120
    clock["now"] += 121
    assert lim.bot_check() == 200
    clock["now"] += 201
    lim.success()
    assert lim.bot_check() == 60


# --- re-enrich ------------------------------------------------------------

def test_reenrich_fills_rows_and_cleans_junk(tmp_path):
    path = _db(tmp_path, _row(published="121K"), _row("bbbbbbbbbbb", duration=60.0,
               author="Kept", published="2020-01-01"))
    reads = []

    def extract(video_id):
        reads.append(video_id)
        return VIDEO_INFO

    result = ytmeta.reenrich(path, extract=extract, limiter=_Limiter())

    assert reads == ["dQw4w9WgXcQ"]
    row = _rows(path)["dQw4w9WgXcQ"]
    assert (row["duration"], row["author"], row["published"]) == (213.0, "Rick Astley", "2009-10-25")
    assert row["metadata_checked"]
    assert _rows(path)["bbbbbbbbbbb"]["author"] == "Kept"
    assert (result.updated, result.sanitized, result.remaining) == (1, 1, 0)

    reads.clear()
    ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    assert reads == []


def test_reenrich_never_overwrites_good_values(tmp_path):
    path = _db(tmp_path, _row(author="Uploader Name", published="2001-01-01"))
    ytmeta.reenrich(path, extract=lambda vid: VIDEO_INFO, limiter=_Limiter())
    row = _rows(path)["dQw4w9WgXcQ"]
    assert (row["author"], row["published"], row["duration"]) == ("Uploader Name", "2001-01-01", 213.0)


def test_reenrich_stops_at_bot_check_and_leaves_fields_empty(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"), _row("bbbbbbbbbbb"), _row("ccccccccccc"))
    reads = []

    def extract(video_id):
        reads.append(video_id)
        raise ytmeta.BotCheckError(BOT_CHECK)

    lim = _Limiter()
    result = ytmeta.reenrich(path, extract=extract, limiter=lim)

    assert len(reads) == 1
    assert result.bot_check and result.updated == 0 and result.remaining == 3
    assert lim.blocked
    for row in _rows(path).values():
        assert row["duration"] is None and row["author"] is None and row["published"] == ""
        assert not row.get("metadata_checked")

    reads.clear()
    assert ytmeta.reenrich(path, extract=extract, limiter=lim).bot_check
    assert reads == []


def test_reenrich_marks_unreadable_videos_and_moves_on(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"), _row("bbbbbbbbbbb"))

    def extract(video_id):
        if video_id == "aaaaaaaaaaa":
            raise ytmeta.MetadataError("Video unavailable")
        return VIDEO_INFO

    result = ytmeta.reenrich(path, extract=extract, limiter=_Limiter())

    rows = _rows(path)
    assert rows["aaaaaaaaaaa"]["metadata_checked"] and rows["aaaaaaaaaaa"]["duration"] is None
    assert rows["bbbbbbbbbbb"]["duration"] == 213.0
    assert (result.errors, result.updated, result.remaining) == (1, 1, 0)


def test_reenrich_respects_limit_and_stop(tmp_path):
    path = _db(tmp_path, *(_row(c * 11) for c in "abcde"))
    assert ytmeta.reenrich(path, limit=2, extract=lambda v: VIDEO_INFO,
                           limiter=_Limiter()).checked == 2
    stop = threading.Event()
    stop.set()
    assert ytmeta.reenrich(path, extract=lambda v: VIDEO_INFO, limiter=_Limiter(),
                           stop=stop).checked == 0


def test_reenrich_keeps_rows_written_meanwhile(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"))

    def extract(video_id):
        db = EnvelopeJsonStorage(path)
        new = _row("zzzzzzzzzzz", duration=5.0, author="x", published="2000-01-01")
        db[new["url"]] = new
        db.store()
        return VIDEO_INFO

    ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    assert set(_rows(path)) == {"aaaaaaaaaaa", "zzzzzzzzzzz"}


def test_non_youtube_rows_are_left_alone(tmp_path):
    other = {"source": "soundcloud", "url": "https://soundcloud.com/a/b", "title": "x",
             "duration": None}
    path = _db(tmp_path, other)
    result = ytmeta.reenrich(path, extract=lambda v: pytest.fail("no read"), limiter=_Limiter())
    assert result.checked == 0
    assert len(EnvelopeJsonStorage(path)) == 1
