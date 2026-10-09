"""FastAPI app factory + uvicorn entry point."""
from __future__ import annotations

import logging
from pathlib import Path

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
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates

    from media_archivist.server.routes import register_routes
    from media_archivist.server.web import register_web

    app = FastAPI(
        title="media_archivist",
        description="Cross-source media metadata index — HTTP surface.",
    )
    scheduler = register_routes(app, db_path=db_path)

    server_dir = Path(__file__).parent
    static_dir = server_dir / "static"
    templates_dir = server_dir / "templates"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

    def _safe_url(value):
        """Only allow http(s) URLs through to href/src attributes.

        Blocks ``javascript:``/``data:``/other scheme injection from
        ingested provider data (stored XSS hardening).
        """
        if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
            return value
        return ""

    templates.env.filters["safe_url"] = _safe_url

    register_web(app, db_path=db_path, templates=templates, scheduler=scheduler)

    return app


def run(db_path: str, *, host: str = "127.0.0.1", port: int = 8000,
        reload: bool = False) -> None:
    """Block on uvicorn serving the app for ``db_path``."""
    _require_fastapi()
    import uvicorn

    app = create_app(db_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
