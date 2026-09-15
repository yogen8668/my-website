"""Stage 2 — the wrapper. Owns units, validation and traceability; delegates all
thermodynamics to the Stage 1 engine (§20).

Stateless. No global calculation state, no shared mutable inputs, no file or UI
dependency. Callable by FastAPI, tests, a batch job, or the future EMS (§75, §79).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.core import units as U
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ComponentResolutionError,
    CompositionSumError,
    ConditionOutOfRangeError,
)
from app.core.logging import calculation_id_var
from app.core.versions import APP_VERSION, version_report
from app.thermo.calculation_engine import analyze_stream, describe_case
from app.thermo.component_service import get_component_service

logger = logging.getLogger(__name__)

#: Display units for the properties the model actually returns. MW in g/mol is
#: numerically identical to kg/kmol, and molar Cp in J/(mol*K) to kJ/(kmol*K) —
#: so these are relabellings, not conversions. Documented in docs/CALCULATION_MODEL.md.
RESULT_UNITS = {
    "molecular_weight": "kg/kmol",
    "cp_molar": "J/(mol*K)",
    "cp_mass": "kJ/(kg*K)",
    "density_mass": "kg/m3",
    "density_molar": "kmol/m3",
    "beta": "mole fraction",
}


def _next_calculation_id(sequence: int) -> str:
    return f"CALC-{datetime.now(timezone.utc):%Y%m%d}-{sequence:06d}"


def _quantity(value: float | None, unit: str) -> dict[str, Any] | None:
    return None if value is None else {"value": value, "unit": unit}


def _phase_payload(phase: dict[str, Any] | None) -> dict[str, Any] | None:
    if phase is None:
        return None
    return {
        "beta": _quantity(phase["beta"], RESULT_UNITS["beta"]),
        "molecular_weight": _quantity(phase["MW"], RESULT_UNITS["molecular_weight"]),
        "cp_molar": _quantity(phase["Cp"], RESULT_UNITS["cp_molar"]),
        "density_mass": _quantity(phase["rho_mass"], RESULT_UNITS["density_mass"]),
        "density_molar": _quantity(phase["rho_molar_kmol_m3"], RESULT_UNITS["density_molar"]),
    }


def calculate_thermodynamic_properties(
    payload: dict[str, Any],
    settings: Settings | None = None,
    sequence: int = 1,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Validate, convert units, call the engine, and assemble the audit record.

    `payload` is a validated CalculationRequest dumped to a dict.
    """
    settings = settings or get_settings()
    started = time.perf_counter()

    calc_id = _next_calculation_id(sequence)
    calculation_id_var.set(calc_id)
    logger.info("Calculation Started", extra={"endpoint": "calculations.create"})

    component_ids = [c["component_id"] for c in payload["components"]]
    raw_values = [float(c["composition"]) for c in payload["components"]]
    basis = payload["composition_basis"]

    svc = get_component_service()
    resolved = svc.resolve_many(component_ids)
    unresolved = [r["cas"] for r in resolved if not r["resolved"]]
    if unresolved:
        raise ComponentResolutionError(
            "One or more components could not be resolved by the thermodynamic library.",
            details={"unresolved_components": unresolved},
        )

    return _calculate(
        payload=payload,
        settings=settings,
        calc_id=calc_id,
        started=started,
        component_ids=component_ids,
        raw_values=raw_values,
        basis=basis,
        resolved=resolved,
        user_id=user_id,
    )


def _calculate(
    *,
    payload: dict[str, Any],
    settings: Settings,
    calc_id: str,
    started: float,
    component_ids: list[str],
    raw_values: list[float],
    basis: str,
    resolved: list[dict[str, Any]],
    user_id: str | None,
) -> dict[str, Any]:
    warnings: list[str] = []

    # --- units: composition -------------------------------------------------
    fractions = U.composition_to_fraction(raw_values, basis)

    if U.is_mass_basis(basis):
        # Mass -> mole needs MWs from thermo. Never done in the frontend (§12).
        from thermo import ChemicalConstantsPackage

        constants, _ = ChemicalConstantsPackage.from_IDs(list(component_ids))
        fractions = U.mass_to_mole_fractions(fractions, list(constants.MWs))
        warnings.append(
            "Composition was supplied on a mass basis and converted to mole "
            "fractions using thermo molecular weights before the flash."
        )

    sum_zs = sum(fractions)
    tol = settings.composition_sum_tolerance

    # --- F-5: the approved policy — reject, do not silently normalise -------
    normalization_applied = False
    if abs(sum_zs - 1.0) > tol:
        if not payload.get("normalize", False):
            raise CompositionSumError(
                "Composition total does not equal 1.0 within tolerance.",
                details={
                    "composition_total": sum_zs,
                    "required_total": 1.0,
                    "tolerance": tol,
                    "deviation": sum_zs - 1.0,
                    "basis": basis,
                },
            )
        # Explicit opt-in only; the original and normalised totals are both recorded.
        normalization_applied = True
        warnings.append(
            f"Composition was explicitly normalised at the caller's request: "
            f"original total {sum_zs:.10f} -> 1.0000000000."
        )

    # --- units: conditions --------------------------------------------------
    t_in = payload["temperature"]
    p_in = payload["pressure"]
    p_atm = payload.get("p_atm") or settings.p_atm_default

    T_K = U.temperature_to_kelvin(float(t_in["value"]), t_in["unit"])
    P_Pa = U.pressure_to_pascal_absolute(float(p_in["value"]), p_in["unit"], p_atm)

    # p_atm is only *applied* for gauge units, but it is recorded either way so
    # the audit trail shows what reference was in force.
    p_atm_used = p_atm
    if U.is_gauge(p_in["unit"]):
        warnings.append(
            f"Gauge pressure converted to absolute using P_atm = {p_atm:.1f} Pa. "
            "Confirm this reference matches site elevation."
        )

    if T_K < settings.t_min_k or T_K > settings.t_max_k:
        raise ConditionOutOfRangeError(
            "Operating condition is outside the supported model range.",
            details={
                "temperature_K": T_K,
                "supported_min_K": settings.t_min_k,
                "supported_max_K": settings.t_max_k,
            },
        )
    if P_Pa > settings.p_max_pa:
        warnings.append(
            f"Pressure {P_Pa / 1e5:.2f} bar(a) is above the practical Peng-Robinson "
            "advisory band; treat results with additional scrutiny."
        )

    # --- F-7: optional pruning, off by default ------------------------------
    engine_components = list(component_ids)
    engine_zs = list(fractions)
    if payload.get("prune_zero_components"):
        kept = [(c, z) for c, z in zip(engine_components, engine_zs) if z > 0.0]
        if len(kept) != len(engine_components):
            warnings.append(
                f"Zero-composition components were pruned before the flash "
                f"({len(engine_components)} declared -> {len(kept)} present). "
                "This is an opt-in deviation from the reference model; confirm "
                "equivalence with a golden case before relying on it."
            )
            engine_components = [c for c, _ in kept]
            engine_zs = [z for _, z in kept]

    # --- the model ----------------------------------------------------------
    result = analyze_stream(
        T=T_K,
        P=P_Pa,
        components=engine_components,
        zs=engine_zs,
        water_cas=payload.get("water_cas", "7732-18-5"),
        kij_source=payload.get("kij_source", "ChemSep PR"),
        require_kijs=settings.require_interaction_parameters,
    )
    warnings.extend(result.get("warnings", []))

    case = describe_case(result)
    meta = result["metadata"]
    versions = version_report()
    execution_time_ms = (time.perf_counter() - started) * 1000.0

    # --- §28 component snapshot --------------------------------------------
    normalized = [z / sum(fractions) for z in fractions] if sum(fractions) else fractions
    by_cas = {r["cas"]: r for r in resolved}
    snapshots = [
        {
            "component_id": cid,
            "name": by_cas.get(cid, {}).get("name") or cid,
            "formula": by_cas.get(cid, {}).get("formula"),
            "cas": cid,
            "composition": raw,
            "composition_basis": basis,
            "mole_fraction_normalized": norm,
        }
        for cid, raw, norm in zip(component_ids, raw_values, normalized)
    ]

    logger.info(
        "Calculation Completed | %.1f ms | case=%s",
        execution_time_ms,
        case,
        extra={
            "endpoint": "calculations.create",
            "execution_time_ms": execution_time_ms,
            "model_version": meta["model_version"],
            "thermo_version": meta["thermo_version"],
            "status": "SUCCESS",
        },
    )

    return {
        "calculation_id": calc_id,
        "status": "SUCCESS_WITH_WARNINGS" if warnings else "SUCCESS",
        "calculation_name": payload.get("calculation_name"),
        "created_at": datetime.now(timezone.utc),
        "phase_split": {
            "case": case,
            "n_phases": result["n_phases"],
            "vapor_fraction": result["V"],
            "liquid_fraction": result["L"],
        },
        "vapor": _phase_payload(result["vapor"]),
        "aqueous": _phase_payload(result["aqueous"]),
        "organic": _phase_payload(result["organic"]),
        "bulk": {
            "molecular_weight": _quantity(result["bulk"]["MW"], RESULT_UNITS["molecular_weight"]),
            "cp_molar": _quantity(result["bulk"]["Cp"], RESULT_UNITS["cp_molar"]),
            "cp_mass": _quantity(result["bulk"]["Cp_mass_kJ_kgK"], RESULT_UNITS["cp_mass"]),
            "density_mass": _quantity(result["bulk"]["rho_mass"], RESULT_UNITS["density_mass"]),
            "density_molar": _quantity(
                result["bulk"]["rho_molar_kmol_m3"], RESULT_UNITS["density_molar"]
            ),
        },
        "components": snapshots,
        "inputs_as_entered": {
            "temperature": t_in,
            "pressure": p_in,
            "composition_basis": basis,
            "components": payload["components"],
            "p_atm": p_atm_used,
        },
        "canonical_inputs": {
            "temperature_K": T_K,
            "pressure_Pa": P_Pa,
            "p_atm_Pa": p_atm_used,
            "mole_fractions": normalized,
            "sum_zs_original": meta["sum_zs_original"],
            "normalization_applied": normalization_applied,
        },
        "display_units": {
            "temperature": t_in["unit"],
            "pressure": p_in["unit"],
            "composition": basis,
            **RESULT_UNITS,
        },
        "metadata": {
            "app_version": APP_VERSION,
            "model_version": meta["model_version"],
            "thermo_version": versions["thermo_version"],
            "chemicals_version": versions["chemicals_version"],
            "eos": meta["eos"],
            "flash_algorithm": meta["flash_algorithm"],
            "kij_source": meta["kij_source"],
            "kijs_applied": meta["kijs_applied"],
            "kij_error": meta["kij_error"],
            "water_cas": meta["water_cas"],
            "aqueous_water_threshold": meta["aqueous_water_threshold"],
            "n_components_declared": meta["n_components_declared"],
            "n_components_present": meta["n_components_present"],
        },
        "execution_time_ms": execution_time_ms,
        "warnings": warnings,
        "errors": [],
        "_user_id": user_id,
    }
