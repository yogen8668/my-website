"""Component endpoints (§8). Resolution is always server-side; no thermo object
ever crosses the wire."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from app.schemas.calculation import ComponentDetail, ComponentListResponse, ComponentSummary
from app.thermo.component_service import backend_info, get_component_service

router = APIRouter()


@router.get("", response_model=ComponentListResponse, summary="Search components")
async def list_components(
    search: str = Query(default="", description="Chemical name, formula, or CAS number"),
    limit: int = Query(default=25, ge=1, le=200),
) -> ComponentListResponse:
    svc = get_component_service()
    items, total = await run_in_threadpool(svc.search, search, limit)
    return ComponentListResponse(items=[ComponentSummary(**i) for i in items], total=total)


@router.post("/resolve", response_model=ComponentListResponse, summary="Resolve a CAS slate")
async def resolve_components(component_ids: list[str]) -> ComponentListResponse:
    """Bulk resolution for a preset slate.

    Unresolved entries come back with `resolved: false` rather than failing the
    whole request — the UI needs to show *which* of 48 CAS numbers is bad.
    """
    svc = get_component_service()
    items = await run_in_threadpool(svc.resolve_many, component_ids)
    return ComponentListResponse(items=[ComponentSummary(**i) for i in items], total=len(items))


@router.get("/backend", summary="Which search surface the installed library exposes")
async def component_backend() -> dict:
    """Diagnostic — `chemicals` search APIs differ between versions (§9)."""
    return backend_info()


@router.get("/{component_id}", response_model=ComponentDetail, summary="Component detail (§15)")
async def get_component(component_id: str) -> ComponentDetail:
    svc = get_component_service()
    detail = await run_in_threadpool(svc.get_component_detail, component_id)
    return ComponentDetail(**detail)
