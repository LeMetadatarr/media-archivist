# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/providers``: what is installed, what is enabled, what failed."""
from __future__ import annotations

from fastapi import APIRouter, Request

from media_archivist import plugins
from media_archivist.server.api_v1 import deps
from media_archivist.settings import Settings

router = APIRouter()
deps.FEATURES.add("providers")


@router.get("/providers")
def list_providers(request: Request):
    settings = Settings(deps.get_index(request))
    enabled = set(plugins.enabled_names(settings))
    adult_allowed = set(settings.get("adult_export_providers"))
    reg = plugins.registry()
    loaded = [{"name": p.name, "builtin": p.builtin, "api_version": p.api_version,
               "adult": p.adult, "enabled": p.name in enabled,
               "adult_export": p.adult and p.name in adult_allowed, "error": None}
              for p in reg.providers.values()]
    failed = [{"name": name, "builtin": name in plugins.BUILTIN, "api_version": None,
               "adult": None, "enabled": name in enabled, "adult_export": False, "error": error}
              for name, error in reg.errors.items()]
    return {"api_version": plugins.API_VERSION, "providers": loaded + failed}
