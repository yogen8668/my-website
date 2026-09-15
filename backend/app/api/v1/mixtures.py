"""Saved-mixture endpoints (§26).

A saved mixture is a COMPOSITION DEFINITION. It is never a stored
thermodynamic result — properties are always recomputed against the current
library at the current operating conditions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import NotFoundError
from app.database import repository as repo
from app.database.session import get_db
from app.schemas.calculation import MixtureCreate, MixtureListResponse, MixtureResponse
from app.thermo.component_service import get_component_service

router = APIRouter()


@router.post("", response_model=MixtureResponse, status_code=201, summary="Save a mixture")
async def create_mixture(
    mixture: MixtureCreate, db: Session = Depends(get_db)
) -> MixtureResponse:
    svc = get_component_service()
    resolved = await run_in_threadpool(
        svc.resolve_many, [c.component_id for c in mixture.components]
    )
    return MixtureResponse(**repo.create_mixture(db, mixture, resolved))


@router.get("", response_model=MixtureListResponse, summary="List saved mixtures")
async def list_mixtures(db: Session = Depends(get_db)) -> MixtureListResponse:
    items, total = repo.list_mixtures(db)
    return MixtureListResponse(items=items, total=total)


@router.get("/{mixture_id}", response_model=MixtureResponse, summary="Retrieve one")
async def get_mixture(mixture_id: str, db: Session = Depends(get_db)) -> MixtureResponse:
    record = repo.get_mixture(db, mixture_id)
    if record is None:
        raise NotFoundError(
            f"Mixture {mixture_id} does not exist.", details={"mixture_id": mixture_id}
        )
    return MixtureResponse(**record)


@router.delete("/{mixture_id}", status_code=204, summary="Delete one")
async def delete_mixture(mixture_id: str, db: Session = Depends(get_db)) -> None:
    if not repo.delete_mixture(db, mixture_id):
        raise NotFoundError(
            f"Mixture {mixture_id} does not exist.", details={"mixture_id": mixture_id}
        )
