"""HTTP routes for the media_archivist server.

Routes are mounted onto a FastAPI ``app`` by :func:`register_routes`.
All bodies / responses use pydantic models from
:mod:`media_archivist.models.api`.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional

LOG = logging.getLogger("media_archivist.server.routes")

# Imported at module scope (not inside register_routes, unlike most fastapi
# symbols here) because `from __future__ import annotations` makes route
# signatures resolve their type hints against *module* globals -- a
# ``Request`` type hint only visible inside register_routes's local scope
# fails that resolution and silently degrades to a query-param model.
from fastapi import Request  # noqa: E402

# Truthy env var strings, matching the shell/CI convention used elsewhere
# in the codebase (1/true/yes/on, case-insensitive).
_TRUTHY = {"1", "true", "yes", "on"}


def _env_strm_resolve_default() -> bool:
    """Default for the ``resolve`` query param on ``/strm/{id}``.

    ``MEDIA_ARCHIVIST_STRM_RESOLVE`` lets an operator turn on yt-dlp
    resolution for every ``.strm`` request (e.g. a Jellyfin library that
    has no yt-dlp plugin of its own) without every caller having to pass
    ``?resolve=1``.
    """
    return os.environ.get("MEDIA_ARCHIVIST_STRM_RESOLVE", "").strip().lower() in _TRUTHY


def _env_strm_proxy_default() -> bool:
    """Default for the ``mode=proxy`` query param on ``/strm/{id}``.

    ``MEDIA_ARCHIVIST_STRM_PROXY`` lets an operator force byte-proxying
    for every resolved ``.strm`` request (e.g. players that don't follow
    redirects, or a CDN the player box can't reach directly) without
    every caller having to pass ``?mode=proxy``.
    """
    return os.environ.get("MEDIA_ARCHIVIST_STRM_PROXY", "").strip().lower() in _TRUTHY

from media_archivist.canonicalize import (
    canonicalize as run_canonicalize,
    load_canonical,
    load_quarantine,
    quarantine_reject,
    quarantine_resolve,
    render_conflict,
)
from media_archivist.index import Index, WhereError
from media_archivist.models.api import (
    ArchiveRequest,
    CanonicalizeRequest,
    CanonicalizeResponse,
    CollectionCreateRequest,
    CollectionDeleteRequest,
    CollectionEntriesResponse,
    CollectionInfo,
    CollectionListResponse,
    DownloadRequest,
    EntryListResponse,
    HealthResponse,
    ProviderInfo,
    ProvidersResponse,
    QuarantineConflict,
    QuarantineDecisionResponse,
    QuarantineListResponse,
    ReResolveResponse,
    StatsResponse,
    StreamHealthEntry,
    StreamHealthResponse,
    SubscriptionCreateRequest,
    SubscriptionDeleteRequest,
    SubscriptionInfo,
    SubscriptionListResponse,
    SubscriptionSyncResponse,
    SubscriptionSyncResult,
    Task,
)
from media_archivist.providers import all_providers
from media_archivist.models.canonical import MediaEntry
from media_archivist.server.scheduler import Scheduler
from media_archivist.version import __version__

# A stalled/wedged download (e.g. a CDN socket that never closes) must not
# block every other queued task forever, since the scheduler runs tasks
# strictly sequentially (see Scheduler._run). Bound each archive() call;
# on timeout the task is marked "error" and the queue proceeds to the next
# task. Note asyncio.to_thread() cannot be force-killed once started (the
# underlying thread keeps running the blocking call to completion in the
# background), but wait_for() still frees the scheduler loop to move on to
# the next queued task — that's the intended mitigation, not a true kill.
ARCHIVE_TIMEOUT_S = 3600

# Same rationale as ARCHIVE_TIMEOUT_S: a stalled download must not
# head-of-line-block the sequential scheduler forever.
DOWNLOAD_TIMEOUT_S = 3600


def register_routes(app, *, db_path: str) -> Scheduler:
    from contextlib import asynccontextmanager

    from fastapi import HTTPException, Query
    from fastapi.responses import (
        JSONResponse,
        PlainTextResponse,
        RedirectResponse,
        Response,
        StreamingResponse,
    )

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
        # block the event loop on network I/O. Bounded by ARCHIVE_TIMEOUT_S
        # so one wedged download can't head-of-line-block every other
        # queued task forever (see module docstring above).
        await asyncio.wait_for(
            asyncio.to_thread(archivist.archive, task.request.url),
            timeout=ARCHIVE_TIMEOUT_S,
        )
        after = len(archivist.video_urls) if hasattr(archivist, "video_urls") else 0
        task.rows_added = max(0, after - before)

    def _make_progress_hook(task_id: str):
        def _hook(d: dict) -> None:
            # Runs on the asyncio.to_thread() worker thread, i.e. off the
            # event loop — TaskStore.update_progress() is the
            # lock-protected, in-memory-only mutator built for exactly
            # this (see its docstring for why it doesn't also save()).
            try:
                status = d.get("status")
                if status == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    downloaded = d.get("downloaded_bytes")
                    if total and downloaded is not None:
                        pct = int(downloaded * 100 / total)
                        scheduler.store.update_progress(task_id, min(99, max(0, pct)))
                elif status == "finished":
                    scheduler.store.update_progress(task_id, 100)
            except Exception:
                LOG.exception("progress hook failed for task %s", task_id)
        return _hook

    async def _download_worker(task: Task) -> None:
        from media_archivist import streams

        request: DownloadRequest = task.request  # type: ignore[assignment]
        idx = Index(db_path)
        entry = idx.get(request.entry_id)
        if entry is None:
            raise ValueError(f"entry not found: {request.entry_id}")
        url = entry.stream or entry.url
        dest_dir = streams.default_download_dir()
        hook = _make_progress_hook(task.id)
        # Same bounded-thread pattern as _archive_worker: run the blocking
        # yt-dlp call in a worker thread, wrapped in wait_for() so a
        # wedged download can't block the sequential scheduler forever.
        # Any StreamDownloadError / asyncio.TimeoutError raised here
        # propagates to Scheduler._run(), which already marks the task
        # "error" and records repr(exc) — no separate try/except needed.
        path = await asyncio.wait_for(
            asyncio.to_thread(
                streams.download,
                url,
                str(dest_dir),
                format=request.format,
                progress_hook=hook,
                timeout=DOWNLOAD_TIMEOUT_S,
            ),
            timeout=DOWNLOAD_TIMEOUT_S,
        )
        task.filepath = str(path)
        task.progress = 100

    async def _dispatch_worker(task: Task) -> None:
        if task.kind == "download":
            await _download_worker(task)
        else:
            await _archive_worker(task)

    scheduler = Scheduler(db_path, _dispatch_worker)

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
        offset: int = Query(default=0, ge=0),
    ) -> EntryListResponse:
        idx = Index(db_path)
        try:
            entries: List[MediaEntry] = idx.to_list(
                source=source, where=where, grep=grep,
                has_stream=has_stream, explicit=explicit, limit=limit,
                offset=offset,
            )
            total = idx.count(
                source=source, where=where, grep=grep,
                has_stream=has_stream, explicit=explicit,
            )
        except WhereError as e:
            raise HTTPException(status_code=400, detail=f"--where: {e}") from None
        return EntryListResponse(total=total, entries=entries, limit=limit, offset=offset)

    @app.get("/entries/{entry_id}", response_model=MediaEntry)
    def get_entry(entry_id: str) -> MediaEntry:
        idx = Index(db_path)
        entry = idx.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="entry not found")
        return entry

    @app.post("/archive", response_model=Task)
    def submit_archive(request: ArchiveRequest) -> Task:
        try:
            return scheduler.submit(request)
        except asyncio.QueueFull:
            raise HTTPException(status_code=429, detail="archive queue full") from None

    @app.post("/entries/{entry_id}/download", response_model=Task)
    def submit_download(entry_id: str) -> Task:
        from media_archivist import streams

        if not streams.ytdlp_available():
            raise HTTPException(
                status_code=503,
                detail="yt-dlp is not available on this server; download is disabled",
            )
        idx = Index(db_path)
        if idx.get(entry_id) is None:
            raise HTTPException(status_code=404, detail="entry not found")
        try:
            return scheduler.submit(DownloadRequest(entry_id=entry_id))
        except asyncio.QueueFull:
            raise HTTPException(status_code=429, detail="download queue full") from None

    @app.get("/tasks/{task_id}", response_model=Task)
    def get_task(task_id: str) -> Task:
        task = scheduler.store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    def _proxy_stream(url: str, range_header: Optional[str]):
        """Stream ``url``'s bytes through us, forwarding ``Range``.

        Belt-and-suspenders path for players that don't follow redirects
        (or can't reach the resolved CDN host directly). Never raises —
        callers must fall back to a redirect on any failure.
        """
        import requests

        headers = {"Range": range_header} if range_header else {}
        upstream = requests.get(url, headers=headers, stream=True, timeout=30)
        upstream.raise_for_status()
        resp_headers = {}
        for h in ("Content-Type", "Content-Range", "Accept-Ranges", "Content-Length"):
            if h in upstream.headers:
                resp_headers[h] = upstream.headers[h]
        status_code = 206 if upstream.status_code == 206 else 200

        def _iter():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            _iter(),
            status_code=status_code,
            media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
            headers=resp_headers,
        )

    @app.api_route("/strm/{entry_id}", methods=["GET", "HEAD"])
    def strm(entry_id: str, request: Request, resolve: Optional[bool] = None,
             mode: Optional[str] = None):
        """Resolve and serve the playable URL/stream for an entry.

        ``.strm`` files are one-line text files whose body is a URL —
        Jellyfin / Kodi read that body at *scan* time. By default (no
        ``resolve``) we return the already-resolved ``stream`` field
        when present (Bandcamp, SoundCloud, IA), otherwise the canonical
        watch URL as plain text, so the client's own resolver (yt-dlp /
        a Jellyfin plugin) handles it — unchanged, classic .strm
        behavior.

        When ``resolve=1`` (or the ``MEDIA_ARCHIVIST_STRM_RESOLVE`` env
        var is truthy), this endpoint becomes a *play-time* yt-dlp hook:
        point a .strm file's body AT this URL
        (``<base_url>/strm/{id}?resolve=1``) and Jellyfin/ffmpeg will
        open it every time the item is played. We resolve a fresh
        direct media URL via yt-dlp on every call and 302-redirect to
        it, so ffmpeg follows the redirect to the live CDN URL instead
        of us handing back inert text a player can't follow as a
        stream. This also means stale/expired stored stream URLs get
        refreshed automatically on every play.

        ``mode=proxy`` (or ``MEDIA_ARCHIVIST_STRM_PROXY``) streams the
        resolved URL's bytes through media-archivist instead of
        redirecting, forwarding the client's ``Range`` header for
        seeking — for players that don't follow redirects or can't
        reach the CDN directly.

        This must never 500: Jellyfin needs a usable response or the
        item breaks in the library. Any resolution/proxy failure falls
        back to a redirect to the unresolved URL, with a logged
        warning.
        """
        idx = Index(db_path)
        entry = idx.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="entry not found")
        fallback = entry.stream or entry.url
        do_resolve = resolve if resolve is not None else _env_strm_resolve_default()
        do_proxy = mode == "proxy" if mode is not None else _env_strm_proxy_default()

        if not do_resolve:
            return PlainTextResponse(fallback, media_type="text/plain")

        from media_archivist import streams

        target = entry.stream or entry.url
        resolved_url: Optional[str] = None
        try:
            resolved = streams.resolve_stream(
                target, source=entry.source.value if entry.source else None
            )
            resolved_url = resolved.url
        except streams.StreamResolveError as e:
            LOG.warning("strm resolve failed for %s (%s) — falling back to "
                        "unresolved url: %s", entry_id, target, e)
        except Exception as e:  # pragma: no cover — defensive, never 500 a .strm
            LOG.warning("strm resolve raised unexpectedly for %s (%s) — "
                        "falling back to unresolved url: %s", entry_id, target, e)

        if resolved_url is None:
            # Best-effort fallback: still a redirect, so the response shape
            # (and what ffmpeg/Jellyfin expect from this endpoint) stays
            # consistent whether or not resolution succeeded.
            return RedirectResponse(url=fallback, status_code=302)

        if do_proxy:
            try:
                return _proxy_stream(resolved_url, request.headers.get("range"))
            except Exception as e:
                LOG.warning("strm proxy failed for %s (%s) — falling back to "
                            "redirect: %s", entry_id, resolved_url, e)
                return RedirectResponse(url=resolved_url, status_code=302)

        return RedirectResponse(url=resolved_url, status_code=302)

    @app.get("/feed.rss", response_class=Response)
    def feed_rss(limit: int = Query(default=50, ge=1, le=500)):
        from xml.sax.saxutils import escape

        idx = Index(db_path)
        items: list[str] = []
        for e in idx.to_list(limit=limit):
            title = escape(e.title or "")
            url = escape(e.url)
            published = escape(e.published or "")
            items.append(
                f"<item><title>{title}</title><link>{url}</link>"
                f"<guid isPermaLink=\"true\">{url}</guid>"
                f"<pubDate>{published}</pubDate></item>"
            )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            '<title>media_archivist</title>'
            f'<link>{escape(os.path.basename(db_path))}</link>'
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

    @app.get("/healthz", response_model=HealthResponse)
    def healthz():
        try:
            idx = Index(db_path)
            len(idx)
            _ = idx.meta
        except Exception:
            unhealthy = HealthResponse(
                status="unhealthy",
                version=__version__,
                db_path=os.path.basename(db_path),
            )
            return JSONResponse(status_code=503, content=unhealthy.model_dump())
        return HealthResponse(version=__version__, db_path=os.path.basename(db_path))

    # Bounds a single /health/streams scan so it can never wedge a request
    # indefinitely against a huge library — same "explicit and limited"
    # posture as the WebUI's page size. Callers that want to check more
    # page through with ?limit=&offset= via --where narrowing, or use the
    # CLI (`media-archivist health`) for a full, unbounded scan.
    HEALTH_SCAN_DEFAULT_LIMIT = 200
    HEALTH_SCAN_MAX_LIMIT = 1000

    @app.get("/health/streams", response_model=StreamHealthResponse)
    def health_streams(
        source: Optional[str] = None,
        where: Optional[str] = None,
        limit: int = Query(default=HEALTH_SCAN_DEFAULT_LIMIT, ge=1,
                           le=HEALTH_SCAN_MAX_LIMIT),
    ) -> StreamHealthResponse:
        from media_archivist import health as health_mod

        try:
            results = health_mod.check_library(
                db_path, source=source, where=where, limit=limit,
            )
        except WhereError as e:
            raise HTTPException(status_code=400, detail=f"--where: {e}") from None
        entries = [StreamHealthEntry(**r.__dict__) for r in results]
        counts = {"ok": 0, "dead": 0, "expired": 0, "no-stream": 0, "gone": 0}
        for e in entries:
            counts[e.status] = counts.get(e.status, 0) + 1
        return StreamHealthResponse(total=len(entries), counts=counts, entries=entries)

    @app.post("/entries/{entry_id}/health/reresolve", response_model=ReResolveResponse)
    def health_reresolve(entry_id: str, dry_run: bool = False) -> ReResolveResponse:
        from media_archivist import health as health_mod

        idx = Index(db_path)
        entry = idx.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="entry not found")
        result = health_mod.reresolve_entry(db_path, entry, dry_run=dry_run)
        return ReResolveResponse(
            entry_id=result.entry_id, ok=result.ok,
            old_stream=result.old_stream, new_stream=result.new_stream,
            error=result.error,
        )

    @app.delete("/entries/{entry_id}/health", response_model=ReResolveResponse)
    def health_remove_gone(entry_id: str) -> ReResolveResponse:
        """Explicit, opt-in removal of an entry confirmed ``gone`` by a scan.

        Destructive — never called by anything else in this module. The
        caller (CLI ``--remove-gone`` / WebUI "Remove" button) is
        responsible for having shown the user a ``status=gone`` result
        first; this endpoint itself does not re-check status, it just
        drops the row on request.
        """
        from media_archivist import health as health_mod

        idx = Index(db_path)
        entry = idx.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="entry not found")
        removed = health_mod.remove_entry(db_path, entry)
        return ReResolveResponse(entry_id=entry_id, ok=removed,
                                 error=None if removed else "entry already removed")

    @app.get("/providers", response_model=ProvidersResponse)
    def providers() -> ProvidersResponse:
        registry = all_providers()
        infos: List[ProviderInfo] = []
        for name, p in sorted(registry.items()):
            try:
                avail = bool(p.is_available())
            except Exception:
                avail = False
            infos.append(ProviderInfo(
                name=name,
                available=avail,
                media=sorted(getattr(m, "value", str(m)) for m in (p.media or set())),
                modality=sorted(getattr(m, "value", str(m)) for m in (getattr(p, "modality", set()) or set())),
                genre_filter=sorted(p.genre_filter or set()),
            ))
        return ProvidersResponse(
            total=len(infos),
            active=sum(1 for i in infos if i.available),
            providers=infos,
        )

    @app.post("/canonicalize", response_model=CanonicalizeResponse)
    async def canonicalize_endpoint(request: CanonicalizeRequest) -> CanonicalizeResponse:
        try:
            canonical, quarantine, entities = await asyncio.to_thread(
                run_canonicalize,
                db_path,
                providers=request.providers,
                stamp_rows=request.stamp_rows,
                max_workers=request.max_workers,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return CanonicalizeResponse(
            canonical_records=len(canonical.records),
            quarantined=len(quarantine.entries),
            entities=len(entities.entities),
        )

    @app.get("/quarantine", response_model=QuarantineListResponse)
    def quarantine_list() -> QuarantineListResponse:
        sidecar = load_quarantine(db_path)
        entries = [
            QuarantineConflict(
                row_id=qe.row_id,
                candidate_canonical_id=qe.candidate_canonical_id,
                conflicts=[render_conflict(c) for c in (qe.conflicts or [])],
            )
            for qe in sidecar.entries.values()
        ]
        return QuarantineListResponse(total=len(entries), entries=entries)

    @app.post("/quarantine/{row_id}/accept", response_model=QuarantineDecisionResponse)
    def quarantine_accept(row_id: str,
                          canonical_id: Optional[str] = None) -> QuarantineDecisionResponse:
        ok = quarantine_resolve(db_path, row_id, canonical_id=canonical_id)
        if not ok:
            raise HTTPException(status_code=404, detail="row not in quarantine")
        return QuarantineDecisionResponse(row_id=row_id, decision="accept", ok=True)

    @app.post("/quarantine/{row_id}/reject", response_model=QuarantineDecisionResponse)
    def quarantine_reject_route(row_id: str) -> QuarantineDecisionResponse:
        ok = quarantine_reject(db_path, row_id)
        if not ok:
            raise HTTPException(status_code=404, detail="row not in quarantine")
        return QuarantineDecisionResponse(row_id=row_id, decision="reject", ok=True)

    @app.get("/subscriptions", response_model=SubscriptionListResponse)
    def subscriptions_list() -> SubscriptionListResponse:
        from media_archivist import subscriptions as subs_mod

        subs = subs_mod.list_subscriptions(db_path)
        return SubscriptionListResponse(
            total=len(subs),
            subscriptions=[SubscriptionInfo(**s.model_dump()) for s in subs],
        )

    @app.post("/subscriptions", response_model=SubscriptionInfo)
    def subscriptions_add(request: SubscriptionCreateRequest) -> SubscriptionInfo:
        from media_archivist import subscriptions as subs_mod

        try:
            sub = subs_mod.add_subscription(
                db_path, request.url, backend=request.backend, label=request.label,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return SubscriptionInfo(**sub.model_dump())

    @app.delete("/subscriptions", response_model=SubscriptionListResponse)
    def subscriptions_remove(request: SubscriptionDeleteRequest) -> SubscriptionListResponse:
        from media_archivist import subscriptions as subs_mod

        ok = subs_mod.remove_subscription(db_path, request.url)
        if not ok:
            raise HTTPException(status_code=404, detail="subscription not found")
        subs = subs_mod.list_subscriptions(db_path)
        return SubscriptionListResponse(
            total=len(subs),
            subscriptions=[SubscriptionInfo(**s.model_dump()) for s in subs],
        )

    @app.post("/subscriptions/sync", response_model=SubscriptionSyncResponse)
    async def subscriptions_sync(dry_run: bool = False) -> SubscriptionSyncResponse:
        from media_archivist import subscriptions as subs_mod

        # Network-bound (each subscription's archive() call) — offload to a
        # worker thread so it doesn't block the event loop, same pattern as
        # /canonicalize and _archive_worker above.
        results = await asyncio.to_thread(subs_mod.sync_all, db_path, dry_run=dry_run)
        return SubscriptionSyncResponse(
            total=len(results),
            results=[SubscriptionSyncResult(**r.model_dump()) for r in results],
        )

    @app.get("/collections", response_model=CollectionListResponse)
    def collections_list() -> CollectionListResponse:
        from media_archivist import collections as coll_mod

        colls = coll_mod.list_collections(db_path)
        infos = []
        for c in colls:
            try:
                n = coll_mod.collection_count(db_path, c)
            except WhereError:
                n = 0
            infos.append(CollectionInfo(**c.model_dump(), count=n))
        return CollectionListResponse(total=len(infos), collections=infos)

    @app.post("/collections", response_model=CollectionInfo)
    def collections_add(request: CollectionCreateRequest) -> CollectionInfo:
        from media_archivist import collections as coll_mod

        try:
            coll = coll_mod.add_collection(
                db_path, request.name, where=request.where, source=request.source,
                grep=request.grep, has_stream=request.has_stream,
                explicit=request.explicit, description=request.description,
            )
        except (ValueError, WhereError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        try:
            n = coll_mod.collection_count(db_path, coll)
        except WhereError:
            n = 0
        return CollectionInfo(**coll.model_dump(), count=n)

    @app.delete("/collections", response_model=CollectionListResponse)
    def collections_remove(request: CollectionDeleteRequest) -> CollectionListResponse:
        from media_archivist import collections as coll_mod

        ok = coll_mod.remove_collection(db_path, request.name)
        if not ok:
            raise HTTPException(status_code=404, detail="collection not found")
        colls = coll_mod.list_collections(db_path)
        infos = []
        for c in colls:
            try:
                n = coll_mod.collection_count(db_path, c)
            except WhereError:
                n = 0
            infos.append(CollectionInfo(**c.model_dump(), count=n))
        return CollectionListResponse(total=len(infos), collections=infos)

    @app.get("/collections/{name}", response_model=CollectionEntriesResponse)
    def collections_entries(name: str,
                            limit: int = Query(default=0, ge=0, le=10_000),
                            offset: int = Query(default=0, ge=0)) -> CollectionEntriesResponse:
        from media_archivist import collections as coll_mod

        coll = coll_mod.get_collection(db_path, name)
        if coll is None:
            raise HTTPException(status_code=404, detail="collection not found")
        try:
            entries = coll_mod.collection_entries(db_path, coll, limit=limit, offset=offset)
            total = coll_mod.collection_count(db_path, coll)
        except WhereError as e:
            raise HTTPException(status_code=400, detail=f"--where: {e}") from None
        return CollectionEntriesResponse(total=total, entries=entries)

    @app.get("/collections/{name}/m3u", response_class=PlainTextResponse)
    def collections_m3u(name: str):
        """Stable per-collection M3U URL — point Jellyfin/Kodi/VLC at this directly.

        Reuses the same rendering as :func:`m3u` above (via
        :func:`media_archivist.collections.build_m3u`) so a collection's
        playlist and the ad-hoc ``/m3u?...`` endpoint look identical to a
        player, but this URL is stable and doesn't require the caller to
        know/repeat the filter.
        """
        from media_archivist import collections as coll_mod

        coll = coll_mod.get_collection(db_path, name)
        if coll is None:
            raise HTTPException(status_code=404, detail="collection not found")
        try:
            entries = coll_mod.collection_entries(db_path, coll)
        except WhereError as e:
            raise HTTPException(status_code=400, detail=f"--where: {e}") from None
        return PlainTextResponse(coll_mod.build_m3u(entries), media_type="audio/x-mpegurl")

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
            db_path=os.path.basename(db_path),
        )

    return scheduler
