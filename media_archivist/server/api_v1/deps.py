# SPDX-License-Identifier: Apache-2.0
"""What every ``/api/v1`` module shares: the index, the caller's app label.

Modules import from here, never from the package ``__init__``, which imports
them. A module that adds a feature names it in :data:`FEATURES` so
``GET /api/v1/meta`` reports it.
"""
from __future__ import annotations

from typing import Set

APP_HEADER = "x-archivist-app"
DEFAULT_APP = "user"

FEATURES: Set[str] = {"healthz", "meta"}


def get_index(request):
    """The :class:`~media_archivist.indexdb.IndexDB` of the served database."""
    return request.app.state.index


def get_db_path(request) -> str:
    """The path of the served JSON database."""
    return request.app.state.db_path


def app_label(request) -> str:
    """The calling app's name from ``X-Archivist-App``; ``user`` without it.

    A label, not a credential: it decides whose queue and history a request
    joins, never what it may do.
    """
    return (request.headers.get(APP_HEADER) or "").strip() or DEFAULT_APP
