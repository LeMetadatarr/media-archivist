# SPDX-License-Identifier: Apache-2.0
"""A stand-in for the yt-dlp program, driven by a JSON control file.

Recorded YouTube answers are read from the folder named by
``FAKE_YTDLP_FIXTURES``.

Installed by the tests as an executable named ``yt-dlp`` on ``PATH``. It
reads ``FAKE_YTDLP_CONTROL`` (``{"mode": ..., "modes": {video_id: mode},
"titles": {video_id: title}}``), appends its argument list as one JSON line
to ``FAKE_YTDLP_LOG`` and behaves as yt-dlp would for the mode:

``ok``           progress lines, the video and one subtitle file per language; with
                 ``--write-info-json`` also its info JSON, whose caption lists are
                 the control file's ``info`` (``subtitles``, ``automatic_captions``;
                 like yt-dlp's cleaned JSON it has no ``requested_subtitles``
                 and, for YouTube, no ``language``) and which then limits the
                 subtitle files to the tracks the flags allow, as yt-dlp would
``gate``         as ``ok``, once ``release-<video_id>`` exists beside the control file
``hang``         a partial file and progress, then sleeps until it is killed
``bot``          the bot-check error recorded from YouTube, exit 1
``unavailable``  a private-video error, exit 1
``network``      a name-resolution error, exit 1
``nofile``       progress and a clean exit, but no video and no done line
``emptyfile``    a zero-byte video, reported as done
``escape``       reports a file outside its output folder as the video

Called with ``-g`` (stream resolution), it prints one direct address for the
video named by its last argument, or fails as the mode says.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

FIXTURES = Path(os.environ["FAKE_YTDLP_FIXTURES"])


def main(argv):
    control_path = Path(os.environ["FAKE_YTDLP_CONTROL"])
    control = json.loads(control_path.read_text())
    with open(os.environ["FAKE_YTDLP_LOG"], "a") as log:
        log.write(json.dumps({"argv": argv, "pid": os.getpid(), "at": time.time()}) + "\n")
    if "-g" in argv:
        video_id = re.search(r"v=([0-9A-Za-z_-]{11})", argv[-1])[1]
        if control.get("modes", {}).get(video_id, control.get("mode", "ok")) == "bot":
            sys.stderr.write((FIXTURES / "yt_dlp_bot_check_error.txt").read_text())
            return 1
        print(f"https://media.example/videoplayback/{video_id}.mp4")
        return 0
    opts = {}
    i = 0
    while i < len(argv):
        if argv[i] == "--":
            url = argv[i + 1]
            break
        if argv[i] in ("-o", "-f", "--print", "--sub-langs", "--progress-template"):
            opts[argv[i]] = argv[i + 1]
            i += 2
            continue
        i += 1
    video_id = re.search(r"v=([0-9A-Za-z_-]{11})", url)[1]
    mode = control.get("modes", {}).get(video_id, control.get("mode", "ok"))
    title = control.get("titles", {}).get(video_id, f"Video {video_id}")
    fields = {"title": title.replace("/", "⧸"), "id": video_id, "uploader": "Some Channel",
              "channel": "Some Channel", "upload_date": "20240503", "ext": "mp4"}

    def expand(template):
        def one(m):
            name = m[1]
            if name == "upload_date>%Y":
                return "2024"
            if name == "upload_date>%Y-%m-%d":
                return "2024-05-03"
            return fields[name]
        return re.sub(r"%\(([^)]+)\)s", one, template).replace("%%", "%")

    video = Path(expand(opts["-o"]))
    if mode == "bot":
        sys.stderr.write((FIXTURES / "yt_dlp_bot_check_error.txt").read_text())
        return 1
    if mode == "unavailable":
        sys.stderr.write(f"ERROR: [youtube] {video_id}: Private video. Sign in if you've been granted access\n")
        return 1
    if mode == "network":
        sys.stderr.write("ERROR: unable to download video data: <urlopen error [Errno -3] "
                         "Temporary failure in name resolution>\n")
        return 1
    print("[youtube] Extracting URL: " + url, flush=True)
    print("ARCHIVIST-PROGRESS 0 1000 NA", flush=True)
    if mode == "hang":
        Path(str(video) + ".part").write_bytes(b"partial")
        print("ARCHIVIST-PROGRESS 500 1000 NA", flush=True)
        while True:
            time.sleep(0.1)
    if mode == "gate":
        release = control_path.parent / f"release-{video_id}"
        deadline = time.time() + 30
        while not release.exists() and time.time() < deadline:
            time.sleep(0.02)
    print("ARCHIVIST-PROGRESS 1000 NA 1000", flush=True)
    if mode == "nofile":
        return 0
    if mode == "escape":
        outside = video.parent.parent.parent / "escaped.mp4"
        outside.write_bytes(b"video")
        done = str(outside)
    else:
        video.write_bytes(b"" if mode == "emptyfile" else b"video bytes")
        done = str(video)
        info = control.get("info")
        langs = opts["--sub-langs"].split(",") if "--sub-langs" in opts else []
        if info is None:
            for lang in langs:
                video.with_suffix(f".{lang}.vtt").write_text("WEBVTT\n")
        else:
            subs, auto = info.get("subtitles", {}), info.get("automatic_captions", {})
            for lang in langs:
                if ("--write-subs" in argv and lang in subs) or ("--write-auto-subs" in argv and lang in auto):
                    video.with_suffix(f".{lang}.vtt").write_text("WEBVTT\n")
            if "--write-info-json" in argv:
                info_path = video.with_suffix(".info.json")
                if control.get("trim_info_json"):
                    info_path = video.with_name(video.stem[:-1] + ".info.json")
                info_path.write_text(json.dumps(
                    {"id": video_id, "title": title, **info}))
                if control.get("decoy_info_json"):
                    video.with_name("A decoy.info.json").write_text("{}")
        if info is None and "--write-info-json" in argv:
            video.with_suffix(".info.json").write_text(json.dumps({"id": video_id, "title": title}))
    print("ARCHIVIST-DONE " + json.dumps({"filepath": done, "title": title, "id": video_id,
                                          "ext": "mp4"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
