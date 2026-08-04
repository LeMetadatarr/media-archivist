# SPDX-License-Identifier: Apache-2.0
"""Server-rendered htmx WebUI — mounted onto the same FastAPI app as the
JSON API in :mod:`media_archivist.server.routes`.

All pages extend ``base.html``; htmx swaps are served by dedicated
``/ui/...`` fragment routes returning partial templates (no base layout).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Annotated, List, Optional

from media_archivist.canonicalize import (
    canonicalize as run_canonicalize,
    load_quarantine,
    quarantine_reject,
    quarantine_resolve,
    render_conflict,
)
from media_archivist.index import Index, WhereError
from media_archivist.models.api import (
    ArchiveRequest,
    DownloadRequest,
    ProviderInfo,
    QuarantineConflict,
)
from media_archivist.providers import all_providers
from media_archivist.version import __version__
from mediavocab.models.signals import signal_hash

from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse

LOG = logging.getLogger("media_archivist.server.web")


# Video containers commonly served straight from Internet Archive items —
# anything else falls back to an <audio> element.
_IA_VIDEO_EXTS = {"mp4", "webm", "ogv", "ogg", "mov", "m4v"}

_YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:watch\?v=|embed/)"
    r"|music\.youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})"
)
_BARE_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _stream_kind(entry) -> Optional[str]:
    """"audio"/"video"/None — how to render ``entry.stream`` inline."""
    if not entry.stream:
        return None
    source = getattr(entry.source, "value", entry.source)
    if source in ("bandcamp", "soundcloud"):
        return "audio"
    if source == "internet_archive":
        path = entry.stream.split("?", 1)[0].split("#", 1)[0]
        name = path.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        return "video" if (not ext or ext in _IA_VIDEO_EXTS) else "audio"
    return None


def _kind_for_ext(ext: Optional[str]) -> str:
    """"audio"/"video" for a resolved-stream file extension (defaults video)."""
    if ext and ext.lower() not in _IA_VIDEO_EXTS:
        return "audio"
    return "video"


def _youtube_id(entry) -> Optional[str]:
    """Derive the 11-char YouTube video id, or ``None`` if it can't be found."""
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    vid = raw.get("videoId")
    if isinstance(vid, str) and _BARE_YOUTUBE_ID_RE.match(vid):
        return vid
    m = _YOUTUBE_ID_RE.search(entry.url or "")
    return m.group(1) if m else None


def _providers_payload():
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
    return infos


def register_web(app, *, db_path: str, templates, scheduler) -> None:
    def _render(request: Request, name: str, *, status_code: int = 200, **extra):
        root_path = request.scope.get("root_path", "")
        context = {"root_path": root_path, "version": __version__, **extra}
        return templates.TemplateResponse(
            request, name, context, status_code=status_code
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        idx = Index(db_path)
        from media_archivist.canonicalize import load_canonical
        canonical_n = quarantined_n = 0
        try:
            canonical_n = len(load_canonical(db_path).records)
            quarantined_n = len(load_quarantine(db_path).entries)
        except Exception:
            pass
        stats = {
            "total": len(idx),
            "source_mix": dict(idx.meta.source_mix),
            "canonical_records": canonical_n,
            "quarantined": quarantined_n,
        }
        infos = _providers_payload()
        providers = {"total": len(infos), "active": sum(1 for i in infos if i.available)}
        return _render(request, "dashboard.html", active="dashboard",
                       stats=stats, providers=providers)

    @app.get("/ui/health-dot", response_class=HTMLResponse)
    def health_dot(request: Request):
        try:
            idx = Index(db_path)
            len(idx.meta.source_mix)
            healthy = True
        except Exception:
            healthy = False
        return _render(request, "fragments/health_dot.html", healthy=healthy)

    @app.get("/ui/entries", response_class=HTMLResponse)
    def entries_page(request: Request):
        return _render(request, "entries.html", active="entries")

    def _query_entries(source, where, grep, has_stream, explicit, limit, offset):
        idx = Index(db_path)
        entries = idx.to_list(
            source=source or None, where=where or None, grep=grep or None,
            has_stream=has_stream, explicit=explicit, limit=limit, offset=offset,
        )
        total = idx.count(
            source=source or None, where=where or None, grep=grep or None,
            has_stream=has_stream, explicit=explicit,
        )
        return entries, total

    # Default page size for the WebUI table -- deliberately smaller than the
    # API default (100) so 10k+ libraries are actually paged in the browser
    # instead of rendering one giant table.
    _UI_PAGE_SIZE = 50

    @app.get("/ui/entries/table", response_class=HTMLResponse)
    def entries_table(
        request: Request,
        source: Optional[str] = None,
        where: Optional[str] = None,
        grep: Optional[str] = None,
        has_stream: Optional[bool] = None,
        explicit: Optional[bool] = None,
        limit: int = Query(default=_UI_PAGE_SIZE, ge=1, le=10_000),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            entries, total = _query_entries(
                source, where, grep, has_stream, explicit, limit, offset,
            )
        except WhereError as e:
            # 200, not 400: htmx does not swap 4xx bodies by default, so a
            # non-2xx response here would leave the table silently stale
            # instead of showing the user their DSL mistake.
            return _render(request, "fragments/entries_table.html",
                           error=f"where: {e}")
        # Clamp offset display math -- offset itself is already ge=0 via
        # Query(), and an offset past total simply yields an empty page.
        offset = max(0, offset)
        showing_from = min(offset + 1, total) if total else 0
        showing_to = min(offset + len(entries), total)
        prev_offset = max(0, offset - limit)
        next_offset = offset + limit
        has_prev = offset > 0
        has_next = next_offset < total
        return _render(
            request, "fragments/entries_table.html",
            entries=entries, total=total, limit=limit, offset=offset,
            showing_from=showing_from, showing_to=showing_to,
            prev_offset=prev_offset, next_offset=next_offset,
            has_prev=has_prev, has_next=has_next,
            source=source, where=where, grep=grep,
            has_stream=has_stream, explicit=explicit,
        )

    @app.get("/ui/entries/{entry_id}", response_class=HTMLResponse)
    def entry_detail(request: Request, entry_id: str):
        from media_archivist.streams import ytdlp_available

        idx = Index(db_path)
        for e in idx.view():
            if e.id == entry_id:
                return _render(request, "fragments/entry_detail.html", entry=e,
                               stream_kind=_stream_kind(e), yt_id=_youtube_id(e),
                               ytdlp_available=ytdlp_available())
        return _render(request, "fragments/entry_detail.html",
                       error="entry not found", status_code=404)

    @app.post("/ui/entries/{entry_id}/download", response_class=HTMLResponse)
    def entry_download(request: Request, entry_id: str):
        from media_archivist import streams

        if not streams.ytdlp_available():
            return _render(request, "fragments/task_status.html",
                           error="yt-dlp is not available on this server "
                                 "— download is disabled", status_code=503)
        idx = Index(db_path)
        if idx.get(entry_id) is None:
            return _render(request, "fragments/task_status.html",
                           error="entry not found", status_code=404)
        try:
            task = scheduler.submit(DownloadRequest(entry_id=entry_id))
        except asyncio.QueueFull:
            return _render(request, "fragments/task_status.html",
                           error="download queue full", status_code=429)
        return _render(request, "fragments/task_status.html", task=task)

    @app.get("/ui/entries/{entry_id}/player", response_class=HTMLResponse)
    def entry_player(request: Request, entry_id: str):
        idx = Index(db_path)
        for e in idx.view():
            if e.id == entry_id:
                return _render(request, "fragments/player_iframe.html",
                               entry=e, yt_id=_youtube_id(e))
        return _render(request, "fragments/player_iframe.html",
                       error="entry not found", status_code=404)

    @app.get("/ui/entries/{entry_id}/resolve", response_class=HTMLResponse)
    def entry_resolve(request: Request, entry_id: str):
        """yt-dlp-resolved direct stream — lazy, on-demand (never bulk).

        Used both by the "Play (yt-dlp)" affordance on YouTube-family
        entries and the "refresh stream" affordance on entries whose
        stored ``stream`` URL may have expired. Never raises — a
        resolve failure renders an inline error fragment with the
        Open-original link still available, matching the ``/strm``
        fallback contract (never break the caller with a 500).
        """
        from media_archivist import streams

        idx = Index(db_path)
        entry = None
        for e in idx.view():
            if e.id == entry_id:
                entry = e
                break
        if entry is None:
            return _render(request, "fragments/entry_resolve.html",
                           error="entry not found", status_code=404)

        target = entry.stream or entry.url
        try:
            resolved = streams.resolve_stream(target)
        except streams.StreamResolveError as exc:
            LOG.warning("resolve failed for entry %s (%s): %s", entry_id, target, exc)
            return _render(request, "fragments/entry_resolve.html",
                           entry=entry, resolve_error=str(exc))
        return _render(request, "fragments/entry_resolve.html",
                       entry=entry, resolved=resolved,
                       resolved_kind=_kind_for_ext(resolved.ext))

    @app.get("/ui/archive", response_class=HTMLResponse)
    def archive_page(request: Request):
        return _render(request, "archive.html", active="archive")

    @app.post("/ui/archive", response_class=HTMLResponse)
    def archive_submit(
        request: Request,
        url: str = Form(...),
        backend: Optional[str] = Form(None),
        require: str = Form(""),
        blacklist: str = Form(""),
        min_duration: int = Form(-1),
    ):
        req = ArchiveRequest(
            url=url,
            backend=backend or None,
            require=[s.strip() for s in require.split(",") if s.strip()],
            blacklist=[s.strip() for s in blacklist.split(",") if s.strip()],
            min_duration=min_duration,
        )
        try:
            task = scheduler.submit(req)
        except asyncio.QueueFull:
            return _render(request, "fragments/task_status.html",
                           error="archive queue full", status_code=429)
        return _render(request, "fragments/task_status.html", task=task)

    @app.get("/ui/tasks/{task_id}", response_class=HTMLResponse)
    def task_status(request: Request, task_id: str):
        task = scheduler.store.get(task_id)
        if task is None:
            return _render(request, "fragments/task_status.html",
                           error="task not found", status_code=404)
        return _render(request, "fragments/task_status.html", task=task)

    @app.get("/ui/quarantine", response_class=HTMLResponse)
    def quarantine_page(request: Request):
        return _render(request, "quarantine.html", active="quarantine")

    def _quarantine_entries():
        sidecar = load_quarantine(db_path)
        return [
            QuarantineConflict(
                row_id=qe.row_id,
                candidate_canonical_id=qe.candidate_canonical_id,
                conflicts=[render_conflict(c) for c in (qe.conflicts or [])],
            )
            for qe in sidecar.entries.values()
        ]

    @app.get("/ui/quarantine/list", response_class=HTMLResponse)
    def quarantine_list_fragment(request: Request):
        return _render(request, "fragments/quarantine_list.html",
                       entries=_quarantine_entries())

    def _quarantine_bulk(request: Request, row_ids: List[str], *, action: str):
        # Homelabbers hit hundreds of conflicts after a big canonicalize —
        # bulk accept/reject saves them from clicking each row individually.
        row_ids = [r for r in row_ids if r]
        if not row_ids:
            return _render(request, "fragments/quarantine_list.html",
                           entries=_quarantine_entries(),
                           bulk_note="Nothing selected — check one or more rows first.")
        done = skipped = 0
        for row_id in row_ids:
            if action == "accept":
                ok = quarantine_resolve(db_path, row_id, canonical_id=None)
            else:
                ok = quarantine_reject(db_path, row_id)
            if ok:
                done += 1
            else:
                skipped += 1
        verb = "Accepted" if action == "accept" else "Rejected"
        summary = f"✓ {verb} {done}"
        if skipped:
            summary += f" · {skipped} already resolved or unknown"
        return _render(request, "fragments/quarantine_list.html",
                       entries=_quarantine_entries(), bulk_note=summary)

    # NOTE: these literal "/bulk/..." routes must be registered *before*
    # the "/{row_id}/accept" and "/{row_id}/reject" routes below — FastAPI
    # (Starlette) matches path routes in registration order, and "bulk"
    # would otherwise be captured as a row_id by the single-row routes.
    @app.post("/ui/quarantine/bulk/accept", response_class=HTMLResponse)
    def quarantine_bulk_accept_fragment(
        request: Request, row_ids: Annotated[List[str], Form()] = [],
    ):
        return _quarantine_bulk(request, row_ids, action="accept")

    @app.post("/ui/quarantine/bulk/reject", response_class=HTMLResponse)
    def quarantine_bulk_reject_fragment(
        request: Request, row_ids: Annotated[List[str], Form()] = [],
    ):
        return _quarantine_bulk(request, row_ids, action="reject")

    @app.post("/ui/quarantine/{row_id}/accept", response_class=HTMLResponse)
    def quarantine_accept_fragment(request: Request, row_id: str):
        # Resolve the target canonical id *before* popping the row so the
        # confirmation can tell the user what they just did.
        qe = load_quarantine(db_path).entries.get(row_id)
        target_id = None
        if qe is not None:
            target_id = qe.candidate_canonical_id
            if not target_id and qe.proposed_signals:
                target_id = signal_hash(qe.proposed_signals)
        ok = quarantine_resolve(db_path, row_id, canonical_id=None)
        if not ok:
            # Row already resolved/rejected by someone else — surface this
            # visibly instead of a silent no-op (htmx swaps 200s, not 404s).
            return _render(request, "fragments/quarantine_action.html",
                           row_id=row_id, outcome="error",
                           message="Not accepted — this row is no longer in quarantine "
                                    "(already resolved elsewhere).")
        return _render(request, "fragments/quarantine_action.html",
                       row_id=row_id, outcome="accepted",
                       message=f"Accepted as canon:{target_id or 'new record'}")

    @app.post("/ui/quarantine/{row_id}/reject", response_class=HTMLResponse)
    def quarantine_reject_fragment(request: Request, row_id: str):
        ok = quarantine_reject(db_path, row_id)
        if not ok:
            return _render(request, "fragments/quarantine_action.html",
                           row_id=row_id, outcome="error",
                           message="Not rejected — this row is no longer in quarantine "
                                    "(already resolved elsewhere).")
        return _render(request, "fragments/quarantine_action.html",
                       row_id=row_id, outcome="rejected",
                       message="Row dropped from canonicalization; a new distinct "
                                "canonical id was assigned.")

    @app.get("/ui/providers", response_class=HTMLResponse)
    def providers_page(request: Request):
        return _render(request, "providers.html", active="providers",
                       providers=_providers_payload())

    @app.post("/ui/canonicalize", response_class=HTMLResponse)
    async def canonicalize_fragment(request: Request):
        try:
            canonical, quarantine, entities = await asyncio.to_thread(
                run_canonicalize, db_path,
            )
        except ValueError as e:
            return _render(request, "fragments/canonicalize_result.html",
                           error=str(e), status_code=400)
        result = {
            "canonical_records": len(canonical.records),
            "quarantined": len(quarantine.entries),
            "entities": len(entities.entities),
        }
        return _render(request, "fragments/canonicalize_result.html", result=result)
