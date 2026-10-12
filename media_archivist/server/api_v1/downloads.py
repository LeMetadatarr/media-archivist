# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/downloads``: queue, status, history and cancel (see :mod:`media_archivist.downloads`)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from media_archivist import downloads as downloads_mod
from media_archivist.server.api_v1 import deps

router = APIRouter()
deps.FEATURES.add("downloads")


class DownloadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1, max_length=500)
    target_dir: Optional[str] = Field(default=None, max_length=4096)
    filename: Optional[str] = None
    filename_template: Optional[str] = None
    format: Optional[str] = None
    subtitles: Optional[List[str]] = None


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
    if body.filename is not None and body.filename_template is not None \
            and body.filename != body.filename_template:
        raise HTTPException(status_code=400, detail="give filename or filename_template, not both")
    manager = get_downloads(request)
    record, created = _errors(lambda: manager.queue(
        deps.app_label(request), body.entry, target_dir=body.target_dir,
        filename=body.filename if body.filename is not None else body.filename_template,
        format=body.format, subtitles=body.subtitles))
    return JSONResponse(record, status_code=201 if created else 200)


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
    return {"downloads": records, "next_cursor": next_cursor}


@router.get("/downloads/{download_id}")
def get_download(download_id: str, request: Request):
    manager = get_downloads(request)
    return _errors(lambda: manager.get(download_id))


@router.delete("/downloads/{download_id}")
def cancel_download(download_id: str, request: Request, delete_files: bool = False):
    manager = get_downloads(request)
    return _errors(lambda: manager.cancel(download_id, delete_files=delete_files))
