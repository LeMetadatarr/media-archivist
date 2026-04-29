"""FastAPI app factory + uvicorn entry point."""
from __future__ import annotations

import logging
from typing import Optional

LOG = logging.getLogger("media_archivist.server.app")


def _require_fastapi():
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "fastapi + uvicorn are required for the server — "
            "install with `pip install media_archivist[server]`"
        ) from e


def create_app(db_path: str):
    """Build a FastAPI app bound to the DB at ``db_path``."""
    _require_fastapi()
    from fastapi import FastAPI

    from media_archivist.server.routes import register_routes

    app = FastAPI(
        title="media_archivist",
        description="Cross-source media metadata index — HTTP surface.",
    )
    register_routes(app, db_path=db_path)
    return app


def run(db_path: str, *, host: str = "127.0.0.1", port: int = 8000,
        reload: bool = False) -> None:
    """Block on uvicorn serving the app for ``db_path``."""
    _require_fastapi()
    import uvicorn

    app = create_app(db_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
