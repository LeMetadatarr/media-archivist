# SPDX-License-Identifier: Apache-2.0
"""A stand-in for ``python -m tutubo.fetch``, driven by a JSON control file.

Installed by the tests as the program ``MEDIA_ARCHIVIST_TUTUBO_FETCH`` names.
It reads ``FAKE_TUTUBO_CONTROL`` (``{"mode": ..., "modes": {video_id: mode},
"tracks": [[language, kind], ...], "title": ..., "edition": text added to each
caption}``), appends its argument list, its pid and whether ``TUTUBO_BACKOFF``
reached it as one JSON line to
``FAKE_TUTUBO_LOG``, and writes what tutubo's fetch program writes under
``--dest``: ``audio/<id>.m4a``, ``captions/<id>.<lang>.<kind>.<format>`` and
``meta/<id>.json``, printing one JSON object per line. Modes:

``ok``          every file, ``done``, exit 0
``incomplete``  one caption format reported as not offered, ``error`` with the record, exit 1
``skipped``     a live stream that has not started: a record without files, ``skipped``, exit 0
``paused``      records a bot check of ``pause_kind`` (default ``download``) in tutubo's
                shared pause file and exits 75; with ``TUTUBO_BACKOFF=off`` it fails the
                video with the bot-check text instead, as tutubo does
``error``       ``error`` without a record (``error`` text from the control file), exit 1
``usage``       an argument error on stderr, exit 2
``nometa``      ``done`` naming a record it never wrote, exit 0
``escape``      the record names an audio file outside the folder
``symlink``     the audio file is a symbolic link to a file elsewhere
``foreign``     the record names another video's audio file
``wrongrequest`` the record is for other caption kinds
``hang``        a partial audio file and progress, then sleeps until it is killed
``gate``        as ``ok`` once ``release-<video_id>`` exists beside the control file
``dirlink``     ``audio/`` is a symbolic link to a folder elsewhere
``othermeta``   the record says it is another video's
``exit1``       every file and a complete record, but exit 1
``liar``        a record that says complete while a caption failed, exit 0
``truncated``   the record reports more audio bytes than the file holds
``noaudio``     the record's audio has an error and no file, and says complete
``dub``         the record says the audio written is not the original track
``grandchild``  starts a child of its own in its process group, then sleeps until killed

A partial ``audio/<id>.m4a.part`` left by an earlier run is logged as ``resumed``.
"""
import json
import os
import sys
import time
from pathlib import Path


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def write(path, data):
    """Write as tutubo does: a new file renamed over the name, never the old file rewritten in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def main(argv):
    control_path = Path(os.environ["FAKE_TUTUBO_CONTROL"])
    control = json.loads(control_path.read_text())
    opts = {"--kinds": "manual,asr", "--formats": "json3,vtt"}
    flags = set()
    i = 0
    vid = None
    while i < len(argv):
        if argv[i] == "--":
            vid = argv[i + 1]
            break
        if argv[i] in ("--dest", "--kinds", "--formats", "--gap", "--family"):
            opts[argv[i]] = argv[i + 1]
            i += 2
            continue
        flags.add(argv[i])
        i += 1
    dest = Path(opts["--dest"])
    partial = dest / "audio" / f"{vid}.m4a.part"
    with open(os.environ["FAKE_TUTUBO_LOG"], "a") as log:
        log.write(json.dumps({"argv": argv, "pid": os.getpid(), "at": time.time(),
                              "backoff_env": os.environ.get("TUTUBO_BACKOFF"),
                              "resumed": partial.exists()}) + "\n")
    mode = control.get("modes", {}).get(vid, control.get("mode", "ok"))
    kinds = opts["--kinds"].split(",")
    formats = opts["--formats"].split(",")
    audio = "--no-audio" not in flags
    captions = "--no-captions" not in flags
    request = {"audio": audio, "captions": captions, "kinds": kinds, "formats": formats}
    meta_rel = f"meta/{vid}.json"

    if mode == "usage":
        sys.stderr.write("python -m tutubo.fetch: error: unrecognized arguments\n")
        return 2
    if mode == "error":
        emit("error", video_id=vid, error=control.get("error", "Video unavailable. This video is private"))
        return 1
    if mode == "paused":
        from tutubo import backoff
        kind = control.get("pause_kind", "download")
        if not backoff.enabled():
            emit("error", video_id=vid, error="Sign in to confirm you're not a bot")
            return 1
        backoff.shared().record(kind)
        until = backoff.shared().blocked_until(kind)
        emit("paused", video_id=vid, kind=kind,
             until=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(until)),
             error="Sign in to confirm you're not a bot")
        return 75
    if mode == "grandchild":
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"])
        (control_path.parent / "grandchild.pid").write_text(str(child.pid))
        mode = "hang"
    if mode == "hang":
        write(partial, b"partial")
        emit("progress", video_id=vid, bytes=7, total=100)
        while True:
            time.sleep(0.1)
    if mode == "gate":
        while not (control_path.parent / f"release-{vid}").exists():
            time.sleep(0.02)
    if mode == "nometa":
        emit("done", video_id=vid, meta=meta_rel)
        return 0

    record = {"video_id": vid, "title": control.get("title", f"Video {vid}"), "channel_id": "UCx",
              "duration_s": 61, "upload_date": "2024-05-03", "request": request, "complete": True,
              "captions": [], "audio": None, "tools": {"tutubo": "fake", "yt-dlp": "fake"}}
    if mode == "wrongrequest":
        record["request"] = {**request, "kinds": ["manual"]}
    if mode == "othermeta":
        record["video_id"] = "otherVideo1"
    if mode == "skipped":
        record.update(complete=False, skipped="live stream or premiere has not started")
        write(dest / meta_rel, json.dumps(record).encode())
        emit("skipped", video_id=vid, reason=record["skipped"], meta=meta_rel)
        return 0
    if captions:
        for lang, kind in control.get("tracks", [["pt", "asr"]]):
            if kind not in kinds:
                continue
            for n, fmt in enumerate(formats):
                entry = {"language": lang, "kind": kind, "is_translation": False, "format": fmt}
                if mode in ("incomplete", "liar") and n == len(formats) - 1:
                    record["captions"].append({**entry, "file": None, "bytes": None,
                                               "error": f"not offered as {fmt}"})
                    continue
                rel = f"captions/{vid}.{lang}.{kind}.{fmt}"
                body = f"{fmt} caption {lang} {kind} of {vid}{control.get('edition', '')}".encode()
                write(dest / rel, body)
                record["captions"].append({**entry, "file": rel, "bytes": len(body)})
    if audio and mode == "noaudio":
        record["audio"] = {"error": "yt-dlp failed"}
    elif audio:
        rel = f"audio/{vid}.m4a"
        body = control.get("audio", f"audio of {vid}").encode()
        partial.unlink(missing_ok=True)
        emit("progress", video_id=vid, bytes=len(body) // 2, total=len(body))
        if mode == "escape":
            rel = "audio/../../outside.m4a"
            write(dest / ".." / "outside.m4a", body)
        elif mode == "foreign":
            rel = "audio/otherVideo1.m4a"
            write(dest / rel, body)
        elif mode == "dirlink":
            elsewhere = dest.parent.parent / f"elsewhere-{vid}"
            write(elsewhere / f"{vid}.m4a", body)
            (dest / "audio").symlink_to(elsewhere)
        elif mode == "symlink":
            elsewhere = dest.parent.parent / f"elsewhere-{vid}.m4a"
            write(elsewhere, body)
            (dest / "audio").mkdir(parents=True, exist_ok=True)
            (dest / rel).symlink_to(elsewhere)
        else:
            write(dest / rel, body)
        record["audio"] = {"file": rel, "format_id": "140", "ext": "m4a", "codec": "mp4a.40.2",
                           "sample_rate": 44100, "channels": 2, "original": mode != "dub",
                           "bytes": 1000000 if mode == "truncated" else len(body)}
    if mode == "incomplete":
        record["complete"] = False
    write(dest / meta_rel, json.dumps(record).encode())
    if mode in ("incomplete", "exit1"):
        emit("error", video_id=vid, error="1 caption files failed: not offered", meta=meta_rel)
        return 1
    emit("done", video_id=vid, meta=meta_rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
