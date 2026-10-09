# SPDX-License-Identifier: Apache-2.0
"""Jellyfin / Kodi movie layout for downloads: folder, movie.nfo, poster."""
from __future__ import annotations

import itertools
import socket
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import requests

from media_archivist import movie_layout, streams
from media_archivist.cli import build_parser, main
from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source
from mediavocab.models import ExternalIds

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _entry(**overrides) -> MediaEntry:
    fields = dict(
        source=Source.YOUTUBE,
        url=URL,
        title="Never Gonna Give You Up",
        raw={"videoId": "dQw4w9WgXcQ", "description": "The official video."},
        artist="Rick Astley",
        published="2009-10-25T06:57:33",
        duration=213.0,
        thumbnail="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        tags=["pop"],
    )
    fields.update(overrides)
    return MediaEntry.build(**fields)


class _Resp:
    def __init__(self, body=PNG, content_type="image/jpeg", status=200):
        self._body, self.headers, self.status_code = body, {"Content-Type": content_type}, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, size):
        size = size or 64 * 1024
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


@pytest.fixture
def poster(monkeypatch):
    calls = []

    def _get(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(movie_layout.requests, "get", _get)
    return calls


@pytest.fixture
def downloaded(tmp_path):
    f = tmp_path / "Never Gonna Give You Up [dQw4w9WgXcQ].mkv"
    f.write_bytes(b"video")
    return f


def test_files_into_title_year_folder_with_nfo_and_poster(tmp_path, downloaded, poster):
    out = movie_layout.file_as_movie(downloaded, _entry(), tmp_path)

    folder = tmp_path / "Never Gonna Give You Up (2009)"
    assert out == folder / "Never Gonna Give You Up (2009).mkv"
    assert out.read_bytes() == b"video"
    assert not downloaded.exists()
    assert (folder / "poster.jpg").read_bytes() == PNG
    assert sorted(p.name for p in folder.iterdir()) == [
        "Never Gonna Give You Up (2009).mkv", "movie.nfo", "poster.jpg"]
    assert poster == ["https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"]


def test_movie_nfo_content(tmp_path, downloaded, poster):
    entry = _entry(external_ids=ExternalIds(imdb="tt0000001", tmdb_movie=603))
    movie_layout.file_as_movie(downloaded, entry, tmp_path)

    root = ET.parse(tmp_path / "Never Gonna Give You Up (2009)" / "movie.nfo").getroot()
    assert root.tag == "movie"
    assert root.findtext("title") == "Never Gonna Give You Up"
    assert root.findtext("year") == "2009"
    assert root.findtext("premiered") == "2009-10-25"
    assert root.findtext("plot") == "The official video."
    assert root.findtext("studio") == "Rick Astley"
    assert root.findtext("runtime") == "4"
    ids = {u.get("type"): u.text for u in root.findall("uniqueid")}
    assert ids == {"youtube": "dQw4w9WgXcQ", "imdb": "tt0000001", "tmdb": "603",
                   "media-archivist": entry.id}


def test_unknown_year_names_without_year(tmp_path, downloaded, poster):
    out = movie_layout.file_as_movie(downloaded, _entry(published="3 years ago"), tmp_path)
    assert out == tmp_path / "Never Gonna Give You Up" / "Never Gonna Give You Up.mkv"


def test_unsafe_title_characters_are_removed(tmp_path, downloaded, poster):
    out = movie_layout.file_as_movie(downloaded, _entry(title="../a/b: c?"), tmp_path)
    assert out.parent.parent == tmp_path
    assert "/" not in out.parent.name and ".." not in out.parent.name


def test_poster_extension_follows_url_or_content_type(tmp_path, downloaded, monkeypatch):
    monkeypatch.setattr(movie_layout.requests, "get",
                        lambda url, **kw: _Resp(content_type="image/webp"))
    entry = _entry(thumbnail="https://i.ytimg.com/vi/x/thumb")
    out = movie_layout.file_as_movie(downloaded, entry, tmp_path)
    assert (out.parent / "poster.webp").exists()


def test_poster_failure_still_files_video_and_nfo(tmp_path, downloaded, monkeypatch):
    def _boom(url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(movie_layout.requests, "get", _boom)
    out = movie_layout.file_as_movie(downloaded, _entry(), tmp_path)
    assert out.exists() and (out.parent / "movie.nfo").exists()
    assert not list(out.parent.glob("poster.*"))


def test_http_error_poster_is_skipped(tmp_path, downloaded, monkeypatch):
    monkeypatch.setattr(movie_layout.requests, "get", lambda url, **kw: _Resp(status=404))
    out = movie_layout.file_as_movie(downloaded, _entry(), tmp_path)
    assert not list(out.parent.glob("poster.*"))


def test_oversized_poster_is_skipped(tmp_path, downloaded, monkeypatch):
    monkeypatch.setattr(movie_layout, "_POSTER_MAX_BYTES", 10)
    monkeypatch.setattr(movie_layout.requests, "get", lambda url, **kw: _Resp())
    out = movie_layout.file_as_movie(downloaded, _entry(), tmp_path)
    assert not list(out.parent.glob("poster.*"))


@pytest.mark.parametrize("thumb", [None, "file:///etc/passwd", "javascript:alert(1)"])
def test_non_http_thumbnail_is_never_fetched(tmp_path, downloaded, poster, thumb):
    out = movie_layout.file_as_movie(downloaded, _entry(thumbnail=thumb), tmp_path)
    assert poster == []
    assert not list(out.parent.glob("poster.*"))


def test_music_entry_is_left_where_it_was(tmp_path, downloaded, poster):
    entry = _entry(source=Source.BANDCAMP, raw={})
    assert movie_layout.file_as_movie(downloaded, entry, tmp_path) == downloaded
    assert downloaded.exists()
    assert poster == []


def _other(video_id, **kw):
    return _entry(url=f"https://www.youtube.com/watch?v={video_id}",
                  raw={"videoId": video_id}, **kw)


def _download_file(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(name.encode())
    return f


def test_same_title_and_year_do_not_overwrite_each_other(tmp_path, poster):
    first = _other("AAAAAAAAAAA")
    second = _other("BBBBBBBBBBB")
    out1 = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"), first, tmp_path)
    out2 = movie_layout.file_as_movie(_download_file(tmp_path, "two.mkv"), second, tmp_path)

    assert out1 == tmp_path / "Never Gonna Give You Up (2009)" / "Never Gonna Give You Up (2009).mkv"
    assert out2 == (tmp_path / "Never Gonna Give You Up (2009) [BBBBBBBBBBB]"
                    / "Never Gonna Give You Up (2009) [BBBBBBBBBBB].mkv")
    assert out1.read_bytes() == b"one.mkv" and out2.read_bytes() == b"two.mkv"
    nfo1 = ET.parse(out1.parent / "movie.nfo").getroot()
    nfo2 = ET.parse(out2.parent / "movie.nfo").getroot()
    assert nfo1.find("uniqueid").text == "AAAAAAAAAAA"
    assert nfo2.find("uniqueid").text == "BBBBBBBBBBB"


def test_refiling_the_same_entry_never_overwrites_its_file(tmp_path, poster):
    entry = _other("AAAAAAAAAAA")
    out1 = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"), entry, tmp_path)
    again = _download_file(tmp_path, "again.mkv")
    with pytest.raises(FileExistsError):
        movie_layout.file_as_movie(again, entry, tmp_path)
    assert out1.read_bytes() == b"one.mkv"
    assert again.read_bytes() == b"again.mkv"
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1


def test_refiling_with_another_extension_replaces_only_our_old_video(tmp_path, poster):
    entry = _other("AAAAAAAAAAA")
    out1 = movie_layout.file_as_movie(_download_file(tmp_path, "one.mp4"), entry, tmp_path)
    folder = out1.parent
    (folder / "extras.txt").write_text("not ours")
    (folder / "Other Name.mkv").write_bytes(b"not ours either")

    out2 = movie_layout.file_as_movie(_download_file(tmp_path, "two.webm"), entry, tmp_path)

    assert out2.parent == folder and out2.suffix == ".webm"
    assert out2.read_bytes() == b"two.webm"
    assert not out1.exists()
    assert (folder / "extras.txt").read_text() == "not ours"
    assert (folder / "Other Name.mkv").read_bytes() == b"not ours either"
    assert len([p for p in folder.iterdir() if p.suffix in {".mp4", ".webm", ".mkv"}]) == 2


def _stranger(tmp_path):
    """A user's own copy of the film, with an NFO of its own name."""
    folder = tmp_path / "Never Gonna Give You Up (2009)"
    folder.mkdir()
    (folder / "Never Gonna Give You Up (2009).mkv").write_bytes(b"users own copy")
    (folder / "Never Gonna Give You Up (2009).nfo").write_text("<movie/>")
    return folder


def test_folder_with_files_but_no_movie_nfo_is_never_overwritten(tmp_path, poster):
    folder = _stranger(tmp_path)
    entry = _other("AAAAAAAAAAA")
    out = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"), entry, tmp_path)
    assert out.parent == tmp_path / "Never Gonna Give You Up (2009) [AAAAAAAAAAA]"
    assert out.read_bytes() == b"one.mkv"
    assert (folder / "Never Gonna Give You Up (2009).mkv").read_bytes() == b"users own copy"
    assert (folder / "Never Gonna Give You Up (2009).nfo").read_text() == "<movie/>"
    assert not (folder / "movie.nfo").exists()


def test_empty_existing_folder_is_used(tmp_path, poster):
    (tmp_path / "Never Gonna Give You Up (2009)").mkdir()
    out = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"),
                                     _other("AAAAAAAAAAA"), tmp_path)
    assert out.parent == tmp_path / "Never Gonna Give You Up (2009)"


def test_both_folder_names_taken_fails_without_touching_either(tmp_path, poster):
    folder = _stranger(tmp_path)
    variant = tmp_path / "Never Gonna Give You Up (2009) [AAAAAAAAAAA]"
    variant.mkdir()
    (variant / "x.mkv").write_bytes(b"also theirs")
    dl = _download_file(tmp_path, "one.mkv")
    with pytest.raises(FileExistsError):
        movie_layout.file_as_movie(dl, _other("AAAAAAAAAAA"), tmp_path)
    assert dl.read_bytes() == b"one.mkv"
    assert (variant / "x.mkv").read_bytes() == b"also theirs"
    assert sorted(p.name for p in folder.iterdir()) == [
        "Never Gonna Give You Up (2009).mkv", "Never Gonna Give You Up (2009).nfo"]


def test_filing_that_died_before_the_move_is_recognised_as_ours(tmp_path, poster, monkeypatch):
    entry = _other("AAAAAAAAAAA")
    dl = _download_file(tmp_path, "one.mkv")
    real_move = movie_layout.shutil.move

    def _die(*a, **kw):
        raise OSError("disk went away")

    monkeypatch.setattr(movie_layout.shutil, "move", _die)
    with pytest.raises(OSError):
        movie_layout.file_as_movie(dl, entry, tmp_path)
    monkeypatch.setattr(movie_layout.shutil, "move", real_move)

    out = movie_layout.file_as_movie(dl, entry, tmp_path)
    assert out.parent == tmp_path / "Never Gonna Give You Up (2009)"
    assert out.read_bytes() == b"one.mkv"
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1


def test_nfo_carries_the_entry_id(tmp_path, downloaded, poster):
    entry = _entry()
    out = movie_layout.file_as_movie(downloaded, entry, tmp_path)
    ids = {u.get("type"): u.text
           for u in ET.parse(out.parent / "movie.nfo").getroot().findall("uniqueid")}
    assert ids["media-archivist"] == entry.id


def _folders(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir() if p.is_dir())


def test_entry_that_gained_an_imdb_id_keeps_its_folder(tmp_path, poster):
    before = _other("AAAAAAAAAAA")
    after = _other("AAAAAAAAAAA", external_ids=ExternalIds(imdb="tt0000001"))
    assert before.id == after.id
    movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"), before, tmp_path)
    with pytest.raises(FileExistsError):
        movie_layout.file_as_movie(_download_file(tmp_path, "two.mkv"), after, tmp_path)
    assert _folders(tmp_path) == ["Never Gonna Give You Up (2009)"]


def test_entry_whose_description_changed_keeps_its_folder(tmp_path, poster):
    ia = dict(source=Source.INTERNET_ARCHIVE, url="https://archive.org/details/a")
    first = _entry(raw={"description": "old"}, **ia)
    second = _entry(raw={"description": "new"}, **ia)
    movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"), first, tmp_path)
    with pytest.raises(FileExistsError):
        movie_layout.file_as_movie(_download_file(tmp_path, "two.mkv"), second, tmp_path)
    assert _folders(tmp_path) == ["Never Gonna Give You Up (2009)"]


def _legacy_folder(tmp_path, *uniqueids):
    folder = tmp_path / "Never Gonna Give You Up (2009)"
    folder.mkdir()
    body = "".join(f'<uniqueid type="{t}">{v}</uniqueid>' for t, v in uniqueids)
    (folder / "movie.nfo").write_text(f"<movie><title>x</title>{body}</movie>")
    return folder


def test_radarr_style_movie_nfo_sharing_ids_is_never_overwritten(tmp_path, poster):
    folder = tmp_path / "Never Gonna Give You Up (2009)"
    folder.mkdir()
    video = folder / "Never Gonna Give You Up (2009).mkv"
    video.write_bytes(b"users own copy")
    nfo = folder / "movie.nfo"
    nfo.write_text(
        '<movie><title>x</title><plot>my own plot</plot>'
        '<uniqueid type="tmdb" default="true">10331</uniqueid>'
        '<uniqueid type="imdb">tt0063350</uniqueid></movie>')
    before = (video.read_bytes(), nfo.read_bytes(), sorted(p.name for p in folder.iterdir()))
    entry = _other("AAAAAAAAAAA", external_ids=ExternalIds(imdb="tt0063350"))

    out = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"), entry, tmp_path)

    assert out.parent == tmp_path / "Never Gonna Give You Up (2009) [AAAAAAAAAAA]"
    assert (video.read_bytes(), nfo.read_bytes(),
            sorted(p.name for p in folder.iterdir())) == before


def test_nfo_without_entry_id_and_other_provider_ids_is_another_entry(tmp_path, poster):
    folder = _legacy_folder(tmp_path, ("youtube", "BBBBBBBBBBB"))
    out = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"),
                                     _other("AAAAAAAAAAA"), tmp_path)
    assert out.parent != folder
    assert not (folder / "Never Gonna Give You Up (2009).mkv").exists()


def test_nfo_with_another_entry_id_is_another_entry_even_if_ids_overlap(tmp_path, poster):
    folder = _legacy_folder(tmp_path, ("youtube", "AAAAAAAAAAA"),
                            ("media-archivist", "someone-else"))
    out = movie_layout.file_as_movie(_download_file(tmp_path, "one.mkv"),
                                     _other("AAAAAAAAAAA"), tmp_path)
    assert out.parent != folder


def _trickle_server(declared, interval, stop):
    """A local HTTP server that declares ``declared`` bytes and sends one per ``interval`` s."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def _serve():
        srv.settimeout(10)
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                         b"Content-Length: %d\r\n\r\n" % declared)
            try:
                while not stop.is_set():
                    conn.sendall(b"x")
                    stop.wait(interval)
            except OSError:
                pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return srv, t


def test_poster_fetch_has_an_overall_deadline_on_a_real_slow_socket(
        tmp_path, downloaded, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(movie_layout, "_POSTER_DEADLINE_S", 1)
    stop = threading.Event()
    srv, thread = _trickle_server(5 * 1024 * 1024, 2.0, stop)
    result = {}

    def _call():
        entry = _entry(thumbnail=f"http://127.0.0.1:{srv.getsockname()[1]}/p.jpg")
        started = time.monotonic()
        result["out"] = movie_layout.file_as_movie(downloaded, entry, tmp_path)
        result["elapsed"] = time.monotonic() - started

    caller = threading.Thread(target=_call, daemon=True)
    caller.start()
    try:
        caller.join(1 + 3)   # guard: a regression fails here instead of hanging
        assert not caller.is_alive(), "poster fetch outlived its deadline"
    finally:
        stop.set()
        srv.close()
        thread.join(5)
    out, elapsed = result["out"], result["elapsed"]
    assert elapsed < 1 + 1.5
    assert out.exists() and (out.parent / "movie.nfo").exists()
    assert not list(out.parent.glob("poster.*"))


def _header_trickle_server(interval, stop):
    """A local HTTP server that sends the status line and headers, one byte
    per ``interval`` s, and never finishes them."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def _serve():
        srv.settimeout(30)
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)
            try:
                for b in itertools.chain(b"HTTP/1.1 200 OK\r\nX-Pad: ", itertools.repeat(97)):
                    if stop.is_set():
                        return
                    conn.sendall(bytes([b]))
                    stop.wait(interval)
            except OSError:
                pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return srv, thread


def test_poster_deadline_covers_connect_and_headers_on_a_real_socket(
        tmp_path, downloaded, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(movie_layout, "_POSTER_DEADLINE_S", 1)
    stop = threading.Event()
    srv, server_thread = _header_trickle_server(2.0, stop)
    result = {}

    def _call():
        entry = _entry(thumbnail=f"http://127.0.0.1:{srv.getsockname()[1]}/p.jpg")
        started = time.monotonic()
        result["out"] = movie_layout.file_as_movie(downloaded, entry, tmp_path)
        result["elapsed"] = time.monotonic() - started

    caller = threading.Thread(target=_call, daemon=True)
    caller.start()
    try:
        caller.join(1 + 3)   # guard: a regression fails here instead of hanging
        assert not caller.is_alive(), "poster fetch outlived its deadline"
    finally:
        stop.set()
        srv.close()
        server_thread.join(5)
    assert result["elapsed"] < 1 + 1.5
    out = result["out"]
    assert out.exists() and (out.parent / "movie.nfo").exists()
    time.sleep(0.3)
    assert not list(out.parent.glob("poster.*"))


def test_abandoned_poster_fetch_cannot_write_the_file_late(tmp_path, downloaded, monkeypatch):
    monkeypatch.setattr(movie_layout, "_POSTER_DEADLINE_S", 0.2)
    release = threading.Event()

    def _slow(url, **kw):
        release.wait(5)
        return _Resp()

    monkeypatch.setattr(movie_layout.requests, "get", _slow)
    out = movie_layout.file_as_movie(downloaded, _entry(), tmp_path)
    release.set()
    time.sleep(0.3)
    assert not list(out.parent.glob("poster.*"))


@pytest.mark.parametrize("thumb", [
    "http://[::1/p.jpg",
    "http://" + "a" * 70 + ".example.com/p.jpg",
])
def test_malformed_thumbnail_url_does_not_fail_the_filing(tmp_path, downloaded, thumb):
    out = movie_layout.file_as_movie(downloaded, _entry(thumbnail=thumb), tmp_path)
    assert out.exists() and (out.parent / "movie.nfo").exists()
    assert not list(out.parent.glob("poster.*"))


def test_extension_does_not_change_the_folder_of_a_long_title(tmp_path, poster):
    entry = _other("AAAAAAAAAAA", title="1" + "\u65e5" * 99)
    out1 = movie_layout.file_as_movie(_download_file(tmp_path, "a.mp4"), entry, tmp_path)
    out2 = movie_layout.file_as_movie(_download_file(tmp_path, "b.webm"), entry, tmp_path)
    assert out1.parent == out2.parent
    assert _folders(tmp_path) == [out1.parent.name]
    for part in (out1.name, out2.name, out1.parent.name):
        assert len(part.encode("utf-8")) <= 255


def test_refiling_keeps_a_users_video_of_the_same_name(tmp_path, poster):
    entry = _other("AAAAAAAAAAA")
    out1 = movie_layout.file_as_movie(_download_file(tmp_path, "one.mp4"), entry, tmp_path)
    mine = out1.parent / "Never Gonna Give You Up (2009).mkv"
    mine.write_bytes(b"users own copy")

    out2 = movie_layout.file_as_movie(_download_file(tmp_path, "two.webm"), entry, tmp_path)

    assert out2.suffix == ".webm" and out2.read_bytes() == b"two.webm"
    assert not out1.exists()
    assert mine.read_bytes() == b"users own copy"


def test_all_emoji_titles_of_different_videos_stay_apart(tmp_path, poster):
    outs = [
        movie_layout.file_as_movie(_download_file(tmp_path, f"{i}.mkv"),
                                   _other(vid, title="\U0001F600\U0001F680"), tmp_path)
        for i, vid in enumerate(("AAAAAAAAAAA", "BBBBBBBBBBB"))
    ]
    assert outs[0].parent != outs[1].parent
    assert [o.read_bytes() for o in outs] == [b"0.mkv", b"1.mkv"]


def test_entries_without_ids_that_differ_stay_apart(tmp_path, poster):
    ia = dict(source=Source.INTERNET_ARCHIVE, raw={})
    a = _entry(url="https://archive.org/details/a", **ia)
    b = _entry(url="https://archive.org/details/b", artist="Someone else", **ia)
    out_a = movie_layout.file_as_movie(_download_file(tmp_path, "a.mkv"), a, tmp_path)
    out_b = movie_layout.file_as_movie(_download_file(tmp_path, "b.mkv"), b, tmp_path)
    assert out_a.parent != out_b.parent
    assert out_a.read_bytes() == b"a.mkv"


def test_long_cjk_title_stays_within_the_filename_limit(tmp_path, poster):
    entry = _other("AAAAAAAAAAA", title="\u6f22" * 120)
    out = movie_layout.file_as_movie(_download_file(tmp_path, "x.mkv"), entry, tmp_path)
    assert out.read_bytes() == b"x.mkv"
    for part in (out.name, out.parent.name):
        assert len(part.encode("utf-8")) <= 255
    assert out.name.endswith(" (2009).mkv")


def test_long_cjk_title_with_disambiguator_stays_within_the_limit(tmp_path, poster):
    title = "\u6f22" * 120
    for vid in ("AAAAAAAAAAA", "BBBBBBBBBBB"):
        out = movie_layout.file_as_movie(_download_file(tmp_path, f"{vid}.mkv"),
                                         _other(vid, title=title), tmp_path)
        assert len(out.name.encode("utf-8")) <= 255
        assert len(out.parent.name.encode("utf-8")) <= 255
    assert out.name.endswith(f" (2009) [{vid}].mkv")


def test_truncation_never_splits_a_character(tmp_path):
    assert movie_layout._truncate_bytes("a\u6f22b", 2) == "a"
    assert movie_layout._truncate_bytes("a\u6f22b", 4) == "a\u6f22"
    assert movie_layout._truncate_bytes("abc", 0) == ""


def test_env_switch(monkeypatch):
    monkeypatch.delenv(movie_layout.ENV_VAR, raising=False)
    assert movie_layout.enabled_by_env() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(movie_layout.ENV_VAR, value)
        assert movie_layout.enabled_by_env() is True
    monkeypatch.setenv(movie_layout.ENV_VAR, "0")
    assert movie_layout.enabled_by_env() is False


# --- CLI ------------------------------------------------------------

@pytest.fixture
def db_file(tmp_path):
    from media_archivist.storage import EnvelopeJsonStorage

    path = tmp_path / "db.json"
    db = EnvelopeJsonStorage(str(path))
    db[URL] = {"source": "youtube", "url": URL, "videoId": "dQw4w9WgXcQ",
               "title": "Never Gonna Give You Up", "published": "2009-10-25",
               "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"}
    db.store()
    return path


@pytest.fixture
def fake_download(monkeypatch):
    def _download(url, dest_dir, *, format=None, progress_hook=None, timeout=None):
        path = Path(dest_dir) / "Never Gonna Give You Up [dQw4w9WgXcQ].mkv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        return path

    monkeypatch.setattr(streams, "download", _download)


def test_cli_flag_is_unset_unless_given(monkeypatch):
    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    parse = build_parser().parse_args
    base = ["download", "--url", URL, "--output-dir", "/x"]
    assert parse(base).movie_layout is None
    assert parse(base + ["--movie-layout"]).movie_layout is True
    assert parse(base + ["--no-movie-layout"]).movie_layout is False


def test_env_layout_does_not_break_a_bare_url_download(tmp_path, fake_download, monkeypatch):
    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    rc = main(["download", "--url", URL, "--output-dir", str(tmp_path / "lib")])
    assert rc == 0
    assert [p.name for p in (tmp_path / "lib").iterdir()] == [
        "Never Gonna Give You Up [dQw4w9WgXcQ].mkv"]


def test_env_layout_applies_to_index_entries_and_no_flag_turns_it_off(
        tmp_path, db_file, fake_download, poster, monkeypatch):
    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    on, off = tmp_path / "on", tmp_path / "off"
    assert main(["download", "--db-file", str(db_file), "--source", "youtube",
                 "--output-dir", str(on)]) == 0
    assert (on / "Never Gonna Give You Up (2009)" / "movie.nfo").exists()
    assert main(["download", "--db-file", str(db_file), "--source", "youtube",
                 "--output-dir", str(off), "--no-movie-layout"]) == 0
    assert [p.name for p in off.iterdir()] == ["Never Gonna Give You Up [dQw4w9WgXcQ].mkv"]


def test_cli_download_default_layout_is_unchanged(tmp_path, db_file, fake_download, monkeypatch):
    monkeypatch.delenv(movie_layout.ENV_VAR, raising=False)
    out = tmp_path / "lib"
    rc = main(["download", "--db-file", str(db_file), "--source", "youtube",
               "--output-dir", str(out)])
    assert rc == 0
    assert [p.name for p in out.iterdir()] == ["Never Gonna Give You Up [dQw4w9WgXcQ].mkv"]


def test_cli_movie_layout_files_download(tmp_path, db_file, fake_download, poster, monkeypatch):
    monkeypatch.delenv(movie_layout.ENV_VAR, raising=False)
    out = tmp_path / "lib"
    rc = main(["download", "--db-file", str(db_file), "--source", "youtube",
               "--output-dir", str(out), "--movie-layout"])
    assert rc == 0
    folder = out / "Never Gonna Give You Up (2009)"
    assert (folder / "Never Gonna Give You Up (2009).mkv").exists()
    assert (folder / "movie.nfo").exists()


def test_cli_batch_continues_past_a_malformed_thumbnail(tmp_path, db_file, fake_download,
                                                          monkeypatch, capsys):
    from media_archivist.storage import EnvelopeJsonStorage

    db = EnvelopeJsonStorage(str(db_file))
    bad = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
    db[bad] = {"source": "youtube", "url": bad, "videoId": "BBBBBBBBBBB",
               "title": "Second", "published": "2010-01-01",
               "thumbnail": "http://[::1/p.jpg"}
    db.store()
    first = db[URL]
    first["thumbnail"] = "http://[::1/p.jpg"
    db[URL] = first
    db.store()
    monkeypatch.setattr(movie_layout.requests, "get", lambda url, **kw: _Resp())
    out = tmp_path / "lib"
    rc = main(["download", "--db-file", str(db_file), "--source", "youtube",
               "--output-dir", str(out), "--movie-layout"])
    assert rc == 0, capsys.readouterr().err
    names = sorted(p.name for p in out.iterdir())
    assert any(n.startswith("Never Gonna Give You Up") for n in names)
    assert any(n.startswith("Second") for n in names)


def test_cli_movie_layout_rejects_bare_url(tmp_path, fake_download):
    with pytest.raises(SystemExit) as e:
        main(["download", "--url", URL, "--output-dir", str(tmp_path), "--movie-layout"])
    assert "--movie-layout" in str(e.value)


# --- server ---------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from media_archivist.server.app import create_app  # noqa: E402


@pytest.fixture
def server(tmp_path, db_file, fake_download, poster, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_available", lambda: True)
    monkeypatch.setattr(streams, "default_download_dir", lambda: tmp_path / "dl")
    with TestClient(create_app(str(db_file))) as c:
        yield c


def _download(server):
    eid = server.get("/entries").json()["entries"][0]["id"]
    task = server.post(f"/entries/{eid}/download").json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = server.get(f"/tasks/{task['id']}").json()
        if task["status"] in ("ok", "error"):
            return task
        time.sleep(0.02)
    pytest.fail("download never finished")


def test_server_download_default_layout_is_unchanged(server, tmp_path, monkeypatch):
    monkeypatch.delenv(movie_layout.ENV_VAR, raising=False)
    task = _download(server)
    assert task["status"] == "ok", task
    assert task["filepath"] == str(tmp_path / "dl" / "Never Gonna Give You Up [dQw4w9WgXcQ].mkv")


def test_server_download_uses_movie_layout_when_enabled(server, tmp_path, monkeypatch):
    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    task = _download(server)
    assert task["status"] == "ok", task
    folder = tmp_path / "dl" / "Never Gonna Give You Up (2009)"
    assert task["filepath"] == str(folder / "Never Gonna Give You Up (2009).mkv")
    assert (folder / "movie.nfo").exists()
    assert (folder / "poster.jpg").exists()


def test_server_files_the_movie_off_the_event_loop(server, tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    real = movie_layout.file_as_movie
    on_loop = []

    def _spy(*args, **kw):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*args, **kw)

    monkeypatch.setattr(movie_layout, "file_as_movie", _spy)
    task = _download(server)
    assert task["status"] == "ok", task
    assert on_loop == [False]


def test_server_download_ends_ok_with_a_malformed_thumbnail(server, tmp_path, monkeypatch):
    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    monkeypatch.setattr(movie_layout, "_fetch_poster",
                        lambda entry, folder: movie_layout.urlparse("http://[::1/p.jpg"))
    task = _download(server)
    assert task["status"] == "ok", task
    assert (tmp_path / "dl" / "Never Gonna Give You Up (2009)" / "movie.nfo").exists()


def test_server_filing_failure_sends_download_failed(server, monkeypatch):
    from media_archivist import notify as notify_mod

    def _boom(*args, **kw):
        raise OSError(36, "File name too long")

    monkeypatch.setenv(movie_layout.ENV_VAR, "1")
    monkeypatch.setattr(movie_layout, "file_as_movie", _boom)
    calls = []
    monkeypatch.setattr(notify_mod, "notify",
                        lambda event, message, data=None: calls.append((event, message, data)))
    task = _download(server)
    assert task["status"] == "error", task
    assert [c[0] for c in calls] == ["download_failed"], calls
    assert "File name too long" in calls[0][2]["error"]


def test_cli_filing_failure_is_reported_not_raised(tmp_path, db_file, fake_download, poster,
                                                    monkeypatch, capsys):
    def _boom(*args, **kw):
        raise OSError(36, "File name too long")

    monkeypatch.setattr(movie_layout, "file_as_movie", _boom)
    rc = main(["download", "--db-file", str(db_file), "--source", "youtube",
               "--output-dir", str(tmp_path / "dl"), "--movie-layout"])
    assert rc == 1
    assert "could not file" in capsys.readouterr().err
