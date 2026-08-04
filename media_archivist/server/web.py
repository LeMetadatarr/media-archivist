# SPDX-License-Identifier: Apache-2.0
"""Server-rendered htmx WebUI — mounted onto the same FastAPI app as the
JSON API in :mod:`media_archivist.server.routes`.

All pages extend ``base.html``; htmx swaps are served by dedicated
``/ui/...`` fragment routes returning partial templates (no base layout).
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from media_archivist.canonicalize import (
    canonicalize as run_canonicalize,
    load_quarantine,
    quarantine_reject,
    quarantine_resolve,
    render_conflict,
)
from media_archivist.index import Index, WhereError
from media_archivist.models.api import ArchiveRequest, ProviderInfo, QuarantineConflict
from media_archivist.providers import all_providers
from media_archivist.version import __version__

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse


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

    def _query_entries(source, where, grep, has_stream, explicit, limit):
        idx = Index(db_path)
        return idx.to_list(
            source=source or None, where=where or None, grep=grep or None,
            has_stream=has_stream, explicit=explicit, limit=limit,
        )

    @app.get("/ui/entries/table", response_class=HTMLResponse)
    def entries_table(
        request: Request,
        source: Optional[str] = None,
        where: Optional[str] = None,
        grep: Optional[str] = None,
        has_stream: Optional[bool] = None,
        explicit: Optional[bool] = None,
        limit: int = 100,
    ):
        try:
            entries = _query_entries(source, where, grep, has_stream, explicit, limit)
        except WhereError as e:
            return _render(request, "fragments/entries_table.html",
                           error=f"where: {e}", status_code=400)
        return _render(request, "fragments/entries_table.html", entries=entries)

    @app.get("/ui/entries/{entry_id}", response_class=HTMLResponse)
    def entry_detail(request: Request, entry_id: str):
        idx = Index(db_path)
        for e in idx.view():
            if e.id == entry_id:
                return _render(request, "fragments/entry_detail.html", entry=e)
        return _render(request, "fragments/entry_detail.html",
                       error="entry not found", status_code=404)

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

    @app.post("/ui/quarantine/{row_id}/accept", response_class=HTMLResponse)
    def quarantine_accept_fragment(request: Request, row_id: str):
        ok = quarantine_resolve(db_path, row_id, canonical_id=None)
        if not ok:
            raise HTTPException(status_code=404, detail="row not in quarantine")
        return HTMLResponse("")

    @app.post("/ui/quarantine/{row_id}/reject", response_class=HTMLResponse)
    def quarantine_reject_fragment(request: Request, row_id: str):
        ok = quarantine_reject(db_path, row_id)
        if not ok:
            raise HTTPException(status_code=404, detail="row not in quarantine")
        return HTMLResponse("")

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
