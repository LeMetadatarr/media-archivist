"""FastAPI server — exposes the index over HTTP.

FastAPI + uvicorn are optional extras; importing this module lazily
checks for them and raises a helpful error otherwise.
"""
from media_archivist.server.app import create_app, run

__all__ = ["create_app", "run"]
