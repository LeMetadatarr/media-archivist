# SPDX-License-Identifier: Apache-2.0
"""Downloads for datasets: tool ``tutubo``, folders relative to a root, batches, pauses.

tutubo's fetch program is replaced by ``fixtures/fake_tutubo_fetch.py`` and
yt-dlp by ``fixtures/fake_yt_dlp.py``; nothing here touches the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_archivist import downloads as dl
from media_archivist import indexdb, streams, tutubofetch, ytclient
from media_archivist.backoff import Backoff
from media_archivist.indexdb import IndexDB
from media_archivist.server import auth
from media_archivist.server.app import create_app
from media_archivist.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
VID = "dQw4w9WgXcQ"
KEY = f"youtube:{VID}"
OUTSIDE = ("203.0.113.7", 5000)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vid(n: int) -> str:
    return f"vid{n:08d}"


class FakeFetch:
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

    def release(self, video_id: str) -> None:
        (self.root / f"release-{video_id}").touch()


def install(tmp_path: Path, name: str, source: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / name
    exe.write_text(f"#!{sys.executable}\n" + source.read_text())
    exe.chmod(0o755)
    return exe


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = install(tmp_path, "fake-tutubo-fetch", FIXTURES / "fake_tutubo_fetch.py")
    monkeypatch.setenv(tutubofetch.ENV_COMMAND, str(exe))
    ctl = tmp_path / "fake-fetch"
    ctl.mkdir()
    f = FakeFetch(ctl)
    monkeypatch.setenv("FAKE_TUTUBO_CONTROL", str(f.control))
    monkeypatch.setenv("FAKE_TUTUBO_LOG", str(f.log))
    return f


@pytest.fixture
def ytdlp(tmp_path, monkeypatch):
    install(tmp_path, "yt-dlp", FIXTURES / "fake_yt_dlp.py")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    ctl = tmp_path / "fake-ytdlp"
    ctl.mkdir()
    (ctl / "control.json").write_text(json.dumps({"mode": "ok"}))
    monkeypatch.setenv("FAKE_YTDLP_CONTROL", str(ctl / "control.json"))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(ctl / "calls.jsonl"))
    monkeypatch.setenv("FAKE_YTDLP_FIXTURES", str(FIXTURES))
    monkeypatch.setattr(streams, "ffmpeg_available", lambda: True)


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


def fetch(manager, entry=KEY, **options):
    return manager.queue("data", entry, tool="tutubo", **options)[0]


def shared_pause(kind="download"):
    from tutubo import backoff

    backoff.shared().record(kind)


def clear_shared():
    from tutubo import backoff

    for kind in tutubofetch.PAUSE_KINDS:
        backoff.shared().mark_clear(kind)


def tree(folder: Path):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


# -- the fetch, its files and its record ---------------------------------------


def test_a_fetch_places_audio_captions_and_details_with_their_digests(lane, fake, media):
    fake.set(tracks=[["pt", "asr"], ["fr", "manual"]])
    record = fetch(lane, dest="corpus/chan")
    assert record["tool"] == "tutubo" and record["root"] == 0 and record["dest"] == "corpus/chan"
    assert record["fetch"] == {"audio": True, "captions": True, "caption_kinds": ["manual", "asr"],
                               "caption_formats": ["json3", "vtt"]}
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "completed", done["error"]
    assert done["complete"] is True and done["incomplete"] == []
    target = media / "corpus" / "chan"
    rels = sorted(item["path"] for item in done["file_details"])
    assert rels == sorted(f"corpus/chan/{n}" for n in (
        f"audio/{VID}.m4a", f"captions/{VID}.pt.asr.json3", f"captions/{VID}.pt.asr.vtt",
        f"captions/{VID}.fr.manual.json3", f"captions/{VID}.fr.manual.vtt", f"meta/{VID}.json"))
    for item in done["file_details"]:
        path = media / item["path"]
        assert item["sha256"] == sha(path) and item["bytes"] == path.stat().st_size
    roles = {item["path"].rsplit("/", 1)[1]: item["role"] for item in done["file_details"]}
    assert roles[f"{VID}.m4a"] == "audio" and roles[f"{VID}.json"] == "meta"
    on_disk = json.loads((target / "meta" / f"{VID}.files.json").read_text())
    assert on_disk["complete"] is True and on_disk["video_id"] == VID
    assert sorted(f["path"] for f in on_disk["files"]) == [r[len("corpus/chan/"):] for r in rels]
    assert sorted(done["paths"]) == sorted(rels + [f"corpus/chan/meta/{VID}.files.json"])
    assert done["files"] == [str(media / p) for p in done["paths"]]
    assert not list(target.glob(".archivist-*"))
    argv = fake.calls()[0]["argv"]
    assert argv[argv.index("--kinds") + 1] == "manual,asr"
    assert argv[argv.index("--formats") + 1] == "json3,vtt"
    assert argv[argv.index("--family") + 1] == "ipv4" and argv[argv.index("--gap") + 1] == "0"
    assert argv[-2:] == ["--", VID]


def test_caption_kinds_formats_and_no_audio_reach_the_program(lane, fake, media, index):
    Settings(index).set("youtube_ip_family", "auto")
    record = fetch(lane, dest="c", audio=False, caption_kinds=["asr"], caption_formats=["vtt"])
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "completed" and done["complete"] is True
    argv = fake.calls()[0]["argv"]
    assert "--no-audio" in argv and "--no-captions" not in argv
    assert argv[argv.index("--kinds") + 1] == "asr" and argv[argv.index("--formats") + 1] == "vtt"
    assert argv[argv.index("--family") + 1] == "any"
    assert [i["path"] for i in done["file_details"]] == [f"c/captions/{VID}.pt.asr.vtt", f"c/meta/{VID}.json"]


def test_an_incomplete_fetch_is_placed_and_says_what_is_missing(lane, fake, media):
    fake.set("incomplete")
    record = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "completed" and done["complete"] is False
    assert done["incomplete"] == ["caption pt.asr.vtt: not offered as vtt"]
    assert (media / "c" / "audio" / f"{VID}.m4a").is_file()
    assert json.loads((media / "c" / "meta" / f"{VID}.files.json").read_text())["complete"] is False


def test_a_live_stream_that_has_not_started_is_recorded_as_skipped(lane, fake, media):
    fake.set("skipped")
    record = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["complete"] is False
    assert done["incomplete"] == ["skipped: live stream or premiere has not started"]
    assert tree(media / "c") == ["meta", f"meta/{VID}.files.json", f"meta/{VID}.json"]


@pytest.mark.parametrize("mode", ["escape", "symlink", "foreign", "wrongrequest", "nometa"])
def test_files_the_program_should_not_have_named_are_never_placed(lane, fake, media, mode):
    fake.set(mode)
    record = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "failed" and done["error_kind"] == "tool"
    assert done["files"] == [] and done["file_details"] is None
    target = media / "c"
    assert not any(p.name != "outside.m4a" for p in target.iterdir()), tree(target)


@pytest.mark.parametrize("mode,kind", [("error", "permanent"), ("usage", "tool")])
def test_a_program_failure_is_classified(lane, fake, media, mode, kind):
    fake.set(mode)
    record = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "failed" and done["error_kind"] == kind
    assert not list((media / "c").iterdir())


# -- files already in the target ------------------------------------------------


def test_a_file_this_server_did_not_place_is_never_replaced(lane, fake, media):
    audio = media / "c" / "audio" / f"{VID}.m4a"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"someone else's file")
    record = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, record["id"], "completed", "failed")
    assert done["status"] == "failed" and done["error_kind"] == "conflict"
    assert audio.read_bytes() == b"someone else's file"
    assert tree(media / "c") == ["audio", f"audio/{VID}.m4a"]


def test_an_identical_file_in_the_way_is_kept(lane, fake, media):
    audio = media / "c" / "audio" / f"{VID}.m4a"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(f"audio of {VID}".encode())
    record = fetch(lane, dest="c")
    lane.start()
    assert wait_status(lane, record["id"], "completed", "failed")["complete"] is True


def test_a_refetch_replaces_only_unchanged_files_this_server_placed(lane, fake, media):
    fake.set("incomplete", audio="first")
    first = fetch(lane, dest="c")
    lane.start()
    assert wait_status(lane, first["id"], "completed")["complete"] is False
    audio = media / "c" / "audio" / f"{VID}.m4a"
    fake.set("ok", audio="second")
    second = fetch(lane, dest="c")
    done = wait_status(lane, second["id"], "completed", "failed")
    assert done["status"] == "completed" and done["complete"] is True
    assert audio.read_bytes() == b"second"
    audio.write_bytes(b"edited by hand")
    fake.set("ok", audio="third")
    third = fetch(lane, dest="c", caption_formats=["json3"])
    done = wait_status(lane, third["id"], "completed", "failed")
    assert done["error_kind"] == "conflict" and audio.read_bytes() == b"edited by hand"


def test_a_complete_target_finishes_without_running_the_program(lane, fake, media):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    again = fetch(lane, dest="c")
    done = wait_status(lane, again["id"], "completed", "failed")
    assert done["complete"] is True and len(fake.calls()) == 1
    assert sorted(i["sha256"] for i in done["file_details"]) == \
        sorted(i["sha256"] for i in lane.get(first["id"])["file_details"])


def test_a_changed_file_makes_the_target_fetch_again(lane, fake, media):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    (media / "c" / "captions" / f"{VID}.pt.asr.vtt").unlink()
    again = fetch(lane, dest="c")
    done = wait_status(lane, again["id"], "completed", "failed")
    assert done["complete"] is True and len(fake.calls()) == 2
    assert (media / "c" / "captions" / f"{VID}.pt.asr.vtt").is_file()


def test_a_planted_staging_link_cannot_send_the_program_elsewhere(lane, fake, media, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (media / "c").mkdir()
    (media / "c" / (tutubofetch.STAGING_PREFIX + VID)).symlink_to(elsewhere)
    record = fetch(lane, dest="c")
    lane.start()
    assert wait_status(lane, record["id"], "completed", "failed")["complete"] is True
    assert not list(elsewhere.iterdir())


# -- cancel, stop and resume ------------------------------------------------------


def test_cancel_a_running_fetch_stops_the_program_and_leaves_nothing(lane, fake, media):
    fake.set("hang")
    record = fetch(lane, dest="c")
    lane.start()
    wait_for(lambda: list((media / "c").glob(".archivist-fetch-*/audio/*.part")))
    pid = fake.calls()[0]["pid"]
    assert lane.cancel(record["id"])["status"] == "cancelled"
    wait_for(lambda: not (media / "c").exists() or not list((media / "c").iterdir()))

    def gone():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] == "Z"
    wait_for(gone)


def test_stopping_keeps_the_partial_audio_for_the_next_run(lane, fake, media, index):
    fake.set("hang")
    record = fetch(lane, dest="c")
    lane.start()
    wait_for(lambda: list((media / "c").glob(".archivist-fetch-*/audio/*.part")))
    lane.stop(timeout=10)
    assert lane.get(record["id"])["status"] == "queued"
    assert list((media / "c").glob(".archivist-fetch-*/audio/*.part"))
    fake.set("ok")
    again = dl.Downloads(index)
    again.start()
    try:
        done = wait_status(again, record["id"], "completed", "failed")
    finally:
        again.stop(timeout=10)
    assert done["complete"] is True and fake.calls()[-1]["resumed"] is True
    assert not list((media / "c").glob(".archivist-*"))


def test_cancelling_a_queued_fetch_drops_what_it_kept(lane, fake, media):
    shared_pause()
    record = fetch(lane, dest="c")
    kept = media / "c" / (tutubofetch.STAGING_PREFIX + VID) / "audio"
    kept.mkdir(parents=True)
    (kept / f"{VID}.m4a.part").write_bytes(b"partial")
    lane.cancel(record["id"])
    assert not list((media / "c").iterdir())


# -- pauses ------------------------------------------------------------------------


def test_exit_75_pauses_requeues_and_nothing_is_retried(lane, fake, media, index):
    fake.set("paused")
    record = fetch(lane, dest="c")
    lane.start()
    wait_for(lambda: lane.get(record["id"])["attempts"] == 1
             and lane.get(record["id"])["status"] == "queued")
    queued = lane.get(record["id"])
    assert queued["waiting_for"]["reason"] == "bot check" and queued["waiting_for"]["until"]
    assert Backoff(index).blocked_until("download") is not None
    pauses = lane.pauses()
    assert pauses["tutubo"]["paused"] is True and pauses["tutubo"]["retry_at"]
    assert pauses["yt-dlp"]["paused"] is True
    time.sleep(0.5)
    assert len(fake.calls()) == 1
    assert list((media / "c").glob(".archivist-fetch-*"))
    Backoff(index).mark_clear("download")
    lane.wake()
    time.sleep(0.5)
    assert len(fake.calls()) == 1, "tutubo's shared pause still holds"
    clear_shared()
    fake.set("ok")
    lane.wake()
    assert wait_status(lane, record["id"], "completed", "failed")["complete"] is True


def test_a_details_pause_in_the_shared_file_holds_fetches_and_not_yt_dlp(lane, fake, ytdlp, media):
    shared_pause("details")
    record = fetch(lane, dest="c")
    video = lane.queue("data", f"youtube:{vid(1)}", dest="v")[0]
    lane.start()
    assert wait_status(lane, video["id"], "completed", "failed")["status"] == "completed"
    queued = lane.get(record["id"])
    assert queued["status"] == "queued" and queued["waiting_for"]["reason"] == "bot check"
    assert fake.calls() == []
    assert lane.get(video["id"])["paths"][0].startswith("v/")


def test_an_own_details_pause_holds_fetches(lane, fake, media, index):
    Backoff(index).mark_blocked("details")
    record = fetch(lane, dest="c")
    lane.start()
    time.sleep(0.4)
    assert lane.get(record["id"])["status"] == "queued" and fake.calls() == []


def test_turning_tutubos_pause_off_does_not_reach_the_program(lane, fake, media, index, monkeypatch):
    monkeypatch.setenv("TUTUBO_BACKOFF", "off")
    fake.set("paused")
    record = fetch(lane, dest="c")
    lane.start()
    wait_for(lambda: lane.get(record["id"])["attempts"] == 1
             and lane.get(record["id"])["status"] == "queued")
    assert fake.calls()[0]["backoff_env"] is None
    assert Backoff(index).blocked_until("download") is not None


def test_a_bot_check_the_program_only_reports_still_pauses(lane, fake, media, index):
    fake.set("error", error="ERROR: Sign in to confirm you're not a bot")
    record = fetch(lane, dest="c")
    lane.start()
    wait_for(lambda: lane.get(record["id"])["attempts"] == 1
             and lane.get(record["id"])["status"] == "queued")
    assert Backoff(index).blocked_until("download") is not None


def test_a_fetch_that_keeps_meeting_the_bot_check_fails_after_five_runs(lane, fake, media, index):
    fake.set("paused", pause_kind="details")
    record = fetch(lane, dest="c")
    with index.write() as conn:
        conn.execute("UPDATE downloads SET attempts = 4, bot_attempts = 4 WHERE id = ?", (record["id"],))
    lane.start()
    failed = wait_status(lane, record["id"], "failed")
    assert failed["error_kind"] == "bot" and failed["error"] == "met the bot check on 5 runs"
    assert Backoff(index).blocked_until("details") is not None
    assert not list((media / "c").iterdir())


def test_an_unreadable_shared_pause_file_holds_fetches(lane, fake, media, monkeypatch, tmp_path):
    state = tmp_path / "unreadable-state"
    state.mkdir()
    (state / "backoff.json").mkdir()
    monkeypatch.setenv("TUTUBO_STATE_DIR", str(state))
    record = fetch(lane, dest="c")
    lane.start()
    time.sleep(0.4)
    queued = lane.get(record["id"])
    assert queued["status"] == "queued" and "back-off file" in queued["waiting_for"]["reason"]
    assert fake.calls() == []


# -- requests ------------------------------------------------------------------------


@pytest.mark.parametrize("options", [
    {"dest": "../x"}, {"dest": "/abs"}, {"dest": "a/../../x"}, {"dest": "a//b"}, {"dest": "."},
    {"dest": "a/./b"}, {"dest": "~/x"}, {"dest": "a\\b"}, {"dest": "a\x00b"}, {"dest": "a\nb"},
    {"dest": "x" * 1025}, {"dest": ""}, {"dest": ".archivist-fetch-abc"}, {"dest": "a/.archivist-dl-x"},
    {"root": 1}, {"root": -1}, {"root": True}, {"root": "0"},
    {"target_dir": "/tmp", "dest": "x"},
])
def test_folders_outside_a_root_are_refused(lane, media, options):
    with pytest.raises(dl.DownloadRequestError):
        fetch(lane, **options)
    assert lane.list(app=None)[0] == []


def test_a_link_inside_the_root_cannot_carry_dest_out(lane, media, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (media / "link").symlink_to(elsewhere)
    with pytest.raises(dl.DownloadRequestError):
        fetch(lane, dest="link/x")


def test_dest_picks_its_root_by_position(index, fake, tmp_path, monkeypatch):
    first, second = tmp_path / "r0", tmp_path / "r1"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv(dl.ENV_ROOTS, f"{first}{os.pathsep}{second}")
    manager = dl.Downloads(index)
    try:
        record = fetch(manager, root=1, dest="a/b")
        assert record["target_dir"] == str(second / "a" / "b") and record["root"] == 1
        manager.start()
        done = wait_status(manager, record["id"], "completed", "failed")
    finally:
        manager.stop(timeout=10)
    assert all(p.startswith("a/b/") for p in done["paths"])
    assert (second / "a" / "b" / "audio" / f"{VID}.m4a").is_file()
    monkeypatch.setenv(dl.ENV_ROOTS, str(first))
    assert manager.get(record["id"])["root"] is None


def test_a_target_dir_inside_nested_roots_is_relative_to_the_innermost(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    monkeypatch.setenv(dl.ENV_ROOTS, f"{outer}{os.pathsep}{inner}")
    assert dl.resolve_target(str(inner / "x")) == (inner / "x", inner)


@pytest.mark.parametrize("entry", ["youtube:short", "youtube:../../../etc", "youtube:dQw4w9WgXcQ/x",
                                   "youtube:dQw4w9WgXc!", "", " ", "nothing-like-it"])
def test_bad_ids_are_refused(lane, media, entry):
    with pytest.raises((dl.DownloadRequestError, dl.UnknownDownload)):
        fetch(lane, entry=entry)
    assert lane.list(app=None)[0] == []


def test_tutubo_downloads_youtube_videos_only(lane, media, index):
    with index.write() as conn:
        conn.execute("INSERT INTO entries_meta (entry_id, key, url, first_seen) VALUES (?, ?, ?, ?)",
                     ("ia1", "ia:https://archive.org/details/x", "https://archive.org/details/x",
                      "2024-01-01T00:00:00+00:00"))
    with pytest.raises(dl.DownloadRequestError, match="YouTube videos only"):
        fetch(lane, entry="ia1")


def test_an_id_that_starts_with_a_dash_goes_after_the_separator(lane, fake, media):
    record = fetch(lane, entry="youtube:-abcdefghij", dest="c")
    lane.start()
    assert wait_status(lane, record["id"], "completed", "failed")["complete"] is True
    assert fake.calls()[0]["argv"][-2:] == ["--", "-abcdefghij"]


@pytest.mark.parametrize("options", [
    {"filename": "{title}"}, {"format": "bestaudio"}, {"subtitles": ["en"]},
    {"subtitles_kinds": ["manual"]}, {"keep_info_json": True},
    {"caption_kinds": ["automatic"]}, {"caption_kinds": []}, {"caption_formats": ["../x"]},
    {"caption_formats": ["srt"]}, {"audio": False, "captions": False}, {"audio": "yes"},
])
def test_options_that_do_not_fit_tool_tutubo_are_refused(lane, fake, media, options):
    with pytest.raises(dl.DownloadRequestError):
        fetch(lane, **options)


@pytest.mark.parametrize("options", [{"audio": True}, {"captions": False}, {"caption_kinds": ["asr"]},
                                     {"caption_formats": ["vtt"]}])
def test_options_of_tool_tutubo_are_refused_for_yt_dlp(index, ytdlp, media, options):
    with pytest.raises(dl.DownloadRequestError):
        dl.Downloads(index).queue("data", KEY, **options)


def test_tutubo_downloads_need_the_program(index, media, monkeypatch, tmp_path):
    monkeypatch.setenv(tutubofetch.ENV_COMMAND, str(tmp_path / "missing"))
    with pytest.raises(dl.ToolMissing):
        fetch(dl.Downloads(index))


def test_the_same_video_in_one_folder_is_one_download_per_tool(lane, ytdlp, media):
    shared_pause()
    a = fetch(lane, dest="c")
    assert fetch(lane, dest="c")["id"] == a["id"]
    assert lane.queue("data", KEY, dest="c")[0]["id"] != a["id"]


# -- the HTTP API ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, fake, media, monkeypatch):
    monkeypatch.delenv(auth.ENV_KEY, raising=False)
    monkeypatch.delenv(auth.ENV_EXEMPT, raising=False)
    db_path = str(tmp_path / "api.json")
    Settings(indexdb.open_index(db_path)).set("youtube_request_spacing", 0)
    with TestClient(create_app(db_path)) as c:
        yield c


def api_wait(client, download_id, *statuses):
    wait_for(lambda: client.get(f"/api/v1/downloads/{download_id}").json()["status"] in statuses)
    return client.get(f"/api/v1/downloads/{download_id}").json()


def test_api_fetch_answers_paths_relative_to_the_root(client, media):
    r = client.post("/api/v1/downloads", json={"entry": KEY, "tool": "tutubo", "root": 0, "dest": "ds"},
                    headers={"X-Archivist-App": "data"})
    assert r.status_code == 201, r.text
    done = api_wait(client, r.json()["id"], "completed", "failed")
    assert done["complete"] is True and done["root"] == 0 and done["dest"] == "ds"
    assert f"ds/audio/{VID}.m4a" in [i["path"] for i in done["file_details"]]
    listing = client.get("/api/v1/downloads", headers={"X-Archivist-App": "data"}).json()
    assert listing["pauses"]["tutubo"] == {"paused": False, "retry_at": None, "reason": None}


def test_api_refuses_bad_bodies(client, media):
    for body in ({"entry": KEY, "tool": "wget"}, {"entry": KEY, "tool": "tutubo", "root": "0"},
                 {"entry": KEY, "tool": "tutubo", "audio": "yes"}, {"entry": KEY, "extra": 1},
                 {"entry": KEY, "tool": "tutubo", "caption_kinds": ["asr"] * 11}):
        assert client.post("/api/v1/downloads", json=body).status_code == 422, body
    for body in ({"entry": KEY, "tool": "tutubo", "dest": "../x"},
                 {"entry": KEY, "tool": "tutubo", "format": "bestaudio"},
                 {"entry": KEY, "tool": "tutubo", "root": 9}):
        assert client.post("/api/v1/downloads", json=body).status_code == 400, body
    assert client.get("/api/v1/downloads?app=all").json()["downloads"] == []


def test_api_batch_queues_all_or_nothing(client, media):
    shared_pause()
    entries = [f"youtube:{vid(n)}" for n in range(3)]
    bad = client.post("/api/v1/downloads/batch",
                      json={"entries": entries + ["youtube:../../etc"], "tool": "tutubo", "dest": "b"})
    assert bad.status_code == 404
    assert client.get("/api/v1/downloads?app=all").json()["downloads"] == []
    r = client.post("/api/v1/downloads/batch", json={"entries": entries + [entries[0]], "tool": "tutubo",
                                                     "dest": "b"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] == 3 and [d["created"] for d in body["downloads"]] == [True, True, True, False]
    assert body["downloads"][3]["id"] == body["downloads"][0]["id"]
    assert body["pauses"]["tutubo"]["paused"] is True
    again = client.post("/api/v1/downloads/batch", json={"entries": entries, "tool": "tutubo", "dest": "b"})
    assert again.status_code == 200 and again.json()["created"] == 0


def test_api_batch_size_and_the_queue_cap(client, media, monkeypatch):
    shared_pause()
    too_many = [f"youtube:{vid(n)}" for n in range(dl.MAX_BATCH + 1)]
    assert client.post("/api/v1/downloads/batch",
                       json={"entries": too_many, "tool": "tutubo"}).status_code == 422
    assert client.post("/api/v1/downloads/batch", json={"entries": [], "tool": "tutubo"}).status_code == 422
    full = client.post("/api/v1/downloads/batch", json={"entries": too_many[:dl.MAX_BATCH], "tool": "tutubo"})
    assert full.status_code == 201 and full.json()["created"] == dl.MAX_BATCH
    over = client.post("/api/v1/downloads/batch", json={"entries": too_many[dl.MAX_BATCH:], "tool": "tutubo"})
    assert over.status_code == 429
    page = client.get("/api/v1/downloads?app=all&limit=1000&status=queued").json()["downloads"]
    assert len(page) == dl.MAX_BATCH


def test_api_concurrent_identical_requests_make_one_download(client, media):
    shared_pause()
    answers = []

    def post():
        answers.append(client.post("/api/v1/downloads", json={"entry": KEY, "tool": "tutubo", "dest": "c"}))

    threads = [threading.Thread(target=post) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(a.status_code for a in answers) == [200] * 7 + [201]
    assert len({a.json()["id"] for a in answers}) == 1


def test_api_roots_lists_each_root_by_position(client, media):
    roots = client.get("/api/v1/downloads/roots").json()["roots"]
    assert roots == [{"root": 0, "path": str(media), "exists": True, "free_bytes": roots[0]["free_bytes"]}]
    assert isinstance(roots[0]["free_bytes"], int)


def test_api_backoff_shows_the_shared_pause(client, media):
    shared_pause("download")
    shared = client.get("/api/v1/youtube/backoff").json()["shared"]
    assert shared["kinds"]["download"]["paused"] is True and shared["kinds"]["details"] == {"paused": False}


def test_api_meta_names_the_features(client):
    features = client.get("/api/v1/meta").json()["features"]
    assert {"downloads_tutubo", "downloads_dest", "downloads_batch"} <= set(features)


def test_api_new_routes_are_behind_the_key(tmp_path, media, fake, monkeypatch):
    key = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv(auth.ENV_KEY, key)
    with TestClient(create_app(str(tmp_path / "k.json")), client=OUTSIDE) as c:
        assert c.get("/api/v1/downloads/roots").status_code == 401
        assert c.post("/api/v1/downloads/batch", json={"entries": [KEY], "tool": "tutubo"}).status_code == 401
        assert c.post("/api/v1/downloads", json={"entry": KEY, "tool": "tutubo"}).status_code == 401
        assert c.get("/api/v1/downloads/roots", headers={"X-Api-Key": key}).status_code == 200


# -- the index -----------------------------------------------------------------------


def test_the_tool_migration_leaves_old_rows_reading_as_yt_dlp(tmp_path, media):
    idx = IndexDB(tmp_path / "old.json")
    with idx.write() as conn:
        conn.execute("INSERT INTO downloads (id, seq, app, entry_id, target_dir, status, created_at,"
                     " updated_at) VALUES ('dl_old', 1, 'a', 'e', ?, 'completed', 'now', 'now')",
                     (str(media / "x"),))
    assert 404 in [m["version"] for m in idx.migrations()]
    record = dl.Downloads(idx).get("dl_old")
    assert record["tool"] == "yt-dlp" and record["fetch"] is None and record["complete"] is None
    assert record["root"] == 0 and record["dest"] == "x"
    idx.close()


# -- staging folders, the record, a cancel while placing ----------------------------


def test_no_download_can_be_placed_in_a_staging_folder_by_target_dir(lane, fake, ytdlp, media):
    shared_pause("details")
    held = fetch(lane, dest="c")
    staging = media / "c" / (tutubofetch.STAGING_PREFIX + VID)
    staging.mkdir(parents=True)
    for folder in (staging, staging / "deeper", media / "c" / ".archivist-dl-x"):
        with pytest.raises(dl.DownloadRequestError, match="staging"):
            lane.queue("other", f"youtube:{vid(1)}", target_dir=str(folder))
    assert [r["id"] for r in lane.list(app=None)[0]] == [held["id"]]


def test_no_download_can_be_placed_in_a_staging_folder_through_a_link(lane, fake, media):
    staging = media / "c" / (tutubofetch.STAGING_PREFIX + VID)
    staging.mkdir(parents=True)
    (media / "s").symlink_to(staging)
    for options in ({"dest": "s"}, {"dest": "s/x"}, {"target_dir": str(media / "s")}):
        with pytest.raises(dl.DownloadRequestError, match="staging"):
            fetch(lane, entry=f"youtube:{vid(1)}", **options)


def test_a_folder_that_became_a_staging_link_is_refused_at_the_start(lane, fake, ytdlp, media):
    shared_pause("details")
    fetch(lane, dest="c")
    staging = media / "c" / (tutubofetch.STAGING_PREFIX + VID)
    staging.mkdir(parents=True)
    (staging / "keep").write_bytes(b"kept for the next run")
    (media / "w").mkdir()
    other = lane.queue("other", f"youtube:{vid(1)}", dest="w")[0]
    (media / "w").rmdir()
    (media / "w").symlink_to(staging)
    lane.start()
    done = wait_status(lane, other["id"], "completed", "failed")
    assert done["status"] == "failed" and "staging" in done["error"]
    assert tree(staging) == ["keep"]


def test_a_record_this_server_did_not_write_is_a_conflict_and_nothing_is_written(lane, fake, media):
    record = media / "c" / "meta" / f"{VID}.files.json"
    record.parent.mkdir(parents=True)
    record.write_text("notes of my own")
    queued = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, queued["id"], "completed", "failed")
    assert done["error_kind"] == "conflict" and "files.json" in done["error"]
    assert record.read_text() == "notes of my own"
    assert tree(media / "c") == ["meta", f"meta/{VID}.files.json"]


def test_a_cancel_while_the_files_are_placed_leaves_the_target_as_it_was(lane, fake, media, monkeypatch):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    before = {p: p.read_bytes() for p in (media / "c").rglob("*") if p.is_file()}
    entered, release = threading.Event(), threading.Event()
    real = tutubofetch.write_record

    def held(*args, **kwargs):
        record = real(*args, **kwargs)
        entered.set()
        release.wait(10)
        return record

    monkeypatch.setattr(tutubofetch, "write_record", held)
    (media / "c" / "captions" / f"{VID}.pt.asr.vtt").unlink()
    del before[media / "c" / "captions" / f"{VID}.pt.asr.vtt"]
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c")
    assert entered.wait(10)
    assert lane.cancel(second["id"])["status"] == "cancelled"
    release.set()
    wait_for(lambda: not list((media / "c").glob(".archivist-*")))
    time.sleep(0.2)
    after = {p: p.read_bytes() for p in (media / "c").rglob("*") if p.is_file()}
    assert after == before
    assert lane.get(second["id"])["status"] == "cancelled"


def test_a_cancel_while_a_first_fetch_is_placed_leaves_no_file(lane, fake, media, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = tutubofetch.write_record

    def held(*args, **kwargs):
        record = real(*args, **kwargs)
        entered.set()
        release.wait(10)
        return record

    monkeypatch.setattr(tutubofetch, "write_record", held)
    queued = fetch(lane, dest="c")
    lane.start()
    assert entered.wait(10)
    lane.cancel(queued["id"])
    release.set()
    wait_for(lambda: not list((media / "c").glob(".archivist-*")))
    time.sleep(0.2)
    assert tree(media / "c") == []


def snapshot(folder: Path):
    return {str(p.relative_to(folder)): p.read_bytes() if p.is_file() else None for p in folder.rglob("*")}


def folder_record(media: Path, dest: str = "c"):
    return json.loads((media / dest / "meta" / f"{VID}.files.json").read_text())


VTT = f"captions/{VID}.pt.asr.vtt"
JSON3 = f"captions/{VID}.pt.asr.json3"


def test_two_requests_for_one_folder_keep_each_others_files_in_the_record(lane, fake, media):
    first = lane.queue("app-a", KEY, tool="tutubo", dest="c", caption_formats=["json3", "vtt"])[0]
    lane.start()
    wait_status(lane, first["id"], "completed")
    other = lane.queue("app-b", KEY, tool="tutubo", dest="c", caption_formats=["json3"])[0]
    done = wait_status(lane, other["id"], "completed", "failed")
    assert done["status"] == "completed" and done["complete"] is True
    record = folder_record(media)
    assert VTT in [item["path"] for item in record["files"]]
    assert VTT not in record["latest"] and JSON3 in record["latest"]
    assert not any(p.endswith(".vtt") for p in done["paths"])
    assert [d["path"] for d in done["file_details"] if d["path"].endswith(".vtt")] == []
    fake.set("ok", edition=" (updated)")
    again = lane.queue("app-a", KEY, tool="tutubo", dest="c", caption_formats=["json3", "vtt"])[0]
    done = wait_status(lane, again["id"], "completed", "failed")
    assert (done["status"], done["error_kind"], done["complete"]) == ("completed", None, True)
    assert (media / "c" / VTT).read_bytes().endswith(b" (updated)")
    described = {item["path"]: item["sha256"] for item in folder_record(media)["files"]}
    assert described[VTT] == sha(media / "c" / VTT)


def test_one_app_changing_its_options_and_back_keeps_working(lane, fake, media):
    first = fetch(lane, dest="c", caption_formats=["json3", "vtt"])
    lane.start()
    wait_status(lane, first["id"], "completed")
    fewer = fetch(lane, dest="c", caption_formats=["json3"])
    assert wait_status(lane, fewer["id"], "completed", "failed")["complete"] is True
    fake.set("ok", edition=" (updated)")
    back = fetch(lane, dest="c", caption_formats=["json3", "vtt"])
    done = wait_status(lane, back["id"], "completed", "failed")
    assert (done["status"], done["complete"]) == ("completed", True)
    assert sorted(item["path"] for item in folder_record(media)["files"]) == sorted(
        [f"audio/{VID}.m4a", JSON3, VTT, f"meta/{VID}.json"])
    assert len(fake.calls()) == 3


def test_a_file_the_record_lists_that_changed_is_no_longer_listed(lane, fake, media):
    first = fetch(lane, dest="c", caption_formats=["json3", "vtt"])
    lane.start()
    wait_status(lane, first["id"], "completed")
    (media / "c" / VTT).write_text("my own edit")
    fewer = fetch(lane, dest="c", caption_formats=["json3"])
    wait_status(lane, fewer["id"], "completed")
    assert VTT not in [item["path"] for item in folder_record(media)["files"]]
    back = fetch(lane, dest="c", caption_formats=["json3", "vtt"])
    done = wait_status(lane, back["id"], "completed", "failed")
    assert done["error_kind"] == "conflict"
    assert (media / "c" / VTT).read_text() == "my own edit"


def test_an_error_after_the_record_is_written_puts_the_target_back(lane, fake, media, monkeypatch):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    before = snapshot(media / "c")

    def broken(self, *args, **kwargs):
        raise OSError(5, "injected read error")

    monkeypatch.setattr(dl.Downloads, "_complete_fetch", broken)
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c", caption_formats=["json3"])
    assert wait_status(lane, second["id"], "completed", "failed")["status"] == "failed"
    assert snapshot(media / "c") == before


def test_a_file_at_an_older_set_aside_name_is_never_touched(lane, fake, media):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    mine = media / "c" / "audio" / f".{VID}.m4a.archivist-old"
    mine.write_bytes(b"a file of my own")
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c", caption_formats=["json3"])
    assert wait_status(lane, second["id"], "completed", "failed")["status"] == "completed"
    assert mine.read_bytes() == b"a file of my own"
    assert (media / "c" / "audio" / f"{VID}.m4a").read_bytes() == b"newer audio"


def test_a_refetch_leaves_no_set_aside_file_or_journal(lane, fake, media):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c", caption_formats=["json3"])
    wait_status(lane, second["id"], "completed")
    assert [p for p in tree(media / "c") if "archivist" in p] == []
    assert (media / "c" / "audio" / f"{VID}.m4a").read_bytes() == b"newer audio"


crash = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")


def crash_during(monkeypatch, name):
    """Make the next placement stop at ``name`` as a killed process would: nothing is undone."""
    monkeypatch.setattr(tutubofetch.Placement, "undo", lambda self: None)

    def killed(*args, **kwargs):
        raise SystemExit("killed")

    if name == "write_record":
        monkeypatch.setattr(tutubofetch, "write_record", killed)
    else:
        monkeypatch.setattr(dl.Downloads, name, killed)


def crashed_refetch(index, fake, media, monkeypatch, where, first_formats=None, second_formats=("json3",)):
    """A completed fetch, then a refetch with other options whose process dies at ``where``.

    ``where`` names a step for :func:`crash_during`, or is a callable that
    patches the crash in itself.
    """
    first_lane = dl.Downloads(index)
    first = fetch(first_lane, dest="c", **({"caption_formats": first_formats} if first_formats else {}))
    first_lane.start()
    wait_status(first_lane, first["id"], "completed")
    first_lane.stop(timeout=10)
    before = snapshot(media / "c")
    fake.set("ok", audio="newer audio")
    with monkeypatch.context() as patched:
        if callable(where):
            patched.setattr(tutubofetch.Placement, "undo", lambda self: None)
            where(patched)
        else:
            crash_during(patched, where)
        crashed = dl.Downloads(index)
        second = fetch(crashed, dest="c", caption_formats=list(second_formats))
        crashed.start()
        wait_for(lambda: crashed.get(second["id"])["status"] != "queued" and not crashed._jobs)
        crashed.stop(timeout=10)
    return before, second


@crash
def test_recovery_undoes_a_placement_that_crashed_before_its_record(index, fake, media, monkeypatch):
    before, _ = crashed_refetch(index, fake, media, monkeypatch, "write_record")
    assert [p for p in tree(media / "c") if "archivist-old" in p]
    dl.Downloads(index).recover(media / "c", VID)
    assert snapshot(media / "c") == before


@crash
def test_recovery_keeps_a_placement_that_crashed_after_its_record(index, fake, media, monkeypatch):
    crashed_refetch(index, fake, media, monkeypatch, "_complete_fetch")
    assert [p for p in tree(media / "c") if "archivist-old" in p]
    dl.Downloads(index).recover(media / "c", VID)
    assert [p for p in tree(media / "c") if "archivist" in p] == []
    assert (media / "c" / "audio" / f"{VID}.m4a").read_bytes() == b"newer audio"
    assert tutubofetch.already_complete(media / "c", VID, folder_record(media)["request"]) is not None


@crash
def test_recovery_keeps_a_first_fetch_that_crashed_after_its_record(index, fake, media, monkeypatch):
    with monkeypatch.context() as patched:
        crash_during(patched, "_complete_fetch")
        crashed = dl.Downloads(index)
        first = fetch(crashed, dest="c")
        crashed.start()
        wait_for(lambda: crashed.get(first["id"])["status"] != "queued" and not crashed._jobs)
        crashed.stop(timeout=10)
    assert (media / "c" / "meta" / f".{VID}.archivist-journal.json").exists()
    dl.Downloads(index).recover(media / "c", VID)
    assert [p for p in tree(media / "c") if "archivist" in p] == []
    assert tutubofetch.already_complete(media / "c", VID, folder_record(media)["request"]) is not None


@crash
@pytest.mark.parametrize("where", ["write_record", "_complete_fetch"])
def test_a_download_a_crash_interrupted_completes_after_a_restart_with_no_leftover(index, fake, media,
                                                                                   monkeypatch, where):
    _, second = crashed_refetch(index, fake, media, monkeypatch, where)
    lane = dl.Downloads(index)
    try:
        lane.start()
        done = wait_status(lane, second["id"], "completed", "failed")
    finally:
        lane.stop(timeout=10)
    assert (done["status"], done["complete"]) == ("completed", True)
    assert [p for p in tree(media / "c") if "archivist" in p] == []
    assert (media / "c" / "audio" / f"{VID}.m4a").read_bytes() == b"newer audio"
    assert folder_record(media)["request"]["formats"] == ["json3"]


KILLED_LANE = """
import sys, time
from pathlib import Path
from media_archivist import downloads, tutubofetch, ytclient
from media_archivist.indexdb import IndexDB

work = Path(sys.argv[1])
index = IndexDB(work / "db.json")
ytclient.CLIENT.bind(index)

def held(*args, **kwargs):
    (work / "entered").touch()
    time.sleep(60)

if sys.argv[3] == "write_record":
    tutubofetch.write_record = held
else:
    downloads.Downloads._complete_fetch = held
lane = downloads.Downloads(index)
lane.queue("data", sys.argv[2], tool="tutubo", dest="c", caption_formats=["json3"])
lane.start()
time.sleep(60)
"""


@pytest.mark.parametrize("where", ["write_record", "_complete_fetch"])
def test_a_lane_killed_while_placing_leaves_nothing_behind_after_a_restart(tmp_path, index, fake, media, where):
    import signal
    import subprocess

    first_lane = dl.Downloads(index)
    first = fetch(first_lane, dest="c")
    first_lane.start()
    wait_status(first_lane, first["id"], "completed")
    first_lane.stop(timeout=10)
    fake.set("ok", audio="newer audio")
    child = subprocess.Popen([sys.executable, "-c", KILLED_LANE, str(tmp_path), KEY, where])
    try:
        wait_for(lambda: (tmp_path / "entered").exists() or child.poll() is not None, timeout=30)
        assert (tmp_path / "entered").exists()
    finally:
        os.kill(child.pid, signal.SIGKILL)
        child.wait(10)
    assert [p for p in tree(media / "c") if "archivist-old" in p]
    lane = dl.Downloads(index)
    try:
        lane.start()
        rows = lane.list(app=None)[0]
        second = next(r for r in rows if r["id"] != first["id"])
        done = wait_status(lane, second["id"], "completed", "failed")
    finally:
        lane.stop(timeout=10)
    assert (done["status"], done["complete"]) == ("completed", True)
    assert [p for p in tree(media / "c") if "archivist" in p] == []
    assert (media / "c" / "audio" / f"{VID}.m4a").read_bytes() == b"newer audio"


@crash
def test_recovery_never_puts_an_old_file_over_one_placed_after_the_crash(index, fake, media, monkeypatch):
    crashed_refetch(index, fake, media, monkeypatch, "write_record")
    audio = media / "c" / "audio" / f"{VID}.m4a"
    audio.write_bytes(b"a file of my own")
    dl.Downloads(index).recover(media / "c", VID)
    assert audio.read_bytes() == b"a file of my own"
    assert [p for p in tree(media / "c") if "archivist" in p] == []


def test_a_journal_this_server_did_not_write_is_a_conflict(lane, fake, media):
    journal = media / "c" / "meta" / f".{VID}.archivist-journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"video_id": "otherVideo1", "linked": [], "set_aside": []}')
    queued = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, queued["id"], "completed", "failed")
    assert (done["status"], done["error_kind"]) == ("failed", "conflict")
    assert journal.read_text().startswith('{"video_id": "otherVideo1"')
    assert len(fake.calls()) == 0


def plant_journal(media, body, key=None):
    """Write ``body`` as the journal of VID in folder ``c``, sealed with ``key`` (HMAC-SHA256 of its canonical JSON)."""
    import hmac

    journal = media / "c" / "meta" / f".{VID}.archivist-journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    body = {"video_id": VID, "linked": [], "set_aside": [], **body}
    if key is not None:
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        body["seal"] = hmac.new(bytes.fromhex(key), text.encode(), hashlib.sha256).hexdigest()
    journal.write_text(json.dumps(body))
    return journal


def test_a_well_formed_journal_this_server_did_not_seal_is_a_conflict(lane, fake, media):
    audio = media / "c" / "audio" / f"{VID}.m4a"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"my own recording")
    journal = plant_journal(media, {"linked": [{"path": f"audio/{VID}.m4a", "sha256": sha(audio)}]})
    queued = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, queued["id"], "completed", "failed")
    assert (done["status"], done["error_kind"]) == ("failed", "conflict")
    assert audio.read_bytes() == b"my own recording"
    assert journal.exists() and len(fake.calls()) == 0


def test_a_journal_is_acted_on_only_with_the_key_of_its_own_placement(lane, media):
    folder = Path(os.path.realpath(media / "c"))
    audio = folder / "audio" / f"{VID}.m4a"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"my own recording")
    names_audio = {"linked": [{"path": f"audio/{VID}.m4a", "sha256": sha(audio)}]}

    def refused(journal):
        with pytest.raises(tutubofetch.Conflict):
            lane.recover(folder, VID)
        assert audio.read_bytes() == b"my own recording"
        assert journal.exists()

    elsewhere = lane._new_placement_key(Path(os.path.realpath(media)) / "d", VID)
    refused(plant_journal(media, names_audio, elsewhere))
    own = lane._new_placement_key(folder, VID)
    assert own != elsewhere
    journal = plant_journal(media, {}, own)
    changed = json.loads(journal.read_text())
    changed.update(names_audio)
    journal.write_text(json.dumps(changed))
    refused(journal)
    refused(plant_journal(media, names_audio))
    refused(plant_journal(media, names_audio, elsewhere))
    refused(plant_journal(media, names_audio, tutubofetch.new_key()))


@crash
def test_a_journal_of_a_finished_placement_is_not_acted_on_again(index, fake, media, monkeypatch):
    crashed_refetch(index, fake, media, monkeypatch, "write_record")
    journal = media / "c" / "meta" / f".{VID}.archivist-journal.json"
    copy = journal.read_bytes()
    lane = dl.Downloads(index)
    lane.recover(media / "c", VID)
    audio = media / "c" / "audio" / f"{VID}.m4a"
    audio.write_bytes(b"my own recording")
    journal.write_bytes(copy)
    with pytest.raises(tutubofetch.Conflict):
        lane.recover(media / "c", VID)
    assert audio.read_bytes() == b"my own recording"


@crash
def test_recovery_keeps_a_new_file_edited_after_the_crash(index, fake, media, monkeypatch):
    crashed_refetch(index, fake, media, monkeypatch, "write_record", first_formats=["json3"],
                    second_formats=["json3", "vtt"])
    vtt = media / "c" / VTT
    vtt.write_text("my own correction")
    dl.Downloads(index).recover(media / "c", VID)
    assert vtt.read_text() == "my own correction"


@crash
def test_recovery_never_restores_a_set_aside_copy_whose_bytes_changed(index, fake, media, monkeypatch):
    crashed_refetch(index, fake, media, monkeypatch, "write_record")
    aside = next((media / "c" / "audio").glob(f".{VID}.m4a.archivist-old-*"))
    aside.write_bytes(b"not what was set aside")
    dl.Downloads(index).recover(media / "c", VID)
    assert (media / "c" / "audio" / f"{VID}.m4a").read_bytes() == b"newer audio"


@pytest.mark.parametrize("aside", ["audio/mine.m4a", f"captions/.{VID}.m4a.archivist-old-abc"])
def test_recovery_moves_only_a_set_aside_name_beside_its_file(index, media, aside):
    lane = dl.Downloads(index)
    mine = media / "c" / aside
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"a file of my own")
    (media / "c" / "meta").mkdir()
    plant_journal(media, {"set_aside": [{"aside": aside, "dest": f"audio/{VID}.m4a", "sha256": sha(mine),
                                         "placed": "0" * 64}]},
                  lane._new_placement_key(media / "c", VID))
    lane.recover(media / "c", VID)
    assert mine.read_bytes() == b"a file of my own"
    assert not (media / "c" / "audio" / f"{VID}.m4a").exists()


def test_recovery_moves_and_removes_only_files_of_the_layout(index, media):
    lane = dl.Downloads(index)
    folder = media / "c" / "audio"
    folder.mkdir(parents=True)
    other = folder / "other.txt"
    other.write_bytes(b"other notes")
    old_notes = folder / ".notes.txt.archivist-old-abc"
    old_notes.write_bytes(b"older notes")
    plant_journal(media, {"linked": [{"path": "audio/other.txt", "sha256": sha(other)}],
                          "set_aside": [{"aside": "audio/.notes.txt.archivist-old-abc", "dest": "audio/notes.txt",
                                         "sha256": sha(old_notes), "placed": "0" * 64}]},
                  lane._new_placement_key(media / "c", VID))
    lane.recover(media / "c", VID)
    assert other.read_bytes() == b"other notes"
    assert old_notes.read_bytes() == b"older notes" and not (folder / "notes.txt").exists()


def crash_after(patched, test):
    """Let the next file operation that ``test`` picks happen, then stop as a killed process would."""
    real_link, real_replace = os.link, os.replace

    def linked(src, dst, *args, **kwargs):
        real_link(src, dst, *args, **kwargs)
        if test("link", str(dst)):
            raise SystemExit("killed")

    def replaced(src, dst, *args, **kwargs):
        real_replace(src, dst, *args, **kwargs)
        if test("replace", str(dst)):
            raise SystemExit("killed")

    patched.setattr(tutubofetch.os, "link", linked)
    patched.setattr(tutubofetch.os, "replace", replaced)


@crash
@pytest.mark.parametrize("step", ["link", "replace"])
def test_each_change_is_in_the_journal_before_it_is_made(index, fake, media, monkeypatch, step):
    def kill(patched):
        crash_after(patched, lambda op, dst: op == step and (dst.endswith(".vtt") if step == "link"
                                                             else ".archivist-old-" in dst))

    before, _ = crashed_refetch(index, fake, media, monkeypatch, kill, first_formats=["json3"],
                                second_formats=["json3", "vtt"])
    assert snapshot(media / "c") != before
    dl.Downloads(index).recover(media / "c", VID)
    assert snapshot(media / "c") == before


@crash
def test_a_kill_before_a_set_aside_is_made_leaves_no_empty_file(index, fake, media, monkeypatch):
    real_save = tutubofetch.Placement._save

    def kill(patched):
        def save(self):
            if self.set_aside:
                raise SystemExit("killed")
            real_save(self)
        patched.setattr(tutubofetch.Placement, "_save", save)

    before, _ = crashed_refetch(index, fake, media, monkeypatch, kill)
    dl.Downloads(index).recover(media / "c", VID)
    assert snapshot(media / "c") == before


@crash
def test_a_file_that_vanishes_during_recovery_is_passed_over(index, fake, media, monkeypatch):
    before, _ = crashed_refetch(index, fake, media, monkeypatch, "write_record")
    real = tutubofetch.sha256

    def vanished(path):
        if ".archivist-old-" in str(path) and path.name.startswith(f".{VID}.m4a"):
            path.unlink()
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(tutubofetch, "sha256", vanished)
    dl.Downloads(index).recover(media / "c", VID)
    assert not (media / "c" / "meta" / f".{VID}.archivist-journal.json").exists()


@crash
def test_cancelling_a_download_a_crash_interrupted_puts_the_folder_back(index, fake, media, monkeypatch):
    before, second = crashed_refetch(index, fake, media, monkeypatch, "write_record")
    shared_pause()
    lane = dl.Downloads(index)
    try:
        lane.start()
        wait_for(lambda: lane.get(second["id"])["status"] == "queued")
        assert lane.cancel(second["id"])["status"] == "cancelled"
    finally:
        lane.stop(timeout=10)
    assert snapshot(media / "c") == before


def test_an_error_writing_the_record_puts_the_target_back(lane, fake, media, monkeypatch):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    before = snapshot(media / "c")
    real = tutubofetch.json.dump

    def full(obj, *args, **kwargs):
        if isinstance(obj, dict) and "latest" in obj:
            raise OSError(28, "No space left on device")
        return real(obj, *args, **kwargs)

    monkeypatch.setattr(tutubofetch.json, "dump", full)
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c", caption_formats=["json3"])
    assert wait_status(lane, second["id"], "completed", "failed")["status"] == "failed"
    assert snapshot(media / "c") == before


def test_no_placement_key_outlives_its_placement(lane, fake, media, index):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c", caption_formats=["json3"])
    wait_status(lane, second["id"], "completed")
    assert index.execute("SELECT COUNT(*) AS n FROM download_placements").fetchone()["n"] == 0


@pytest.mark.parametrize("where", ["write_record", "_complete_fetch"])
def test_no_placement_key_outlives_a_placement_undone_after_an_error(lane, fake, media, index, monkeypatch, where):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    before = snapshot(media / "c")
    if where == "write_record":
        real = tutubofetch.json.dump

        def full(obj, *args, **kwargs):
            if isinstance(obj, dict) and "latest" in obj:
                raise OSError(28, "No space left on device")
            return real(obj, *args, **kwargs)

        monkeypatch.setattr(tutubofetch.json, "dump", full)
    else:
        def broken(self, *args, **kwargs):
            raise OSError(5, "injected read error")

        monkeypatch.setattr(dl.Downloads, "_complete_fetch", broken)
    fake.set("ok", audio="newer audio")
    second = fetch(lane, dest="c", caption_formats=["json3"])
    assert wait_status(lane, second["id"], "completed", "failed")["status"] == "failed"
    assert snapshot(media / "c") == before
    assert index.execute("SELECT COUNT(*) AS n FROM download_placements").fetchone()["n"] == 0


def test_cancelling_a_queued_fetch_never_removes_a_folder_outside_the_roots(lane, fake, media, tmp_path):
    shared_pause()
    (media / "c").mkdir()
    queued = fetch(lane, dest="c")
    outside = tmp_path / "outside"
    victim = outside / (tutubofetch.STAGING_PREFIX + VID)
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("not the server's")
    (media / "c").rmdir()
    (media / "c").symlink_to(outside)
    assert lane.cancel(queued["id"])["status"] == "cancelled"
    assert (victim / "keep.txt").read_text() == "not the server's"


# -- what the program's record claims ------------------------------------------------


@pytest.mark.parametrize("mode", ["dirlink", "othermeta"])
def test_a_record_of_another_video_or_a_linked_folder_is_refused(lane, fake, media, mode):
    fake.set(mode)
    queued = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, queued["id"], "completed", "failed")
    assert done["status"] == "failed" and done["error_kind"] == "tool"
    assert not list((media / "c").iterdir())


@pytest.mark.parametrize("mode,missing", [
    ("exit1", None),
    ("liar", "caption pt.asr.vtt: not offered as vtt"),
    ("truncated", "audio/dQw4w9WgXcQ.m4a: the program reported 1000000 bytes and the file holds 20"),
    ("noaudio", "audio: not written (yt-dlp failed)"),
    ("dub", "audio: the track written is not the original one"),
])
def test_a_fetch_is_complete_only_when_nothing_is_missing_and_the_program_succeeded(lane, fake, media,
                                                                                   mode, missing):
    fake.set(mode)
    queued = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, queued["id"], "completed", "failed")
    assert done["status"] == "completed" and done["complete"] is False
    assert done["incomplete"] == ([missing] if missing else [])
    assert json.loads((media / "c" / "meta" / f"{VID}.files.json").read_text())["complete"] is False


def test_the_audio_record_says_whether_it_is_the_original_track(lane, fake, media):
    queued = fetch(lane, dest="c")
    lane.start()
    done = wait_status(lane, queued["id"], "completed")
    audio = next(i for i in done["file_details"] if i["role"] == "audio")
    assert audio["original"] is True and "reported_bytes" not in audio


def test_a_same_size_edit_is_not_taken_for_a_complete_target(lane, fake, media):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    audio = media / "c" / "audio" / f"{VID}.m4a"
    audio.write_bytes(b"X" * audio.stat().st_size)
    again = fetch(lane, dest="c")
    done = wait_status(lane, again["id"], "completed", "failed")
    assert len(fake.calls()) == 2 and done["error_kind"] == "conflict"
    assert audio.read_bytes() == b"X" * audio.stat().st_size


def test_another_request_is_not_taken_for_a_complete_target(lane, fake, media):
    first = fetch(lane, dest="c")
    lane.start()
    wait_status(lane, first["id"], "completed")
    again = fetch(lane, dest="c", caption_formats=["json3"])
    done = wait_status(lane, again["id"], "completed", "failed")
    assert len(fake.calls()) == 2 and done["complete"] is True
    assert done["fetch"]["caption_formats"] == ["json3"]


def test_a_cancel_stops_the_programs_the_fetch_program_started(lane, fake, media):
    fake.set("grandchild")
    queued = fetch(lane, dest="c")
    lane.start()
    pid_file = fake.root / "grandchild.pid"
    wait_for(lambda: pid_file.exists() and pid_file.read_text())
    child = int(pid_file.read_text())
    lane.cancel(queued["id"])

    def gone():
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return True
        with open(f"/proc/{child}/stat") as f:
            return f.read().split()[2] == "Z"
    wait_for(gone)
