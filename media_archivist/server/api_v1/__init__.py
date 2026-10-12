# SPDX-License-Identifier: Apache-2.0
"""The versioned app API, mounted at ``/api/v1`` (see ``docs/app-api.md``).

It sits behind the server's API key like every other route;
``GET /api/v1/healthz`` is open, as ``GET /healthz`` is. Each area of the
API is a module in this package with its own ``router`` and one
``router.include_router(...)`` line below.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from media_archivist.server.api_v1 import deps, entries, playlists, subscriptions, youtube
from media_archivist.server.api_v1 import downloads, exports, stream
from media_archivist.version import __version__

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/meta")
def meta(request: Request):
    index = deps.get_index(request)
    return {
        "api": "v1",
        "version": __version__,
        "schema_version": index.schema_version,
        "migrations": [m["version"] for m in index.migrations()],
        "features": sorted(deps.FEATURES),
        "status_cards": list(stream.CARD_REASONS),
    }


router.include_router(subscriptions.router)
router.include_router(entries.router)
router.include_router(playlists.router)
router.include_router(youtube.router)
router.include_router(exports.router)
router.include_router(stream.router)
router.include_router(downloads.router)
