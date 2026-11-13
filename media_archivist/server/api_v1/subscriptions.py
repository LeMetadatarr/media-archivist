# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/subscriptions``: the channels and playlists the index follows."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from media_archivist import subscriptions as subs
from media_archivist import ytgate
from media_archivist.server.api_v1 import deps
from media_archivist.ytindex import query
from media_archivist.ytindex.client import TutuboClient

router = APIRouter()
deps.FEATURES.add("subscriptions")

_CREATE_FIELDS = {"url", "label", "index_playlists", "export"}


async def json_body(request: Request) -> Dict[str, Any]:
    """The request's JSON object; ``400`` for anything else."""
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "the body is not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "the body must be a JSON object")
    return body


def indexer_parts(request: Request):
    """The YouTube client and gate the server's indexer uses."""
    periodic = request.app.state.periodic
    return periodic.index_client or TutuboClient(), periodic.index_gate or ytgate.get()


def _not_found(sid: str) -> HTTPException:
    return HTTPException(404, f"no subscription {sid}")


@router.get("/subscriptions")
def list_subscriptions(request: Request):
    return {"subscriptions": subs.list_indexed(deps.get_index(request))}


@router.post("/subscriptions")
async def create_subscription(request: Request):
    body = await json_body(request)
    unknown = set(body) - _CREATE_FIELDS
    if unknown:
        raise HTTPException(400, f"unknown field(s) {', '.join(sorted(unknown))}")
    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(400, "url is required")
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        raise HTTPException(400, "label must be text or null")
    index_playlists = body.get("index_playlists")
    if index_playlists is not None and not isinstance(index_playlists, bool):
        raise HTTPException(400, "index_playlists must be true or false")
    export = body.get("export")
    if export is not None and not isinstance(export, dict):
        raise HTTPException(400, "export must be an object")
    client, gate = indexer_parts(request)

    def resolve(u: str):
        return gate.call("listing", client.read_channel, u).channel_id

    def create():
        return subs.create_indexed(deps.get_index(request), url, app=deps.app_label(request),
                                   label=label, index_playlists=index_playlists, export=export,
                                   resolve=resolve)

    try:
        sub, created = await asyncio.to_thread(create)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if created:
        try:
            request.app.state.periodic.queue_listing(sub["id"])
        except asyncio.QueueFull:
            pass
    return JSONResponse(sub, status_code=201 if created else 200)


@router.get("/subscriptions/{sid:path}/playlists")
def subscription_playlists(sid: str, request: Request):
    out = query.channel_playlists(deps.get_index(request), sid)
    if out is None:
        raise _not_found(sid)
    return out


@router.get("/subscriptions/{sid:path}/thumbnail")
def subscription_thumbnail(sid: str, request: Request):
    row = deps.get_index(request).execute("SELECT thumbnail FROM subscriptions WHERE id = ?",
                                          (sid,)).fetchone()
    if row is None:
        raise _not_found(sid)
    url = row["thumbnail"] or ""
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(404, "no picture yet")
    return RedirectResponse(url, status_code=302)


@router.post("/subscriptions/{sid:path}/refresh")
def refresh_subscription(sid: str, request: Request):
    try:
        sub = subs.request_listing(deps.get_index(request), sid)
    except subs.SubscriptionNotFound:
        raise _not_found(sid) from None
    if sub["kind"] not in ("channel", "playlist"):
        raise HTTPException(409, "only YouTube channels and playlists are indexed")
    try:
        task = request.app.state.periodic.queue_listing(sid)
    except asyncio.QueueFull:
        raise HTTPException(429, "the task queue is full; the listing runs at the next pass") from None
    return JSONResponse({"task": task.model_dump(mode="json")}, status_code=202)


@router.get("/subscriptions/{sid:path}")
def get_subscription(sid: str, request: Request):
    try:
        return subs.get_indexed(deps.get_index(request), sid)
    except subs.SubscriptionNotFound:
        raise _not_found(sid) from None


@router.patch("/subscriptions/{sid:path}")
async def patch_subscription(sid: str, request: Request):
    body = await json_body(request)
    try:
        return await asyncio.to_thread(subs.update_indexed, deps.get_index(request), sid, body)
    except subs.SubscriptionNotFound:
        raise _not_found(sid) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/subscriptions/{sid:path}")
def delete_subscription(sid: str, request: Request, all_holders: bool = False):
    index = deps.get_index(request)
    try:
        removed = subs.release_indexed(index, sid, deps.app_label(request),
                                       all_holders=all_holders)
    except subs.SubscriptionNotFound:
        raise _not_found(sid) from None
    holders = [] if removed else subs.get_indexed(index, sid)["holders"]
    return {"id": sid, "removed": removed, "holders": holders}
