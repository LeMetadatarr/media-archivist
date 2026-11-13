# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/youtube/backoff`` and ``/api/v1/settings``.

The back-off shows every kind's bot-check pause and ends one by hand; the
settings are the run-time settings of :mod:`media_archivist.settings`.
Both read and write the served working index, which the process-wide
YouTube client is bound to, so a change applies to the next request.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from media_archivist.backoff import KINDS, Backoff
from media_archivist.server.api_v1 import deps
from media_archivist.settings import Settings

router = APIRouter()

deps.FEATURES.update({"youtube_backoff", "settings"})


def _backoff_state(index):
    return {"kinds": Backoff(index).blocked(),
            "ip_family": Settings(index).get("youtube_ip_family")}


@router.get("/youtube/backoff")
def get_backoff(request: Request):
    return _backoff_state(deps.get_index(request))


def _clear(request: Request, kind: str):
    if kind not in KINDS:
        raise HTTPException(404, f"unknown back-off kind {kind!r}")
    index = deps.get_index(request)
    Backoff(index).mark_clear(kind)
    lane = getattr(request.app.state, "downloads", None)
    if kind == "download" and lane is not None:
        lane.wake()
    return _backoff_state(index)


@router.post("/youtube/backoff/{kind}/clear")
def clear_backoff(kind: str, request: Request):
    return _clear(request, kind)


@router.delete("/youtube/backoff/{kind}")
def delete_backoff(kind: str, request: Request):
    return _clear(request, kind)


@router.get("/settings")
def get_settings(request: Request):
    return Settings(deps.get_index(request)).all()


@router.patch("/settings")
def patch_settings(request: Request, values: dict = Body(...)):
    try:
        return Settings(deps.get_index(request)).update(values)
    except KeyError as e:
        raise HTTPException(400, e.args[0])
    except ValueError as e:
        raise HTTPException(400, str(e))
