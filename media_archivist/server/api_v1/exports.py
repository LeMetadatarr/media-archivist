# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/exports``: write ``.strm`` files on request, and undo them."""
from __future__ import annotations

import threading
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from media_archivist import jellyfin, links, strm
from media_archivist.server.api_v1 import deps

router = APIRouter()
deps.FEATURES.add("exports")


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    scope: Literal["entry", "playlist", "subscription"]
    id: str = Field(min_length=1)
    target_dir: str
    layout: Literal["movies", "episodes"] = "movies"
    links: Literal["plain", "signed"] = "plain"
    refresh_jellyfin: bool = Field(False, alias="jellyfin_refresh")
    collection: bool = False
    base_url: Optional[str] = None


def body_maker(request: Request, kind: str, base_url: Optional[str] = None):
    """``entry -> .strm text`` for links of ``kind`` to this server."""
    base = base_url or links.public_base(str(request.base_url))
    key = links.secret(deps.get_index(request)) if kind == "signed" else None
    return lambda entry: links.stream_url(base, entry.id, key=key) + "\n"


@router.post("/exports", status_code=201)
def create_export(body: ExportRequest, request: Request):
    index = deps.get_index(request)
    try:
        record, job = strm.run_export(
            deps.get_db_path(request), index, scope=body.scope, scope_id=body.id,
            target_dir=body.target_dir, body_for=body_maker(request, body.links, body.base_url),
            layout=body.layout, links=body.links, app=deps.app_label(request),
            refresh_jellyfin=body.refresh_jellyfin, collection=body.collection,
            jellyfin_configured=jellyfin.Jellyfin.from_env() is not None)
    except strm.ExportRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except strm.ExportNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    threading.Thread(target=strm.finish_export, args=(index, job),
                     name=f"export-{job.export_id}", daemon=True).start()
    return strm.export_view(record)


@router.get("/exports/{export_id}")
def read_export(export_id: str, request: Request):
    try:
        return strm.export_view(strm.get_export(deps.get_index(request), export_id))
    except strm.ExportNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.delete("/exports/{export_id}")
def delete_export(export_id: str, request: Request):
    try:
        return strm.delete_export(deps.get_index(request), export_id)
    except strm.ExportNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.delete("/exports")
def delete_exports(scope: Literal["entry", "playlist", "subscription"], id: str,
                   target_dir: str, request: Request):
    """Undo every export of a scope into a folder."""
    index = deps.get_index(request)
    ids = strm.find_exports(deps.get_db_path(request), index, scope, id, target_dir)
    if not ids:
        raise HTTPException(status_code=404, detail="no export of that scope into that folder")
    results = [strm.delete_export(index, export_id) for export_id in ids]
    return {"exports": ids,
            "removed": [p for r in results for p in r["removed"]],
            "kept": [k for r in results for k in r["kept"]]}
