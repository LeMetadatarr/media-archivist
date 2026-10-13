# SPDX-License-Identifier: Apache-2.0
"""Downloads on request: the queue, the lane, yt-dlp as a program, /api/v1/downloads.

yt-dlp is replaced by ``fixtures/fake_yt_dlp.py`` installed as ``yt-dlp`` on
``PATH``; nothing here touches the network.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_archivist import downloads as dl
from media_archivist import indexdb, streams, ytclient
from media_archivist.backoff import Backoff
from media_archivist.indexdb import IndexDB
from media_archivist.server import auth
from media_archivist.server.app import create_app
from media_archivist.settings import Settings

FAKE = Path(__file__).parent / "fixtures" / "fake_yt_dlp.py"
VID = "dQw4w9WgXcQ"
KEY = f"youtube:{VID}"


def vid(n: int) -> str:
    return f"vid{n:08d}"


class FakeYtdlp:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.control = root / "control.json"
        self.log = root / "calls.jsonl"
        self.set()

    def set(self, mode="ok", **extra) -> None:
        self.control.write_text(json.dumps({"mode": mode, **extra}))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def urls(self):
        return [c["argv"][-1] for c in self.calls()]

    def release(self, video_id: str) -> None:
        (self.root / f"release-{video_id}").touch()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "yt-dlp"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    ctl = tmp_path / "fake"
    ctl.mkdir()
    f = FakeYtdlp(ctl)
    monkeypatch.setenv("FAKE_YTDLP_CONTROL", str(f.control))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(f.log))
    monkeypatch.setenv("FAKE_YTDLP_FIXTURES", str(FAKE.parent))
    monkeypatch.setattr(streams, "ffmpeg_available", lambda: True)
    return f


@pytest.fixture
def media(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setenv(dl.ENV_ROOTS, str(root))
    return root


@pytest.fixture
def index(tmp_path):
    idx = IndexDB(tmp_path / "db.json")
    Settings(idx).set("youtube_request_spacing", 0)
    ytclient.CLIENT.bind(idx)
    yield idx
    idx.close()


@pytest.fixture
def lane(index, fake, media):
    manager = dl.Downloads(index)
    yield manager
    manager.stop(timeout=10)


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def wait_status(manager, download_id, *statuses, timeout=15.0):
    wait_for(lambda: manager.get(download_id)["status"] in statuses, timeout)
    return manager.get(download_id)


# -- file names and yt-dlp's arguments ----------------------------------------


def test_output_template_maps_the_safe_fields():
    assert streams.output_template("{title} ({year}) [{video_id}]") == \
        "%(title)s (%(upload_date>%Y)s) [%(id)s].%(ext)s"
    assert streams.output_template(None) == "%(title)s [%(id)s].%(ext)s"
    assert streams.output_template("{uploader} - {upload_date} - {date} {id} {channel} {ext}") == \
        "%(uploader)s - %(upload_date)s - %(upload_date>%Y-%m-%d)s %(id)s %(channel)s %(ext)s.%(ext)s"


def test_output_template_cannot_leave_its_folder():
    out = streams.output_template("../../etc/{title}")
    assert "/" not in out and "\\" not in out
    assert not out.startswith(".")
    assert streams.output_template('a\\b:c*d?e"f<g>h|i\x01 {id} 100%') == \
        "a_b_c_d_e_f_g_h_i_ %(id)s 100%%.%(ext)s"


@pytest.mark.parametrize("bad", ["{title!r}", "{title:>20}", "{path}", "{0}", "no fields",
                                 "", "   ", "{title", "x" * 201 + "{id}"])
def test_output_template_refuses(bad):
    with pytest.raises(ValueError):
        streams.output_template(bad)


def test_parse_download_line():
    assert streams.parse_download_line("ARCHIVIST-PROGRESS 250 1000 NA\n") == \
        {"progress": {"downloaded": 250.0, "percent": 25.0}}
    assert streams.parse_download_line("ARCHIVIST-PROGRESS 250 NA 500") == \
        {"progress": {"downloaded": 250.0, "percent": 50.0}}
    assert streams.parse_download_line("ARCHIVIST-PROGRESS 250 NA NA") == \
        {"progress": {"downloaded": 250.0, "percent": None}}
    assert streams.parse_download_line("[download]  42.5% of ~ 50.00MiB at 1.00MiB/s") == \
        {"progress": {"downloaded": None, "percent": 42.5}}
    assert streams.parse_download_line('ARCHIVIST-DONE {"filepath": "/x/a.mp4"}') == \
        {"done": {"filepath": "/x/a.mp4"}}
    for other in ("ARCHIVIST-DONE not json", "ARCHIVIST-DONE [1]", "[youtube] Extracting",
                  "ARCHIVIST-PROGRESS 1 2", ""):
        assert streams.parse_download_line(other) is None


def test_download_argv():
    argv = streams.download_argv("https://www.youtube.com/watch?v=" + VID, "/m/x",
                                 filename="{title}", format="bv*+ba", subtitles=["en", "pt"],
                                 ip_family="ipv4")
    assert argv[-2:] == ["--", "https://www.youtube.com/watch?v=" + VID]
    assert argv[argv.index("-f") + 1] == "bv*+ba"
    assert argv[argv.index("-o") + 1] == "/m/x/%(title)s.%(ext)s"
    assert argv[argv.index("--sub-langs") + 1] == "en,pt"
    assert "--force-ipv4" in argv and "--force-ipv6" not in argv
    v6 = streams.download_argv("https://e/v", "/m", ip_family="ipv6")
    assert "--force-ipv6" in v6 and "--sub-langs" not in v6
    assert "--force-ipv4" not in streams.download_argv("https://e/v", "/m", ip_family="auto")


def test_ytdlp_command_prefers_the_binary(fake):
    assert streams.ytdlp_command()[0].endswith(os.sep + "yt-dlp")


def test_ytdlp_command_falls_back_to_the_module(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert streams.ytdlp_command() == [sys.executable, "-m", "yt_dlp"]


# -- target folders -------------------------------------------------------------


def test_confine_to_the_download_roots(media, tmp_path):
    (media / "shorts").mkdir()
    assert dl.confine(str(media / "shorts")) == media / "shorts"
    assert dl.confine(str(media / "new" / "folder")) == media / "new" / "folder"
    assert dl.confine(None) == media
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (media / "link").symlink_to(outside)
    for bad in (str(outside), str(media / ".." / "elsewhere"), str(media / "link"),
                str(media / "link" / "deeper"), "relative/path", "", str(tmp_path)):
        with pytest.raises(dl.DownloadRequestError):
            dl.confine(bad)


def test_confine_without_roots_uses_the_download_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(dl.ENV_ROOTS, raising=False)
    monkeypatch.setenv("MEDIA_ARCHIVIST_DOWNLOAD_DIR", str(tmp_path / "dl"))
    assert dl.confine(None) == tmp_path / "dl"
    with pytest.raises(dl.DownloadRequestError):
        dl.confine(str(tmp_path))


def test_several_roots(monkeypatch, tmp_path):
    monkeypatch.setenv(dl.ENV_ROOTS, os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")]))
    assert dl.confine(str(tmp_path / "b" / "x")) == tmp_path / "b" / "x"


# -- the queue and the lane -------------------------------------------------------


def test_a_download_completes_with_its_files(lane, fake, media, index):
    fake.set(titles={VID: "A Short / Film"})
    record, created = lane.queue("shortarr", KEY, target_dir=str(media / "in"),
                                 filename="{title} ({year}) [{video_id}]", subtitles=["en", "pt"])
    assert created and record["status"] == "queued" and record["app"] == "shortarr"
    assert record["entry"] == KEY
    row = index.execute("SELECT url FROM entries_meta WHERE key = ?", (KEY,)).fetchone()
    assert row["url"] == f"https://www.youtube.com/watch?v={VID}"
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "completed", done
    video = media / "in" / f"A Short ⧸ Film (2024) [{VID}].mp4"
    assert done["files"][0] == str(video)
    assert sorted(done["files"][1:]) == [str(video.with_suffix(".en.vtt")),
                                         str(video.with_suffix(".pt.vtt"))]
    assert video.read_bytes() == b"video bytes"
    assert done["progress"] == 100 and done["title"] == "A Short / Film"
    assert done["bytes"] == sum(Path(f).stat().st_size for f in done["files"])
    assert done["started"] and done["finished"] and done["attempts"] == 1
    assert sorted(p.name for p in (media / "in").iterdir()) == sorted(Path(f).name for f in done["files"])


def test_an_identical_request_returns_the_queued_one(lane, media):
    first, created = lane.queue("a", KEY, target_dir=str(media))
    again, created_again = lane.queue("b", KEY, target_dir=str(media) + "/")
    assert created and not created_again and again["id"] == first["id"]
    other, created_other = lane.queue("a", KEY, target_dir=str(media / "other"))
    assert created_other and other["id"] != first["id"]


def test_a_finished_download_does_not_block_a_new_request(lane, media):
    first, _ = lane.queue("a", KEY, target_dir=str(media))
    lane.start()
    wait_status(lane, first["id"], "completed")
    second, created = lane.queue("a", KEY, target_dir=str(media))
    assert created and second["id"] != first["id"]


def test_apps_take_turns(lane, fake, media):
    ids = [lane.queue("aaa", f"youtube:{vid(n)}", target_dir=str(media))[0]["id"] for n in (1, 2, 3)]
    ids.append(lane.queue("bbb", f"youtube:{vid(4)}", target_dir=str(media))[0]["id"])
    ids.append(lane.queue("ccc", f"youtube:{vid(5)}", target_dir=str(media))[0]["id"])
    lane.start()
    for i in ids:
        wait_status(lane, i, "completed")
    order = [u.rsplit("=", 1)[1] for u in fake.urls()]
    assert order == [vid(1), vid(4), vid(5), vid(2), vid(3)]


def test_one_download_at_a_time_by_default(lane, fake, media):
    fake.set("gate")
    a = lane.queue("a", f"youtube:{vid(1)}", target_dir=str(media))[0]["id"]
    b = lane.queue("b", f"youtube:{vid(2)}", target_dir=str(media))[0]["id"]
    lane.start()
    wait_for(lambda: len(fake.calls()) == 1)
    time.sleep(0.3)
    assert len(fake.calls()) == 1 and lane.get(b)["status"] == "queued"
    fake.release(vid(1))
    fake.release(vid(2))
    wait_status(lane, a, "completed")
    wait_status(lane, b, "completed")


def test_concurrency_setting_runs_downloads_side_by_side(lane, fake, media, index):
    Settings(index).set("download_concurrency", 2)
    fake.set("gate")
    a = lane.queue("a", f"youtube:{vid(1)}", target_dir=str(media))[0]["id"]
    b = lane.queue("a", f"youtube:{vid(2)}", target_dir=str(media))[0]["id"]
    c = lane.queue("a", f"youtube:{vid(3)}", target_dir=str(media))[0]["id"]
    lane.start()
    wait_for(lambda: len(fake.calls()) == 2)
    assert lane.get(a)["status"] == lane.get(b)["status"] == "running"
    assert lane.get(c)["status"] == "queued"
    for n in (1, 2, 3):
        fake.release(vid(n))
    for i in (a, b, c):
        wait_status(lane, i, "completed")


@pytest.mark.parametrize("value", [0, 9, 1.5, True, "2"])
def test_concurrency_setting_is_validated(index, value):
    with pytest.raises(ValueError):
        Settings(index).set("download_concurrency", value)


def test_nothing_starts_while_downloads_are_paused(lane, fake, media, index):
    Backoff(index).mark_blocked("download")
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    assert record["waiting_for"]["reason"] == "bot check"
    assert record["waiting_for"]["until"] == lane.get(record["id"])["waiting_for"]["until"]
    lane.start()
    time.sleep(0.5)
    assert fake.calls() == [] and lane.get(record["id"])["status"] == "queued"
    Backoff(index).mark_clear("download")
    lane.wake()
    assert wait_status(lane, record["id"], "completed")["waiting_for"] is None


def test_a_bot_check_requeues_and_pauses_downloads(lane, fake, media, index):
    fake.set("bot")
    first = lane.queue("a", f"youtube:{vid(1)}", target_dir=str(media))[0]["id"]
    second = lane.queue("b", f"youtube:{vid(2)}", target_dir=str(media))[0]["id"]
    lane.start()
    wait_for(lambda: Backoff(index).blocked_until("download") is not None)
    record = wait_status(lane, first, "queued")
    assert record["attempts"] == 1 and record["error"] is None
    assert record["waiting_for"]["reason"] == "bot check"
    assert Backoff(index).blocked_until("details") is None
    time.sleep(0.3)
    assert len(fake.calls()) == 1 and lane.get(second)["status"] == "queued"
    assert not list(media.iterdir())
    fake.set("ok")
    Backoff(index).mark_clear("download")
    lane.wake()
    assert wait_status(lane, first, "completed")["attempts"] == 2
    wait_status(lane, second, "completed")


def test_success_resets_the_download_pause(lane, media, index):
    backoff = Backoff(index)
    backoff.mark_blocked("download")
    backoff.clock = lambda: time.time() + 2 * backoff.maximum
    assert backoff._read("download") is not None and backoff.remaining("download") == 0
    lane.backoff = backoff
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    wait_status(lane, record["id"], "completed")
    assert backoff._read("download") is None


@pytest.mark.parametrize("mode,kind,text", [
    ("unavailable", "permanent", "Private video"),
    ("network", "network", "name resolution"),
])
def test_a_failure_is_classified(lane, fake, media, mode, kind, text):
    fake.set(mode)
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    failed = wait_status(lane, record["id"], "failed")
    assert failed["error_kind"] == kind and text in failed["error"]
    assert failed["error"].startswith("ERROR:")
    assert failed["files"] == [] and not list(media.iterdir())


@pytest.mark.parametrize("mode", ["nofile", "emptyfile", "escape"])
def test_no_completion_without_a_video_file(lane, fake, media, mode):
    fake.set(mode)
    target = media / "in"
    record = lane.queue("a", KEY, target_dir=str(target))[0]
    lane.start()
    failed = wait_status(lane, record["id"], "completed", "failed")
    assert failed["status"] == "failed"
    assert failed["error"] == "yt-dlp finished without producing a video file"
    assert not list(target.iterdir())


def test_cancel_a_running_download_stops_yt_dlp_and_removes_its_files(lane, fake, media):
    fake.set("hang")
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    wait_for(lambda: list(media.glob(".archivist-dl-*/*.part")))
    pid = fake.calls()[0]["pid"]
    cancelled = lane.cancel(record["id"])
    assert cancelled["status"] == "cancelled"

    def gone():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    wait_for(gone)
    wait_for(lambda: lane.idle())
    assert not list(media.iterdir())
    assert lane.get(record["id"])["status"] == "cancelled"


def test_cancel_a_queued_download(lane, fake, media):
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    assert lane.cancel(record["id"])["status"] == "cancelled"
    lane.start()
    time.sleep(0.3)
    assert fake.calls() == [] and lane.get(record["id"])["status"] == "cancelled"


def test_forget_a_finished_download_and_its_files_only(lane, media):
    record = lane.queue("a", KEY, target_dir=str(media), subtitles=["en"])[0]
    keep = media / "someone-elses.mp4"
    keep.write_bytes(b"x")
    lane.start()
    done = wait_status(lane, record["id"], "completed")
    assert len(done["files"]) == 2
    removed = lane.cancel(record["id"], delete_files=True)
    assert removed == {"id": record["id"], "removed": True, "deleted_files": done["files"],
                       "kept_files": []}
    assert [p.name for p in media.iterdir()] == [keep.name]
    with pytest.raises(dl.UnknownDownload):
        lane.get(record["id"])


def test_forget_keeps_files_unless_asked(lane, media):
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    done = wait_status(lane, record["id"], "completed")
    assert lane.cancel(record["id"])["deleted_files"] == []
    assert Path(done["files"][0]).is_file()


def test_forget_never_deletes_outside_the_target_dir(lane, media, index, tmp_path):
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    wait_status(lane, record["id"], "completed")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    with index.write() as conn:
        conn.execute("UPDATE downloads SET files = ? WHERE id = ?",
                     (json.dumps([str(victim), str(media / ".." / "victim.txt")]), record["id"]))
    assert lane.cancel(record["id"], delete_files=True)["deleted_files"] == []
    assert victim.read_text() == "keep"


def test_a_download_never_replaces_a_file_already_there(lane, media):
    mine = media / f"Video {VID} [{VID}].mp4"
    mine.write_bytes(b"my own file")
    record = lane.queue("a", KEY, target_dir=str(media), subtitles=["en"])[0]
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "completed", done
    assert done["files"] == [str(media / f"Video {VID} [{VID}] (2).mp4"),
                             str(media / f"Video {VID} [{VID}] (2).en.vtt")]
    assert mine.read_bytes() == b"my own file"
    assert Path(done["files"][0]).read_bytes() == b"video bytes"
    assert lane.cancel(record["id"], delete_files=True)["deleted_files"] == done["files"]
    assert mine.read_bytes() == b"my own file"


def test_forgetting_one_download_keeps_another_downloads_file(lane, media):
    first = lane.queue("app", KEY, target_dir=str(media))[0]
    lane.start()
    first_done = wait_status(lane, first["id"], "completed")
    second, created = lane.queue("other", KEY, target_dir=str(media))
    assert created
    second_done = wait_status(lane, second["id"], "completed")
    assert set(first_done["files"]).isdisjoint(second_done["files"])
    lane.cancel(first["id"], delete_files=True)
    assert all(Path(f).read_bytes() == b"video bytes" for f in second_done["files"])


def test_forget_keeps_a_file_that_is_not_the_one_it_placed(lane, media, index):
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    video = Path(wait_status(lane, record["id"], "completed")["files"][0])
    video.write_bytes(b"replaced by hand")
    out = lane.cancel(record["id"], delete_files=True)
    assert out["deleted_files"] == []
    assert out["kept_files"] == [{"path": str(video), "reason": "changed since it was downloaded"}]
    assert video.read_bytes() == b"replaced by hand"

    again = lane.queue("a", KEY, target_dir=str(media))[0]
    held = Path(wait_status(lane, again["id"], "completed")["files"][0])
    other = lane.queue("b", f"youtube:{vid(1)}", target_dir=str(media))[0]
    wait_status(lane, other["id"], "completed")
    with index.write() as conn:
        conn.execute("UPDATE downloads SET files = ? WHERE id = ?", (json.dumps([str(held)]), other["id"]))
    out = lane.cancel(again["id"], delete_files=True)
    assert out["kept_files"] == [{"path": str(held), "reason": "listed by another download"}]
    assert held.is_file()


def test_forget_keeps_a_file_with_no_recorded_content(lane, media, index):
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    video = Path(wait_status(lane, record["id"], "completed")["files"][0])
    with index.write() as conn:
        conn.execute("UPDATE downloads SET file_digests = '{}' WHERE id = ?", (record["id"],))
    out = lane.cancel(record["id"], delete_files=True)
    assert out["kept_files"] == [{"path": str(video), "reason": "no record of what was downloaded"}]
    assert video.is_file()


def test_a_download_that_keeps_meeting_the_bot_check_fails_after_five_attempts(lane, fake, media, index):
    fake.set("bot")
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    with index.write() as conn:
        conn.execute("UPDATE downloads SET attempts = 4, bot_attempts = 4 WHERE id = ?", (record["id"],))
    lane.start()
    failed = wait_status(lane, record["id"], "failed", timeout=5)
    assert failed["attempts"] == 5 and failed["error_kind"] == "bot"
    assert failed["error"] == "met the bot check on 5 runs"
    assert Backoff(index).blocked_until("download") is not None
    assert not list(media.iterdir())


def test_stopping_the_lane_requeues_a_running_download(lane, fake, media, index):
    fake.set("hang")
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    wait_for(lambda: list(media.glob(".archivist-dl-*/*.part")))
    lane.stop(timeout=10)
    assert lane.get(record["id"])["status"] == "queued"
    assert not list(media.iterdir())
    fake.set("ok")
    again = dl.Downloads(index)
    again.start()
    try:
        assert wait_status(again, record["id"], "completed")["attempts"] == 2
    finally:
        again.stop()


def test_a_download_running_at_a_crash_is_queued_again(index, fake, media):
    manager = dl.Downloads(index)
    record = manager.queue("a", KEY, target_dir=str(media))[0]
    staging = media / (dl.STAGING_PREFIX + record["id"])
    staging.mkdir()
    (staging / "x.part").write_bytes(b"p")
    with index.write() as conn:
        conn.execute("UPDATE downloads SET status = 'running', progress = 40 WHERE id = ?",
                     (record["id"],))
    manager.start()
    try:
        assert wait_status(manager, record["id"], "completed")["progress"] == 100
        assert not staging.exists()
    finally:
        manager.stop()


def test_the_target_dir_is_checked_again_when_the_download_starts(lane, fake, media, tmp_path):
    target = media / "later"
    target.mkdir()
    record = lane.queue("a", KEY, target_dir=str(target))[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    target.rmdir()
    target.symlink_to(outside)
    with lane.index.write() as conn:
        conn.execute("UPDATE downloads SET target_dir = ? WHERE id = ?", (str(target), record["id"]))
    lane.start()
    failed = wait_status(lane, record["id"], "failed")
    assert "download roots" in failed["error"] and fake.calls() == []
    assert not list(outside.iterdir())


def test_ip_family_and_format_reach_yt_dlp(lane, fake, media, index):
    Settings(index).set("youtube_ip_family", "ipv6")
    record = lane.queue("a", KEY, target_dir=str(media), format="bv*[height<=720]+ba")[0]
    lane.start()
    wait_status(lane, record["id"], "completed")
    argv = fake.calls()[0]["argv"]
    assert "--force-ipv6" in argv and argv[argv.index("-f") + 1] == "bv*[height<=720]+ba"
    assert argv[-1] == f"https://www.youtube.com/watch?v={VID}"


def test_queue_refuses_bad_requests(lane, media, tmp_path):
    for kwargs in ({"filename": "{nope}"}, {"format": " "}, {"format": "a\nb"},
                   {"subtitles": ["en", "--exec"]}, {"subtitles": ["x"] * 21},
                   {"target_dir": str(tmp_path)}):
        with pytest.raises(dl.DownloadRequestError):
            lane.queue("a", KEY, **{"target_dir": str(media), **kwargs})
    with pytest.raises(dl.UnknownDownload):
        lane.queue("a", "no-such-entry", target_dir=str(media))
    with pytest.raises(dl.UnknownDownload):
        lane.queue("a", "youtube:short", target_dir=str(media))


def test_queue_needs_yt_dlp(lane, media, monkeypatch):
    monkeypatch.setattr(streams, "ytdlp_command", lambda: None)
    with pytest.raises(dl.ToolMissing):
        lane.queue("a", KEY, target_dir=str(media))


def test_a_full_queue_is_refused(lane, media, monkeypatch):
    monkeypatch.setattr(dl, "MAX_QUEUED_PER_APP", 2)
    lane.queue("a", f"youtube:{vid(1)}", target_dir=str(media))
    lane.queue("a", f"youtube:{vid(2)}", target_dir=str(media))
    with pytest.raises(dl.QueueFull):
        lane.queue("a", f"youtube:{vid(3)}", target_dir=str(media))
    assert lane.queue("b", f"youtube:{vid(3)}", target_dir=str(media))[1]


def test_an_indexed_entry_is_found_by_id_or_key(lane, media, index):
    with index.write() as conn:
        conn.execute("INSERT INTO entries_meta (entry_id, key, url, first_seen) VALUES"
                     " ('abc123', 'youtube:zzzzzzzzzzz', 'https://www.youtube.com/watch?v=zzzzzzzzzzz',"
                     " '2024-01-01T00:00:00+00:00')")
    by_id = lane.queue("a", "abc123", target_dir=str(media))[0]
    by_key = lane.queue("a", "youtube:zzzzzzzzzzz", target_dir=str(media))[0]
    assert by_id["id"] == by_key["id"] and by_id["entry"] == "youtube:zzzzzzzzzzz"


def test_list_is_newest_first_with_a_cursor(lane, media):
    ids = [lane.queue("a", f"youtube:{vid(n)}", target_dir=str(media))[0]["id"] for n in range(5)]
    lane.queue("b", f"youtube:{vid(9)}", target_dir=str(media))
    page, cursor = lane.list(app="a", limit=2)
    assert [r["id"] for r in page] == ids[:2:-1][:2] == [ids[4], ids[3]]
    page2, cursor2 = lane.list(app="a", limit=2, cursor=cursor)
    page3, cursor3 = lane.list(app="a", limit=2, cursor=cursor2)
    assert [r["id"] for r in page2 + page3] == [ids[2], ids[1], ids[0]] and cursor3 is None
    assert len(lane.list()[0]) == 6
    lane.cancel(ids[0])
    assert [r["id"] for r in lane.list(app="a", statuses=["cancelled"])[0]] == [ids[0]]
    with pytest.raises(dl.DownloadRequestError):
        lane.list(statuses=["done"])
    with pytest.raises(dl.DownloadRequestError):
        lane.list(cursor="abc")


def test_real_yt_dlp_downloads_from_a_local_server(index, media, tmp_path, monkeypatch):
    import functools
    import http.server
    import threading

    served = tmp_path / "served"
    served.mkdir()
    payload = os.urandom(300_000)
    (served / "clip.mp4").write_bytes(payload)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("PATH", "")
    Settings(index).set("youtube_ip_family", "auto")
    url = f"http://127.0.0.1:{httpd.server_address[1]}/clip.mp4"
    with index.write() as conn:
        conn.execute("INSERT INTO entries_meta (entry_id, key, url, first_seen)"
                     " VALUES ('local1', 'generic:clip', ?, 't')", (url,))
    manager = dl.Downloads(index)
    try:
        assert streams.ytdlp_command() == [sys.executable, "-m", "yt_dlp"]
        record = manager.queue("a", "generic:clip", target_dir=str(media / "out"), format="b",
                               filename="{title} [{id}]")[0]
        manager.start()
        done = wait_status(manager, record["id"], "completed", "failed", timeout=60)
    finally:
        manager.stop()
        httpd.shutdown()
        httpd.server_close()
    assert done["status"] == "completed", done
    assert done["files"] == [str(media / "out" / "clip [clip].mp4")]
    assert Path(done["files"][0]).read_bytes() == payload
    assert done["bytes"] == len(payload) and done["progress"] == 100
    assert [p.name for p in (media / "out").iterdir()] == ["clip [clip].mp4"]


def test_a_file_that_appears_while_the_video_is_hashed_is_not_replaced(lane, media, monkeypatch):
    real = dl._sha256
    victim = media / f"Video {VID} [{VID}].mp4"

    def sha(path):
        if not victim.exists():
            victim.write_bytes(b"ANOTHER APP'S FILE")
        return real(path)

    monkeypatch.setattr(dl, "_sha256", sha)
    rec = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    done = wait_status(lane, rec["id"], "completed", "failed")
    assert done["status"] == "completed"
    assert victim.read_bytes() == b"ANOTHER APP'S FILE"
    assert [Path(f).name for f in done["files"]] == [f"Video {VID} [{VID}] (2).mp4"]
    assert Path(done["files"][0]).read_bytes() == b"video bytes"


def test_a_file_that_appears_at_the_last_moment_makes_the_set_take_the_next_number(tmp_path, monkeypatch):
    target = tmp_path / "t"
    staging = tmp_path / "s"
    target.mkdir()
    staging.mkdir()
    video, sub = staging / "v [x].mp4", staging / "v [x].en.vtt"
    video.write_bytes(b"video")
    sub.write_bytes(b"WEBVTT")
    real = os.link
    late = target / "v [x].en.vtt"

    def link(src, dest, **kw):
        result = real(src, dest, **kw)
        late.write_bytes(b"late file")
        return result

    monkeypatch.setattr(os, "link", link)
    placed = dl._place([video, sub], target, video)
    assert [p.name for p in placed] == ["v [x] (2).mp4", "v [x] (2).en.vtt"]
    assert (target / "v [x].en.vtt").read_bytes() == b"late file"
    assert not (target / "v [x].mp4").exists()
    assert not video.exists() and not sub.exists()


def test_placing_falls_back_to_a_copy_where_links_fail(tmp_path, monkeypatch):
    target, staging = tmp_path / "t", tmp_path / "s"
    target.mkdir()
    staging.mkdir()
    video = staging / "v.mp4"
    video.write_bytes(b"video")

    def nolink(src, dest, **kw):
        raise OSError(1, "operation not permitted")

    monkeypatch.setattr(os, "link", nolink)
    (placed,) = dl._place([video], target, video)
    assert placed.read_bytes() == b"video" and not video.exists()
    (target / "w.mp4").write_bytes(b"other")
    again = staging / "w.mp4"
    again.write_bytes(b"new")
    (placed,) = dl._place([again], target, again)
    assert placed.name == "w (2).mp4" and (target / "w.mp4").read_bytes() == b"other"


def test_cancelling_another_download_is_not_held_up_by_a_hash(lane, media, monkeypatch):
    import threading

    real = dl._sha256
    hashing = threading.Event()

    def sha(path):
        hashing.set()
        time.sleep(2)
        return real(path)

    monkeypatch.setattr(dl, "_sha256", sha)
    lane.queue("a", KEY, target_dir=str(media))
    other = lane.queue("b", "youtube:vid00000001", target_dir=str(media))[0]
    lane.start()
    assert hashing.wait(10)
    started = time.monotonic()
    lane.cancel(other["id"])
    assert time.monotonic() - started < 1.0


def test_cancelling_the_download_that_is_being_hashed_leaves_no_files(lane, media, monkeypatch):
    import threading

    real = dl._sha256
    hashing = threading.Event()

    def sha(path):
        hashing.set()
        time.sleep(1)
        return real(path)

    monkeypatch.setattr(dl, "_sha256", sha)
    rec = lane.queue("a", KEY, target_dir=str(media))[0]
    lane.start()
    assert hashing.wait(10)
    lane.cancel(rec["id"])
    wait_for(lambda: not lane._jobs)
    assert lane.get(rec["id"])["status"] == "cancelled"
    assert list(media.iterdir()) == []


def test_restarts_do_not_count_as_bot_checks(index, fake, media):
    fake.set("hang")
    lane = dl.Downloads(index)
    rec = lane.queue("a", KEY, target_dir=str(media))[0]
    try:
        for _ in range(4):
            lane.start()
            wait_for(lambda: lane.get(rec["id"])["status"] == "running" and lane._procs)
            lane.stop(timeout=10)
            lane._thread = None
        assert lane.get(rec["id"])["attempts"] == 4
        fake.set("bot")
        lane.start()
        wait_for(lambda: Backoff(index).blocked_until("download") is not None)
        wait_for(lambda: lane.get(rec["id"])["status"] == "queued")
        again = lane.get(rec["id"])
        assert again["status"] == "queued" and again["error"] is None
        assert index.execute("SELECT bot_attempts FROM downloads WHERE id = ?",
                             (rec["id"],)).fetchone()[0] == 1
    finally:
        lane.stop(timeout=10)


def test_the_failure_reports_the_bot_checks_it_met(lane, fake, media, index):
    fake.set("bot")
    record = lane.queue("a", KEY, target_dir=str(media))[0]
    with index.write() as conn:
        conn.execute("UPDATE downloads SET attempts = 9, bot_attempts = 4 WHERE id = ?", (record["id"],))
    lane.start()
    failed = wait_status(lane, record["id"], "failed", timeout=5)
    assert failed["error"] == "met the bot check on 5 runs"


def test_forget_reports_symlinked_missing_and_escaped_files(lane, media, index, tmp_path):
    rec = lane.queue("a", KEY, target_dir=str(media), subtitles=["en"])[0]
    lane.start()
    done = wait_status(lane, rec["id"], "completed")
    video, sub = (Path(f) for f in done["files"])
    elsewhere = tmp_path / "elsewhere.mp4"
    elsewhere.write_bytes(b"video bytes")
    video.unlink()
    video.symlink_to(elsewhere)
    sub.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    with index.write() as conn:
        row = conn.execute("SELECT files, file_digests FROM downloads WHERE id = ?", (rec["id"],)).fetchone()
        files = json.loads(row["files"]) + [str(outside)]
        digests = json.loads(row["file_digests"])
        digests[str(outside)] = dl._sha256(outside)
        conn.execute("UPDATE downloads SET files = ?, file_digests = ? WHERE id = ?",
                     (json.dumps(files), json.dumps(digests), rec["id"]))
    out = lane.cancel(rec["id"], delete_files=True)
    reasons = {k["path"]: k["reason"] for k in out["kept_files"]}
    assert set(reasons) == {str(video), str(sub), str(outside)}
    assert "symbolic link" in reasons[str(video)]
    assert reasons[str(sub)] == "no longer there"
    assert "outside" in reasons[str(outside)]
    assert out["deleted_files"] == [] and elsewhere.exists() and outside.exists()


def test_migration_numbers_earlier_rows(tmp_path):
    from media_archivist.index_migrations import m0001_schema

    idx = IndexDB(tmp_path / "db.json", migrate=False)
    with idx.write() as conn:
        m0001_schema.apply(conn, idx)
        for n in (1, 2):
            conn.execute("INSERT INTO downloads (id, app, entry_id, target_dir, status, created_at,"
                         " updated_at) VALUES (?, 'a', 'e', '/m', 'completed', 't', 't')", (f"old{n}",))
    idx.migrate()
    rows = idx.execute("SELECT id, seq, files, subtitles, attempts FROM downloads ORDER BY seq").fetchall()
    assert [(r["id"], r["seq"], r["files"], r["subtitles"], r["attempts"]) for r in rows] == \
        [("old1", 1, "[]", "[]", 0), ("old2", 2, "[]", "[]", 0)]
    assert 400 in [m["version"] for m in idx.migrations()]
    idx.close()


# -- /api/v1/downloads ------------------------------------------------------------


@pytest.fixture
def client(tmp_path, fake, media, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    monkeypatch.delenv(auth.ENV_EXEMPT, raising=False)
    db_path = str(tmp_path / "api.json")
    Settings(indexdb.open_index(db_path)).set("youtube_request_spacing", 0)
    with TestClient(create_app(db_path)) as c:
        yield c


def api_wait(client, download_id, *statuses):
    def check():
        return client.get(f"/api/v1/downloads/{download_id}").json()["status"] in statuses
    wait_for(check)
    return client.get(f"/api/v1/downloads/{download_id}").json()


def test_api_queue_attributes_the_app_and_completes(client, media):
    r = client.post("/api/v1/downloads", headers={"X-Archivist-App": "shortarr"},
                    json={"entry": KEY, "target_dir": str(media / "incoming"),
                          "filename": "{title} [{video_id}]", "subtitles": ["en"]})
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["app"] == "shortarr" and record["id"].startswith("dl_")
    again = client.post("/api/v1/downloads", headers={"X-Archivist-App": "shortarr"},
                        json={"entry": KEY, "target_dir": str(media / "incoming")})
    assert again.status_code == 200 and again.json()["id"] == record["id"]
    done = api_wait(client, record["id"], "completed", "failed")
    assert done["status"] == "completed"
    assert done["files"][0] == str(media / "incoming" / f"Video {VID} [{VID}].mp4")


def test_api_filename_template_is_accepted_too(client, media):
    r = client.post("/api/v1/downloads", json={"entry": KEY, "target_dir": str(media),
                                               "filename_template": "{video_id}"})
    assert r.status_code == 201 and r.json()["filename"] == "{video_id}"
    assert api_wait(client, r.json()["id"], "completed")["files"] == [str(media / f"{VID}.mp4")]
    both = client.post("/api/v1/downloads", json={"entry": KEY, "target_dir": str(media),
                                                  "filename": "{id}", "filename_template": "{title}"})
    assert both.status_code == 400


def test_api_lists_the_callers_downloads_unless_asked_for_all(client, media):
    a = client.post("/api/v1/downloads", headers={"X-Archivist-App": "a"},
                    json={"entry": f"youtube:{vid(1)}", "target_dir": str(media)}).json()
    b = client.post("/api/v1/downloads", json={"entry": f"youtube:{vid(2)}",
                                               "target_dir": str(media)}).json()
    assert b["app"] == "user"
    mine = client.get("/api/v1/downloads", headers={"X-Archivist-App": "a"}).json()
    assert [d["id"] for d in mine["downloads"]] == [a["id"]] and mine["next_cursor"] is None
    everyone = client.get("/api/v1/downloads?app=all").json()["downloads"]
    assert {d["id"] for d in everyone} == {a["id"], b["id"]}
    api_wait(client, a["id"], "completed")
    api_wait(client, b["id"], "completed")
    history = client.get("/api/v1/downloads?app=all&status=completed,failed,cancelled&limit=1").json()
    assert len(history["downloads"]) == 1 and history["next_cursor"]
    rest = client.get(f"/api/v1/downloads?app=all&status=completed&cursor={history['next_cursor']}")
    assert len(rest.json()["downloads"]) == 1
    assert client.get("/api/v1/downloads?status=queued", headers={"X-Archivist-App": "a"}) \
        .json()["downloads"] == []
    assert client.get("/api/v1/downloads?status=bogus").status_code == 400


def test_api_errors(client, media, tmp_path, monkeypatch):
    def post(**body):
        return client.post("/api/v1/downloads", json={"entry": KEY, "target_dir": str(media), **body})

    assert post(target_dir=str(tmp_path)).status_code == 400
    assert post(filename="{path}").status_code == 400
    assert post(subtitles=["en;rm"]).status_code == 400
    assert post(entry="not-indexed").status_code == 404
    assert post(unknown=1).status_code == 422
    assert client.get("/api/v1/downloads/dl_missing").status_code == 404
    assert client.delete("/api/v1/downloads/dl_missing").status_code == 404
    monkeypatch.setattr(streams, "ytdlp_command", lambda: None)
    r = post()
    assert r.status_code == 503 and "yt-dlp" in r.json()["detail"]


def test_api_cancel_and_forget(client, media, fake):
    fake.set("hang")
    record = client.post("/api/v1/downloads", json={"entry": KEY, "target_dir": str(media)}).json()
    api_wait(client, record["id"], "running")
    wait_for(lambda: list(media.glob(".archivist-dl-*/*.part")))
    r = client.delete(f"/api/v1/downloads/{record['id']}")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    wait_for(lambda: not list(media.iterdir()))
    forgotten = client.delete(f"/api/v1/downloads/{record['id']}?delete_files=true").json()
    assert forgotten == {"id": record["id"], "removed": True, "deleted_files": [], "kept_files": []}
    assert client.get(f"/api/v1/downloads/{record['id']}").status_code == 404


def test_api_meta_names_the_feature(client):
    assert "downloads" in client.get("/api/v1/meta").json()["features"]


def test_api_downloads_are_behind_the_key(tmp_path, media, fake, monkeypatch):
    monkeypatch.setenv(auth.ENV_KEY, "0123456789abcdef0123456789abcdef")
    with TestClient(create_app(str(tmp_path / "k.json")), client=("203.0.113.7", 5000)) as c:
        assert c.get("/api/v1/downloads").status_code == 401
        assert c.post("/api/v1/downloads", json={"entry": KEY}).status_code == 401
