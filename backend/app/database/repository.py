"""Persistence for calculations and mixtures.

Uses parameterised SQLAlchemy throughout — no string-built SQL, which is the
SQL-injection posture §78 asks for.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import units as U
from app.database.models import (
    AuditLog,
    Calculation,
    CalculationInput,
    CalculationResult,
    CalculationSequence,
    Mixture,
    MixtureComponent,
)

logger = logging.getLogger(__name__)


def next_calculation_sequence(db: Session) -> int:
    """Issue the next sequence number for a CALC- identifier.

    A dedicated table rather than a count(*), so deleting history never causes
    an identifier to be reissued.
    """
    row = CalculationSequence()
    db.add(row)
    db.flush()
    return int(row.id)


def persist_calculation(
    db: Session,
    request: Any,
    result: dict[str, Any],
    parent_calculation_id: str | None = None,
) -> None:
    meta = result["metadata"]
    canonical = result["canonical_inputs"]

    calc = Calculation(
        calculation_id=result["calculation_id"],
        calculation_name=result.get("calculation_name"),
        mixture_id=getattr(request, "mixture_id", None),
        parent_calculation_id=parent_calculation_id,
        user_id=result.get("_user_id"),
        status=result["status"],
        case=result["phase_split"]["case"],
        n_phases=result["phase_split"]["n_phases"],
        vapor_fraction=result["phase_split"]["vapor_fraction"],
        app_version=meta["app_version"],
        model_version=meta["model_version"],
        thermo_version=meta["thermo_version"],
        chemicals_version=meta["chemicals_version"],
        execution_time_ms=result["execution_time_ms"],
        created_at=result["created_at"],
    )
    db.add(calc)

    t_in = result["inputs_as_entered"]["temperature"]
    p_in = result["inputs_as_entered"]["pressure"]

    db.add(CalculationInput(
        calculation_id=calc.calculation_id,
        composition_basis=result["display_units"]["composition"],
        temperature_value=t_in["value"],
        temperature_unit=t_in["unit"],
        pressure_value=p_in["value"],
        pressure_unit=p_in["unit"],
        temperature_K=canonical["temperature_K"],
        pressure_Pa=canonical["pressure_Pa"],
        p_atm_Pa=canonical["p_atm_Pa"],
        sum_zs_original=canonical["sum_zs_original"],
        normalization_applied=canonical["normalization_applied"],
        kij_source=meta["kij_source"],
        kijs_applied=meta["kijs_applied"],
        water_cas=meta["water_cas"],
        components_snapshot=result["components"],
        request_snapshot=_jsonable(request.model_dump()),
    ))

    bulk = result["bulk"]
    db.add(CalculationResult(
        calculation_id=calc.calculation_id,
        bulk_mw=bulk["molecular_weight"]["value"],
        bulk_cp_molar=bulk["cp_molar"]["value"],
        bulk_cp_mass=bulk["cp_mass"]["value"],
        bulk_density_mass=bulk["density_mass"]["value"],
        bulk_density_molar=bulk["density_molar"]["value"],
        payload=_jsonable({k: v for k, v in result.items() if not k.startswith("_")}),
        display_units=result["display_units"],
        warnings=result["warnings"],
    ))

    db.add(AuditLog(
        action="calculation.create",
        entity_type="calculation",
        entity_id=calc.calculation_id,
        user_id=result.get("_user_id"),
        detail={
            "case": calc.case,
            "n_components": len(result["components"]),
            "kijs_applied": meta["kijs_applied"],
            "model_version": meta["model_version"],
            "thermo_version": meta["thermo_version"],
        },
    ))
    db.flush()


def _jsonable(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _jsonable(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_jsonable(v) for v in payload]
    if isinstance(payload, datetime):
        return payload.astimezone(timezone.utc).isoformat()
    return payload


def _display(value: float, unit: str) -> str:
    return f"{value:,.2f} {unit}"


def list_calculations(db: Session, limit: int, offset: int) -> tuple[list[dict], int]:
    total = db.scalar(select(func.count()).select_from(Calculation)) or 0
    rows = db.execute(
        select(Calculation, CalculationInput, Mixture.name)
        .join(CalculationInput, CalculationInput.calculation_id == Calculation.calculation_id)
        .outerjoin(Mixture, Mixture.id == Calculation.mixture_id)
        .order_by(Calculation.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = []
    for calc, inp, mixture_name in rows:
        items.append({
            "calculation_id": calc.calculation_id,
            "created_at": calc.created_at,
            "calculation_name": calc.calculation_name,
            "mixture_name": mixture_name,
            "temperature_display": _display(inp.temperature_value, inp.temperature_unit),
            "pressure_display": _display(
                inp.pressure_value, U.PRESSURE_UNITS[inp.pressure_unit].label
            ),
            "case": calc.case,
            "status": calc.status,
            "model_version": calc.model_version,
        })
    return items, int(total)


def get_calculation(db: Session, calculation_id: str) -> dict | None:
    row = db.execute(
        select(CalculationResult).where(CalculationResult.calculation_id == calculation_id)
    ).scalar_one_or_none()
    return dict(row.payload) if row else None


def get_calculation_request(db: Session, calculation_id: str) -> dict | None:
    row = db.execute(
        select(CalculationInput).where(CalculationInput.calculation_id == calculation_id)
    ).scalar_one_or_none()
    return dict(row.request_snapshot) if row else None


def delete_calculation(db: Session, calculation_id: str) -> bool:
    calc = db.get(Calculation, calculation_id)
    if calc is None:
        return False
    db.delete(calc)
    db.add(AuditLog(
        action="calculation.delete", entity_type="calculation", entity_id=calculation_id, detail={}
    ))
    return True


# --- mixtures --------------------------------------------------------------

def create_mixture(db: Session, payload: Any, resolved: list[dict]) -> dict:
    by_cas = {r["cas"]: r for r in resolved}
    mixture = Mixture(
        name=payload.name,
        description=payload.description,
        composition_basis=payload.composition_basis,
    )
    db.add(mixture)
    db.flush()

    for i, c in enumerate(payload.components):
        info = by_cas.get(c.component_id, {})
        db.add(MixtureComponent(
            mixture_id=mixture.id,
            position=i,
            component_id=c.component_id,
            name=info.get("name") or c.component_id,
            formula=info.get("formula"),
            composition=c.composition,
        ))
    db.flush()
    db.add(AuditLog(
        action="mixture.create", entity_type="mixture", entity_id=mixture.id,
        detail={"name": mixture.name, "n_components": len(payload.components)},
    ))
    return _mixture_dict(db.get(Mixture, mixture.id))


def _mixture_dict(mixture: Mixture) -> dict:
    return {
        "id": mixture.id,
        "name": mixture.name,
        "description": mixture.description,
        "composition_basis": mixture.composition_basis,
        "created_by": mixture.created_by,
        "created_at": mixture.created_at,
        "modified_at": mixture.modified_at,
        "components": [
            {
                "component_id": c.component_id,
                "name": c.name,
                "formula": c.formula,
                "cas": c.component_id,
                "composition": c.composition,
                "composition_basis": mixture.composition_basis,
                "mole_fraction_normalized": c.composition,
            }
            for c in mixture.components
        ],
    }


def list_mixtures(db: Session) -> tuple[list[dict], int]:
    rows = db.execute(select(Mixture).order_by(Mixture.modified_at.desc())).scalars().all()
    return [_mixture_dict(m) for m in rows], len(rows)


def get_mixture(db: Session, mixture_id: str) -> dict | None:
    mixture = db.get(Mixture, mixture_id)
    return _mixture_dict(mixture) if mixture else None


def delete_mixture(db: Session, mixture_id: str) -> bool:
    mixture = db.get(Mixture, mixture_id)
    if mixture is None:
        return False
    db.delete(mixture)
    db.add(AuditLog(
        action="mixture.delete", entity_type="mixture", entity_id=mixture_id, detail={}
    ))
    return True
