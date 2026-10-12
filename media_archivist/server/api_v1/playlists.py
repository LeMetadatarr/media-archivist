# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/playlists``: one playlist and its videos in playlist order."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from media_archivist.server.api_v1 import deps
from media_archivist.ytindex import query

router = APIRouter()
deps.FEATURES.add("playlists")


@router.get("/playlists/{playlist_id:path}")
def get_playlist(playlist_id: str, request: Request):
    out = query.get_playlist(deps.get_index(request), playlist_id)
    if out is None:
        raise HTTPException(404, f"no playlist {playlist_id}")
    return out
