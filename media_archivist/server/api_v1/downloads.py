# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/downloads``: queue, batch, status, history, roots and cancel (see :mod:`media_archivist.downloads`)."""
from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from media_archivist import downloads as downloads_mod
from media_archivist.server.api_v1 import deps

router = APIRouter()
deps.FEATURES.update({"downloads", "downloads_tutubo", "downloads_dest", "downloads_batch"})

Entry = Annotated[str, Field(min_length=1, max_length=500)]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal["yt-dlp", "tutubo"] = "yt-dlp"
    target_dir: Optional[str] = Field(default=None, max_length=4096)
    root: Optional[StrictInt] = None
    dest: Optional[str] = Field(default=None, max_length=downloads_mod.MAX_DEST)
    filename: Optional[str] = None
    filename_template: Optional[str] = None
    format: Optional[str] = None
    subtitles: Optional[List[str]] = None
    subtitles_kinds: Optional[List[str]] = None
    keep_info_json: bool = False
    audio: Optional[StrictBool] = None
    captions: Optional[StrictBool] = None
    caption_kinds: Optional[List[str]] = Field(default=None, max_length=10)
    caption_formats: Optional[List[str]] = Field(default=None, max_length=10)

    def options(self) -> dict:
        if self.filename is not None and self.filename_template is not None \
                and self.filename != self.filename_template:
            raise HTTPException(status_code=400, detail="give filename or filename_template, not both")
        return dict(tool=self.tool, target_dir=self.target_dir, root=self.root, dest=self.dest,
                    filename=self.filename if self.filename is not None else self.filename_template,
                    format=self.format, subtitles=self.subtitles, subtitles_kinds=self.subtitles_kinds,
                    keep_info_json=self.keep_info_json, audio=self.audio, captions=self.captions,
                    caption_kinds=self.caption_kinds, caption_formats=self.caption_formats)


class DownloadBody(Options):
    entry: Entry


class BatchBody(Options):
    entries: List[Entry] = Field(min_length=1, max_length=downloads_mod.MAX_BATCH)


def get_downloads(request: Request) -> downloads_mod.Downloads:
    """The served index's download queue; its lane is started on first use."""
    state = request.app.state
    manager = getattr(state, "downloads", None)
    if manager is None:
        manager = state.downloads = downloads_mod.Downloads(deps.get_index(request))
    manager.start()
    return manager


def install(app) -> None:
    """Run the download lane for the life of ``app``, beside its own lifespan."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a) as state:
            manager = a.state.downloads = downloads_mod.Downloads(a.state.index)
            manager.start()
            try:
                yield state
            finally:
                await asyncio.to_thread(manager.stop)

    app.router.lifespan_context = lifespan


def _errors(call):
    try:
        return call()
    except downloads_mod.UnknownDownload as e:
        raise HTTPException(status_code=404, detail=e.args[0]) from None
    except downloads_mod.QueueFull as e:
        raise HTTPException(status_code=429, detail=str(e)) from None
    except downloads_mod.ToolMissing as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except downloads_mod.DownloadRequestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/downloads")
def queue_download(body: DownloadBody, request: Request):
    options = body.options()
    manager = get_downloads(request)
    record, created = _errors(lambda: manager.queue(deps.app_label(request), body.entry, **options))
    return JSONResponse(record, status_code=201 if created else 200)


@router.post("/downloads/batch")
def queue_downloads(body: BatchBody, request: Request):
    """One download per entry, all with the same options; all are queued or none."""
    options = body.options()
    manager = get_downloads(request)
    queued = _errors(lambda: manager.queue_many(deps.app_label(request), body.entries, **options))
    created = sum(1 for _, c in queued if c)
    return JSONResponse({"downloads": [{**record, "created": c} for record, c in queued],
                         "created": created, "pauses": manager.pauses()},
                        status_code=201 if created else 200)


@router.get("/downloads/roots")
def list_roots(request: Request):
    """The folders downloads may be written under, by position; ``root`` in a request is that position."""
    roots = []
    for position, root in enumerate(downloads_mod.download_roots()):
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            free = None
        roots.append({"root": position, "path": str(root), "exists": root.is_dir(), "free_bytes": free})
    return {"roots": roots}


@router.get("/downloads")
def list_downloads(request: Request, app: Optional[str] = None, status: Optional[str] = None,
                   limit: int = Query(default=100, ge=1, le=1000), cursor: Optional[str] = None):
    """The calling app's downloads, newest first; ``app=all`` is everyone's."""
    if app is None:
        app = deps.app_label(request)
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    manager = get_downloads(request)
    records, next_cursor = _errors(lambda: manager.list(
        app=None if app == "all" else app, statuses=statuses, limit=limit, cursor=cursor))
    return {"downloads": records, "next_cursor": next_cursor, "pauses": manager.pauses()}


@router.get("/downloads/{download_id}")
def get_download(download_id: str, request: Request):
    manager = get_downloads(request)
    return _errors(lambda: manager.get(download_id))


@router.delete("/downloads/{download_id}")
def cancel_download(download_id: str, request: Request, delete_files: bool = False):
    manager = get_downloads(request)
    return _errors(lambda: manager.cancel(download_id, delete_files=delete_files))
