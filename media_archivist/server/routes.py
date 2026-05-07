"""HTTP routes for the media_archivist server.

Routes are mounted onto a FastAPI ``app`` by :func:`register_routes`.
All bodies / responses use pydantic models from
:mod:`media_archivist.models.api`.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from media_archivist.canonicalize import load_canonical, load_quarantine
from media_archivist.index import Index, WhereError
from media_archivist.models.api import (
    ArchiveRequest,
    EntryListResponse,
    StatsResponse,
    Task,
)
from media_archivist.models.canonical import MediaEntry
from media_archivist.server.scheduler import Scheduler
from media_archivist.version import __version__


def register_routes(app, *, db_path: str) -> None:
    from contextlib import asynccontextmanager

    from fastapi import HTTPException, Query
    from fastapi.responses import PlainTextResponse, Response

    async def _archive_worker(task: Task) -> None:
        from media_archivist.bandcamp import BandcampArchivist
        from media_archivist.ia import IAArchivist
        from media_archivist.music import YoutubeMusicArchivist
        from media_archivist.soundcloud import SoundCloudArchivist
        from media_archivist.youtube import YoutubeArchivist

        backend = task.request.backend or "youtube"
        cls = {
            "youtube": YoutubeArchivist,
            "ia": IAArchivist,
            "music": YoutubeMusicArchivist,
            "bandcamp": BandcampArchivist,
            "soundcloud": SoundCloudArchivist,
        }[backend]
        archivist = cls(
            db_path=db_path,
            required_kwords=task.request.require,
            blacklisted_kwords=task.request.blacklist,
            min_duration=task.request.min_duration,
        )
        before = len(archivist.video_urls) if hasattr(archivist, "video_urls") else 0
        # Run the (synchronous) archive call in a worker thread so we don't
        # block the event loop on network I/O.
        await asyncio.to_thread(archivist.archive, task.request.url)
        after = len(archivist.video_urls) if hasattr(archivist, "video_urls") else 0
        task.rows_added = max(0, after - before)

    scheduler = Scheduler(db_path, _archive_worker)

    @asynccontextmanager
    async def _lifespan(_app):
        scheduler.start(asyncio.get_running_loop())
        try:
            yield
        finally:
            await scheduler.stop()

    app.router.lifespan_context = _lifespan

    @app.get("/entries", response_model=EntryListResponse)
    def list_entries(
        source: Optional[str] = None,
        where: Optional[str] = None,
        grep: Optional[str] = None,
        has_stream: Optional[bool] = None,
        explicit: Optional[bool] = None,
        limit: int = Query(default=100, ge=1, le=10_000),
    ) -> EntryListResponse:
        idx = Index(db_path)
        try:
            entries: List[MediaEntry] = idx.to_list(
                source=source, where=where, grep=grep,
                has_stream=has_stream, explicit=explicit, limit=limit,
            )
        except WhereError as e:
            raise HTTPException(status_code=400, detail=f"--where: {e}") from None
        return EntryListResponse(total=len(entries), entries=entries)

    @app.get("/entries/{entry_id}", response_model=MediaEntry)
    def get_entry(entry_id: str) -> MediaEntry:
        idx = Index(db_path)
        for e in idx.view():
            if e.id == entry_id:
                return e
        raise HTTPException(status_code=404, detail="entry not found")

    @app.post("/archive", response_model=Task)
    def submit_archive(request: ArchiveRequest) -> Task:
        return scheduler.submit(request)

    @app.get("/tasks/{task_id}", response_model=Task)
    def get_task(task_id: str) -> Task:
        task = scheduler.store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.get("/strm/{entry_id}", response_class=PlainTextResponse)
    def strm(entry_id: str):
        """Return the playable URL for an entry as plain text.

        ``.strm`` files are one-line text files whose body is a URL —
        Jellyfin / Kodi follow it to the actual stream. We return the
        already-resolved ``stream`` field when present (Bandcamp,
        SoundCloud, IA), otherwise the canonical watch URL so the
        client's resolver (yt-dlp / Jellyfin plugin) handles it.
        """
        idx = Index(db_path)
        for entry in idx.view():
            if entry.id == entry_id:
                return PlainTextResponse(entry.stream or entry.url,
                                         media_type="text/plain")
        raise HTTPException(status_code=404, detail="entry not found")

    @app.get("/feed.rss", response_class=Response)
    def feed_rss(limit: int = Query(default=50, ge=1, le=500)):
        idx = Index(db_path)
        items: list[str] = []
        for e in idx.to_list(limit=limit):
            title = (e.title or "").replace("&", "&amp;").replace("<", "&lt;")
            url = e.url
            published = e.published or ""
            items.append(
                f"<item><title>{title}</title><link>{url}</link>"
                f"<guid isPermaLink=\"true\">{url}</guid>"
                f"<pubDate>{published}</pubDate></item>"
            )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            '<title>media_archivist</title>'
            f'<link>{db_path}</link>'
            '<description>Recently indexed entries.</description>'
            + "".join(items)
            + "</channel></rss>"
        )
        return Response(content=body, media_type="application/rss+xml")

    @app.get("/m3u", response_class=PlainTextResponse)
    def m3u(
        source: Optional[str] = None,
        where: Optional[str] = None,
        has_stream: Optional[bool] = True,
        limit: int = Query(default=200, ge=1, le=10_000),
    ):
        idx = Index(db_path)
        try:
            entries = idx.to_list(source=source, where=where,
                                  has_stream=has_stream, limit=limit)
        except WhereError as e:
            raise HTTPException(status_code=400, detail=f"--where: {e}") from None
        lines = ["#EXTM3U"]
        for e in entries:
            secs = int(e.duration) if e.duration else -1
            artist = e.artist or ""
            title = e.title or e.url
            lines.append(f"#EXTINF:{secs},{artist} - {title}".rstrip(" -"))
            lines.append(e.stream or e.url)
        return PlainTextResponse("\n".join(lines), media_type="audio/x-mpegurl")

    @app.get("/stats", response_model=StatsResponse)
    def stats() -> StatsResponse:
        idx = Index(db_path)
        canonical_n = quarantined_n = 0
        try:
            canonical_n = len(load_canonical(db_path).records)
            quarantined_n = len(load_quarantine(db_path).entries)
        except Exception:
            pass
        return StatsResponse(
            total=len(idx),
            source_mix=dict(idx.meta.source_mix),
            canonical_records=canonical_n,
            quarantined=quarantined_n,
            archivist_version=__version__,
            db_path=db_path,
        )
