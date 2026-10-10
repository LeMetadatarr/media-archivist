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
    lim.release()
    assert lim.acquire()
    assert slept == [2.0]
    lim.release()

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


# --- single flight, 429, network errors -----------------------------------

def test_overlapping_reads_run_one_at_a_time_and_pause_once(tmp_path):
    import time

    rows = [_row(c * 11) for c in "abcdef"]
    path = _db(tmp_path, *rows)
    lim = ytmeta.MetadataLimiter(0.0, backoff=15 * 60)
    state = {"now": 0, "peak": 0, "reads": 0}
    guard = threading.Lock()

    def extract(video_id):
        with guard:
            state["now"] += 1
            state["reads"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with guard:
            state["now"] -= 1
        raise ytmeta.BotCheckError("Sign in to confirm you're not a bot")

    threads = [threading.Thread(target=ytmeta.fill_entry_lazily, args=(path, r),
                                kwargs={"extract": extract, "limiter": lim}) for r in rows]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["peak"] == 1
    assert 14 * 60 < lim.blocked_for() <= 15 * 60
    assert lim.bot_check() == pytest.approx(lim.blocked_for(), abs=1)
    lim_next = lim._backoff
    assert lim_next == 30 * 60


def test_bot_check_during_a_pause_does_not_double_it():
    lim = _Limiter()
    assert lim.bot_check() == 60 * 15
    lim.now += 60
    assert lim.bot_check() == pytest.approx(60 * 14, abs=1)
    assert lim.blocked_for() == pytest.approx(60 * 14, abs=1)
    lim.now += 60 * 15
    assert lim.bot_check() == 60 * 30


def test_a_read_in_flight_refuses_the_next_one():
    lim = _Limiter()
    assert lim.try_acquire()
    assert not lim.try_acquire()
    lim.release()
    assert lim.try_acquire()


@pytest.mark.parametrize("message", [
    "ERROR: [youtube] x: HTTP Error 429: Too Many Requests",
    "Too many requests, slow down",
])
def test_http_429_is_a_bot_check(message):
    assert ytmeta.is_bot_check(message)


@pytest.mark.parametrize("error", [
    ytmeta.MetadataError("Video unavailable"),
    ytmeta.MetadataError("Private video. Sign in if you've been granted access"),
    ytmeta.MetadataError("This video has been removed by the uploader"),
])
def test_definitive_failures_stamp_the_row(tmp_path, error):
    path = _db(tmp_path, _row("aaaaaaaaaaa"))

    def extract(video_id):
        raise error

    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), extract=extract, limiter=_Limiter())
    assert res.status == "error"
    assert _rows(path)["aaaaaaaaaaa"]["metadata_checked"]


def _urlerror(*a):
    import urllib.error
    return urllib.error.URLError("Name or service not known")


@pytest.mark.parametrize("make", [
    _urlerror,
    lambda *a: __import__("socket").timeout("timed out"),
    lambda *a: ConnectionResetError("reset by peer"),
    lambda *a: RuntimeError("ERROR: Unable to download webpage: <urlopen error [Errno -3]>"),
    lambda *a: ytmeta.MetadataError("yt-dlp failed for u: Unable to download webpage: timed out"),
])
def test_network_errors_do_not_stamp_the_row(tmp_path, make):
    path = _db(tmp_path, _row("aaaaaaaaaaa"))

    def extract(video_id):
        raise make()

    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), extract=extract, limiter=_Limiter())
    assert res.status == "network"
    assert not _rows(path)["aaaaaaaaaaa"].get("metadata_checked")


def test_extract_video_network_errors_are_not_definitive(fake_ytdlp):
    fake_ytdlp.error = "ERROR: Unable to download webpage: <urlopen error timed out>"
    with pytest.raises(ytmeta.MetadataError) as e:
        ytmeta.extract_video("dQw4w9WgXcQ")
    assert ytmeta.is_network_error(e.value)


def test_reenrich_leaves_rows_unstamped_when_the_network_is_down(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"), _row("bbbbbbbbbbb"))
    reads = []

    def extract(video_id):
        reads.append(video_id)
        raise ConnectionError("down")

    result = ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    assert reads == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert result.remaining == 2


# --- HTTP status classification and the re-enrich lock-up ------------------

def _ytdlp_http_error(status, reason):
    """The error yt-dlp raises for an HTTP status: DownloadError around its HTTPError."""
    import io

    from yt_dlp.networking.exceptions import HTTPError
    from yt_dlp.utils import DownloadError

    class _Response:
        def __init__(self):
            self.status = status
            self.reason = reason
            self.url = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
            self.headers = {}
            self.fp = io.BytesIO(b"")

        def read(self, *a):
            return b""

        def close(self):
            pass

    inner = HTTPError(_Response())
    return DownloadError(f"Unable to download webpage: {inner}", exc_info=(HTTPError, inner, None))


@pytest.mark.parametrize("status,reason", [(404, "Not Found"), (410, "Gone"),
                                           (451, "Unavailable For Legal Reasons")])
def test_http_client_errors_are_definitive(tmp_path, status, reason):
    err = _ytdlp_http_error(status, reason)
    assert not ytmeta.is_network_error(err)
    path = _db(tmp_path, _row("aaaaaaaaaaa"))

    def extract(video_id):
        raise ytmeta.MetadataError("yt-dlp failed for u: " + str(err)) from err

    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), extract=extract, limiter=_Limiter())
    assert res.status == "error"
    assert _rows(path)["aaaaaaaaaaa"]["metadata_checked"]


def test_http_503_is_a_network_failure(tmp_path):
    err = _ytdlp_http_error(503, "Service Unavailable")
    assert ytmeta.is_network_error(err)
    path = _db(tmp_path, _row("aaaaaaaaaaa"))

    def extract(video_id):
        raise ytmeta.NetworkError("yt-dlp could not reach u: " + str(err)) from err

    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), extract=extract, limiter=_Limiter())
    assert res.status == "network"
    assert not _rows(path)["aaaaaaaaaaa"].get("metadata_checked")


def test_http_status_in_the_message_alone_decides(tmp_path):
    assert not ytmeta.is_network_error(RuntimeError("Unable to download webpage: HTTP Error 404: Not Found"))
    assert ytmeta.is_network_error(RuntimeError("Unable to download webpage: HTTP Error 502: Bad Gateway"))
    assert ytmeta.is_network_error(RuntimeError("Unable to download webpage"))


def test_extract_video_http_429_is_a_bot_check(fake_ytdlp):
    fake_ytdlp.error = str(_ytdlp_http_error(429, "Too Many Requests"))
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("dQw4w9WgXcQ")


def test_extract_video_http_404_is_definitive(fake_ytdlp):
    fake_ytdlp.error = str(_ytdlp_http_error(404, "Not Found"))
    with pytest.raises(ytmeta.MetadataError) as e:
        ytmeta.extract_video("dQw4w9WgXcQ")
    assert not isinstance(e.value, ytmeta.NetworkError)


def test_a_row_that_keeps_failing_on_the_network_does_not_block_the_rest(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"), _row("bbbbbbbbbbb"), _row("ccccccccccc"))
    reads = []

    def extract(video_id):
        reads.append(video_id)
        if video_id == "aaaaaaaaaaa":
            raise ConnectionError("reset")
        return VIDEO_INFO

    result = ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    rows = _rows(path)
    assert rows["bbbbbbbbbbb"]["duration"] == 213.0 and rows["ccccccccccc"]["duration"] == 213.0
    assert not rows["aaaaaaaaaaa"].get("metadata_checked")
    assert result.remaining == 1

    reads.clear()
    ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    assert reads == ["aaaaaaaaaaa"]


def test_recently_failed_rows_are_read_last(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"), _row("bbbbbbbbbbb"), _row("ccccccccccc"))
    order = []

    def extract(video_id):
        order.append(video_id)
        raise ConnectionError("down")

    ytmeta.reenrich(path, extract=extract, limiter=_Limiter(), limit=1)
    assert order == ["aaaaaaaaaaa"]
    order.clear()
    ytmeta.reenrich(path, extract=extract, limiter=_Limiter(), limit=1)
    assert order == ["bbbbbbbbbbb"]


def test_three_network_failures_in_a_row_stop_the_run(tmp_path):
    path = _db(tmp_path, *(_row(c * 11) for c in "abcde"))
    reads = []

    def extract(video_id):
        reads.append(video_id)
        raise ConnectionError("down")

    result = ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    assert len(reads) == 3
    assert result.remaining == 5


# --- only a positive identification is definitive --------------------------

def _tls_eof_chain():
    """DownloadError -> yt-dlp SSLError -> ssl.SSLEOFError, as a closing TLS server gives."""
    import ssl

    from yt_dlp.networking.exceptions import SSLError
    from yt_dlp.utils import DownloadError

    inner = ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
    try:
        try:
            raise inner
        except ssl.SSLEOFError as e:
            raise SSLError(msg=str(e)) from e
    except SSLError as t:
        return DownloadError(f"Unable to download webpage: {t} (caused by {inner!r})",
                             exc_info=(SSLError, t, None))


def _stamped(tmp_path, error):
    path = _db(tmp_path, _row("aaaaaaaaaaa"))

    def extract(video_id):
        raise error

    ytmeta.fill_row(path, _row("aaaaaaaaaaa"), extract=extract, limiter=_Limiter())
    return bool(_rows(path)["aaaaaaaaaaa"].get("metadata_checked"))


def test_tls_eof_is_not_stamped(tmp_path):
    err = _tls_eof_chain()
    assert ytmeta.is_network_error(err)
    assert not _stamped(tmp_path, err)


def test_tls_eof_against_a_closing_server_is_not_stamped(tmp_path):
    import socket

    import yt_dlp

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    port = s.getsockname()[1]

    def loop():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            c.recv(4096)
            c.close()

    threading.Thread(target=loop, daemon=True).start()
    path = _db(tmp_path, _row("aaaaaaaaaaa"))
    try:
        res = ytmeta.fill_row(
            path, _row("aaaaaaaaaaa"), limiter=_Limiter(),
            extract=lambda v: ytmeta._extract_python(yt_dlp, f"https://127.0.0.1:{port}/{v}", 2))
    finally:
        s.close()
    assert res.status == "network"
    assert not _rows(path)["aaaaaaaaaaa"].get("metadata_checked")


class _Strange(Exception):
    pass


def test_an_unknown_exception_type_is_not_stamped(tmp_path):
    assert not _stamped(tmp_path, _Strange("something nobody has seen"))
    assert not _stamped(tmp_path, KeyError("x"))


@pytest.mark.parametrize("message", [
    "ERROR: [youtube] aaaaaaaaaaa: Video unavailable",
    "ERROR: [youtube] aaaaaaaaaaa: Private video. Sign in if you've been granted access",
    "ERROR: [youtube] aaaaaaaaaaa: This video has been removed by the uploader",
    "ERROR: [youtube] aaaaaaaaaaa: Video unavailable. This video has been deleted",
    "ERROR: [youtube] aaaaaaaaaaa: The uploader's account has been terminated",
    "ERROR: [youtube] aaaaaaaaaaa: The uploader has not made this video available in your country",
])
def test_permanent_messages_are_stamped(tmp_path, message):
    assert _stamped(tmp_path, RuntimeError(message))


def test_binary_stderr_reads_the_status_from_the_error_line_only(monkeypatch):
    import subprocess

    stderr = ("WARNING: [youtube] aaaaaaaaaaa: HTTP Error 404: Not Found. Retrying\n"
              "ERROR: [youtube] aaaaaaaaaaa: Unable to download webpage: timed out\n")

    def run(*a, **k):
        raise subprocess.CalledProcessError(1, "yt-dlp", stderr=stderr)

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    monkeypatch.setattr(ytmeta.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(ytmeta.subprocess, "run", run)
    with pytest.raises(ytmeta.NetworkError):
        ytmeta.extract_video("aaaaaaaaaaa")


def test_binary_stderr_404_error_line_is_definitive(monkeypatch):
    import subprocess

    def run(*a, **k):
        raise subprocess.CalledProcessError(
            1, "yt-dlp", stderr="ERROR: [youtube] a: Unable to download webpage: HTTP Error 404: Not Found\n")

    monkeypatch.setattr(streams, "_import_yt_dlp", lambda: None)
    monkeypatch.setattr(ytmeta.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(ytmeta.subprocess, "run", run)
    with pytest.raises(ytmeta.MetadataError) as e:
        ytmeta.extract_video("aaaaaaaaaaa")
    assert not isinstance(e.value, ytmeta.NetworkError)


def test_extract_video_status_429_without_text_is_a_bot_check(fake_ytdlp, monkeypatch):
    class _Quiet(Exception):
        status = 429

    def boom(*a, **k):
        raise _Quiet("Unable to download webpage")

    monkeypatch.setattr(ytmeta, "_extract_python", boom)
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("dQw4w9WgXcQ")


def test_a_good_row_between_network_failures_resets_the_count(tmp_path):
    path = _db(tmp_path, *(_row(c * 11) for c in "abcdefg"))
    bad = {"a" * 11, "b" * 11, "d" * 11, "e" * 11}
    reads = []

    def extract(video_id):
        reads.append(video_id)
        if video_id in bad:
            raise ConnectionError("down")
        return VIDEO_INFO

    ytmeta.reenrich(path, extract=extract, limiter=_Limiter())
    assert len(reads) == 7


def test_a_success_clears_the_network_failure_mark(tmp_path):
    path = _db(tmp_path, _row("aaaaaaaaaaa"))
    ytmeta.reenrich(path, extract=lambda v: (_ for _ in ()).throw(ConnectionError("x")),
                    limiter=_Limiter())
    assert _rows(path)["aaaaaaaaaaa"][ytmeta.NETWORK_FAILED_KEY]
    ytmeta.reenrich(path, extract=lambda v: VIDEO_INFO, limiter=_Limiter())
    row = _rows(path)["aaaaaaaaaaa"]
    assert ytmeta.NETWORK_FAILED_KEY not in row and row["metadata_checked"]


# --- age gate, more permanent messages, 403 ---------------------------------

AGE_GATE = ("ERROR: [youtube] ageageageag: Sign in to confirm your age. This video may be "
            "inappropriate for some users. Use --cookies-from-browser or --cookies for the "
            "authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
            "  for how to manually pass cookies")


def test_the_age_gate_is_stamped_and_does_not_pause_reads(tmp_path, fake_ytdlp):
    fake_ytdlp.error = AGE_GATE
    path = _db(tmp_path, _row("ageageageag"))
    lim = _Limiter()
    assert lim.try_acquire()
    res = ytmeta.fill_row(path, _row("ageageageag"), limiter=lim)
    assert res.status == "error"
    assert _rows(path)["ageageageag"]["metadata_checked"]
    assert not lim.blocked


@pytest.mark.parametrize("message", [BOT_CHECK, "Sign in to confirm you're not a bot",
                                     "Sign in to confirm you\u2019re not a bot"])
def test_the_not_a_bot_message_is_still_a_bot_check(tmp_path, fake_ytdlp, message):
    fake_ytdlp.error = message
    path = _db(tmp_path, _row("aaaaaaaaaaa"))
    lim = _Limiter()
    assert lim.try_acquire()
    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), limiter=lim)
    assert res.status == "bot-check" and lim.blocked
    assert not _rows(path)["aaaaaaaaaaa"].get("metadata_checked")


@pytest.mark.parametrize("message", [
    "ERROR: [youtube] a: Join this channel to get access to members-only content",
    "ERROR: [youtube] a: This video is for Members only",
    "ERROR: [youtube] a: This video is available to this channel's members on level: Fan",
    "ERROR: [youtube] a: This video requires payment to watch",
    "ERROR: [youtube] a: This video is DRM protected",
    "ERROR: [youtube] a: This live stream recording is not available",
    "ERROR: [youtube] a: This video is no longer available because the uploader has closed their YouTube account",
    "ERROR: [youtube] a: This video isn't available anymore",
    "ERROR: [youtube] a: This video is not available anymore",
])
def test_more_permanent_messages_are_stamped(tmp_path, fake_ytdlp, message):
    fake_ytdlp.error = message
    path = _db(tmp_path, _row("aaaaaaaaaaa"))
    lim = _Limiter()
    assert lim.try_acquire()
    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), limiter=lim)
    assert res.status == "error" and not lim.blocked
    assert _rows(path)["aaaaaaaaaaa"]["metadata_checked"]


def test_http_403_pauses_reads_and_is_not_stamped(tmp_path):
    err = _ytdlp_http_error(403, "Forbidden")
    assert not ytmeta.is_definitive_failure(err) and not ytmeta.is_network_error(err)
    path = _db(tmp_path, _row("aaaaaaaaaaa"))
    lim = _Limiter()
    assert lim.try_acquire()

    def extract(video_id):
        raise err

    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), extract=extract, limiter=lim)
    assert res.status == "bot-check" and lim.blocked
    assert not _rows(path)["aaaaaaaaaaa"].get("metadata_checked")


def test_extract_video_http_403_is_a_bot_check(fake_ytdlp):
    fake_ytdlp.error = str(_ytdlp_http_error(403, "Forbidden"))
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("dQw4w9WgXcQ")


def test_extract_video_age_gate_is_a_plain_failure(fake_ytdlp):
    fake_ytdlp.error = AGE_GATE
    with pytest.raises(ytmeta.MetadataError) as e:
        ytmeta.extract_video("ageageageag")
    assert not isinstance(e.value, (ytmeta.BotCheckError, ytmeta.NetworkError))


# --- bot wording outranks "Video unavailable" -------------------------------

BLOCKED = "ERROR: [youtube] aaaaaaaaaaa: Video unavailable. This content isn’t available."


@pytest.mark.parametrize("message", [
    BLOCKED,
    BLOCKED.replace("’", "'"),
    "ERROR: [youtube] a: Video unavailable. Sign in to confirm you're not a bot. Use --cookies",
])
def test_a_blocked_session_is_a_bot_check_not_a_stamp(tmp_path, fake_ytdlp, message):
    fake_ytdlp.error = message
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("aaaaaaaaaaa")
    path = _db(tmp_path, _row("aaaaaaaaaaa"))
    lim = _Limiter()
    assert lim.try_acquire()
    res = ytmeta.fill_row(path, _row("aaaaaaaaaaa"), limiter=lim)
    assert res.status == "bot-check" and lim.blocked
    assert not _rows(path)["aaaaaaaaaaa"].get("metadata_checked")


def test_age_gate_through_extract_video_is_permanent_and_does_not_pause(fake_ytdlp):
    fake_ytdlp.error = AGE_GATE
    with pytest.raises(ytmeta.MetadataError) as e:
        ytmeta.extract_video("ageageageag")
    assert not isinstance(e.value, ytmeta.BotCheckError)
    assert ytmeta.failure_kind(e.value) == "permanent"


def test_a_permanent_message_inside_a_403_chain_is_permanent():
    err = _ytdlp_http_error(403, "Forbidden")
    err.msg = "ERROR: [youtube] a: Video unavailable. This video has been removed by the uploader"
    wrapped = RuntimeError(err.msg)
    wrapped.__cause__ = err
    assert ytmeta.failure_kind(wrapped) == "permanent"


def test_plain_403_through_extract_video_is_a_bot_check(fake_ytdlp):
    fake_ytdlp.error = str(_ytdlp_http_error(403, "Forbidden"))
    with pytest.raises(ytmeta.BotCheckError):
        ytmeta.extract_video("aaaaaaaaaaa")


def test_bot_markers_stay_narrow():
    assert not any(m in ("sign in to confirm you", "sign in") for m in ytmeta.BOT_CHECK_MARKERS)
    assert not ytmeta.is_bot_check("Sign in to confirm your age")
    assert not ytmeta.is_bot_check("Private video. Sign in if you've been granted access")
