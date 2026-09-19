"""Calculation endpoints (§18, §19, §25)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.database import repository as repo
from app.database.session import get_db
from app.schemas.calculation import (
    CalculationListResponse,
    CalculationRequest,
    CalculationResponse,
)
from app.services.calculation_service import calculate_thermodynamic_properties

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "",
    response_model=CalculationResponse,
    summary="Run a thermodynamic calculation",
    responses={422: {"description": "Validation or model-range failure (§31 envelope)"}},
)
async def create_calculation(
    request: CalculationRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalculationResponse:
    """The flash determines the phase case on its own; no case is selected here.

    Runs in a worker thread so the event loop is never blocked by the flash (F-3).
    """
    sequence = repo.next_calculation_sequence(db)
    result = await run_in_threadpool(
        calculate_thermodynamic_properties,
        request.model_dump(),
        settings,
        sequence,
        None,  # user_id — wired when auth lands
    )
    repo.persist_calculation(db, request, result)
    return CalculationResponse(**{k: v for k, v in result.items() if not k.startswith("_")})


@router.get("", response_model=CalculationListResponse, summary="Calculation history")
async def list_calculations(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> CalculationListResponse:
    items, total = repo.list_calculations(db, limit=limit, offset=offset)
    return CalculationListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{calculation_id}", response_model=CalculationResponse, summary="Retrieve one")
async def get_calculation(
    calculation_id: str, db: Session = Depends(get_db)
) -> CalculationResponse:
    record = repo.get_calculation(db, calculation_id)
    if record is None:
        raise NotFoundError(
            f"Calculation {calculation_id} does not exist.",
            details={"calculation_id": calculation_id},
        )
    return CalculationResponse(**record)


@router.delete("/{calculation_id}", status_code=200, summary="Delete one")
async def delete_calculation(calculation_id: str, db: Session = Depends(get_db)) -> None:
    if not repo.delete_calculation(db, calculation_id):
        raise NotFoundError(
            f"Calculation {calculation_id} does not exist.",
            details={"calculation_id": calculation_id},
        )


@router.post(
    "/{calculation_id}/recalculate",
    response_model=CalculationResponse,
    summary="Re-run a stored calculation against the CURRENT library",
)
async def recalculate(
    calculation_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalculationResponse:
    """Replays the stored INPUTS, not the stored results.

    This is the point of §26: a saved definition re-resolved through whatever
    thermo version is installed now. A version drift between the original and
    the replay is a finding, not a bug — compare the two records.
    """
    stored = repo.get_calculation_request(db, calculation_id)
    if stored is None:
        raise NotFoundError(
            f"Calculation {calculation_id} does not exist.",
            details={"calculation_id": calculation_id},
        )
    request = CalculationRequest(**stored)
    sequence = repo.next_calculation_sequence(db)
    result = await run_in_threadpool(
        calculate_thermodynamic_properties, request.model_dump(), settings, sequence, None
    )
    result["warnings"].insert(
        0, f"Recalculated from {calculation_id} using the currently installed library versions."
    )
    repo.persist_calculation(db, request, result, parent_calculation_id=calculation_id)
    return CalculationResponse(**{k: v for k, v in result.items() if not k.startswith("_")})
