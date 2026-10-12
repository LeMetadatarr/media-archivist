# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/entries``: the query over everything the index holds."""
from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request

from media_archivist.server.api_v1 import deps
from media_archivist.server.api_v1.subscriptions import indexer_parts
from media_archivist.ytindex import details, query

router = APIRouter()
deps.FEATURES.add("entries")


def _list(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _time(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return query.parse_time(value)
    except query.QueryError:
        raise HTTPException(400, f"{name} is not an ISO 8601 time") from None


@router.get("/entries")
def list_entries(request: Request, subscription: Optional[str] = None,
                 playlist: Optional[str] = None, since: Optional[str] = None,
                 first_seen_after: Optional[str] = None,
                 min_duration: Optional[float] = None, max_duration: Optional[float] = None,
                 unknown_duration: Optional[str] = None, exclude: Optional[str] = None,
                 vertical: Optional[bool] = None, fields: Optional[str] = None,
                 limit: int = 100, cursor: Optional[str] = None):
    q = query.EntryQuery(
        subscriptions=_list(subscription), playlists=_list(playlist),
        since=_time("since", since), first_seen_after=_time("first_seen_after", first_seen_after),
        min_duration=min_duration, max_duration=max_duration,
        unknown_duration=unknown_duration, exclude=_list(exclude), vertical=vertical,
        fields=_list(fields) or None, limit=limit, cursor=cursor)
    try:
        return query.query_entries(deps.get_index(request), q)
    except query.UnknownId as e:
        raise HTTPException(404, str(e)) from None
    except query.QueryError as e:
        raise HTTPException(400, str(e)) from None


def _lacks_details(entry) -> bool:
    return entry["key"].startswith("youtube:") and (
        entry["duration"] is None or entry["published"] is None or entry["channel"] is None)


@router.get("/entries/{ref:path}")
async def get_entry(ref: str, request: Request):
    index = deps.get_index(request)
    entry = query.get_entry(index, ref)
    if entry is None:
        raise HTTPException(404, f"no entry {ref}")
    if _lacks_details(entry):
        _, gate = indexer_parts(request)
        outcome = await asyncio.to_thread(details.fill, index, entry["id"], gate=gate, wait=False)
        if outcome == "filled":
            entry = query.get_entry(index, entry["id"])
    return entry
