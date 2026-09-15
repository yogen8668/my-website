"""Health and version endpoints (§33, §54).

These reflect REAL system state. Nothing here is hard-coded to OK — the
frontend status indicators are driven by this, so a fake value here would put a
fake green dot in front of an engineer.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.versions import version_report
from app.database.session import engine_available, get_engine
from app.schemas.calculation import HealthResponse, VersionResponse
from app.thermo.calculation_engine import cache_stats

logger = logging.getLogger(__name__)
router = APIRouter()

_warm_up: dict[str, Any] = {"state": "pending", "report": {}}


def set_warm_up_state(state: str, report: dict[str, Any]) -> None:
    _warm_up["state"] = state
    _warm_up["report"] = report


@router.get("/health/live", summary="Liveness — is the process up")
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness — can it serve")
def ready(response: Response) -> HealthResponse:
    details: dict[str, Any] = {}

    # Database
    db_status = "UNAVAILABLE"
    if engine_available():
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            db_status = "OK"
        except Exception as exc:  # noqa: BLE001
            db_status = "ERROR"
            details["database_error"] = str(exc)[:200]
    else:
        db_status = "NOT_CONFIGURED"

    # Thermo library — a real import and a real constant lookup, not a version string
    thermo_status = "ERROR"
    try:
        from thermo import ChemicalConstantsPackage

        constants, _ = ChemicalConstantsPackage.from_IDs(["7732-18-5"])
        thermo_status = "OK" if constants.Tcs and constants.Tcs[0] > 0 else "ERROR"
    except Exception as exc:  # noqa: BLE001
        details["thermo_error"] = str(exc)[:200]

    # Calculation engine — an actual flash on a trivial two-component mixture
    engine_status = "ERROR"
    try:
        from app.thermo.calculation_engine import analyze_stream

        probe = analyze_stream(
            T=300.0, P=101325.0,
            components=["7727-37-9", "74-82-8"], zs=[0.8, 0.2],
        )
        engine_status = "OK" if probe["bulk"]["MW"] > 0 else "ERROR"
        details["engine_probe_kijs_applied"] = probe["metadata"]["kijs_applied"]
    except Exception as exc:  # noqa: BLE001
        details["engine_error"] = str(exc)[:200]

    details["flasher_cache"] = cache_stats()
    details["versions"] = version_report()

    warm = _warm_up["state"]
    details["warm_up_report"] = _warm_up["report"]

    ok = thermo_status == "OK" and engine_status == "OK" and db_status in ("OK", "NOT_CONFIGURED")
    ok = ok and warm in ("ready", "skipped")
    if not ok:
        response.status_code = 503

    return HealthResponse(
        status="ready" if ok else "not_ready",
        database=db_status,
        thermo_library=thermo_status,
        calculation_engine=engine_status,
        warm_up=warm,
        details=details,
    )


@router.get("/version", response_model=VersionResponse, summary="Version manifest (§29)")
def version() -> VersionResponse:
    return VersionResponse(**version_report())
