"""
Stage 1 engine extraction — Peng-Robinson EOS stream property calculator.

PROVENANCE
----------
This module is the original `analyze_stream` model, moved verbatim from the
user's script. The thermodynamic body of `analyze_stream` below is UNCHANGED:
same PRMIX EOS, same FlashVLN solver with two liquid phases, same "ChemSep PR"
kij source, same auto-normalisation, same aqueous/organic labelling rules and
the same 0.5 water-mole-fraction threshold.

The FlashVLN choice is a MODEL INVARIANT, not an implementation detail. From
the original module docstring: FlashVL was rejected because it converged on a
spurious vapour-liquid split (~15% "vapour" at 17 kg/m3) for a stream
independently confirmed by HYSYS *and* by FlashVLN to be 100% liquid at
~770-860 kg/m3. Do not "optimise" this back to FlashVL.

WHAT CHANGED IN STAGE 1, AND WHY (see docs/PHASE1_ANALYSIS.md)
--------------------------------------------------------------
Every change is either purely additive or provably behaviour-preserving for
valid input. None of them alters a returned number.

  F-1  Cache key now includes kij_source. Previously the cache was keyed on the
       component tuple alone, so a second call with a different kij_source
       silently received the FIRST caller's flasher. This makes the cache obey
       the arguments it was given. water_cas affects only labelling, never the
       flasher, so it is derived per call instead of being cached.

  F-2  The kij lookup failure is no longer swallowed. The fallback behaviour is
       UNCHANGED (kijs are omitted, exactly as before) but it is now visible:
       `kijs_applied` and `kij_error` are reported in metadata, and the caller
       raises or warns according to `require_kijs`. A silent all-zero kij matrix
       misrepresents water/hydrocarbon mutual solubility, which is precisely the
       prediction FlashVLN was chosen to get right.

  F-3  Per-key build lock (one build per component set) and per-flasher flash
       lock (flash() re-entrancy across threads is unverified — assume unsafe).
       LRU bound on the cache. No numerical effect.

  F-4  Guard clause validating lengths, negativity, duplicates, T and P. Runs
       BEFORE the original body, so valid input reaches identical code.

  F-8  A warning is emitted when a phase label is assigned near the 0.5 water
       threshold or with water absent. The labels themselves are unchanged.

  Additive metadata block for traceability (§24, §29). No thermodynamic outputs
  were added: enthalpy, entropy, Cv, gamma, Z and specific volume remain
  UNIMPLEMENTED pending explicit approval (Stage 3), even though they are
  available on the same flash result.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Iterable, Sequence

import numpy as np
from thermo import (
    CEOSGas,
    CEOSLiquid,
    ChemicalConstantsPackage,
    FlashVLN,
    PRMIX,
)
from thermo.interaction_parameters import IPDB

from app.core.exceptions import (
    ComponentResolutionError,
    FlashConvergenceError,
    InteractionParameterError,
    InvalidCompositionError,
    InvalidConditionError,
)
from app.core.versions import CHEMICALS_VERSION, MODEL_VERSION, THERMO_VERSION

logger = logging.getLogger(__name__)

DEFAULT_WATER_CAS = "7732-18-5"
DEFAULT_KIJ_SOURCE = "ChemSep PR"

#: F-8 — the aqueous/organic labelling threshold, promoted from a bare literal
#: to a named constant so it is greppable and auditable. Value unchanged.
AQUEOUS_WATER_THRESHOLD = 0.5

#: Warn when a single-liquid label is assigned this close to the threshold.
AQUEOUS_THRESHOLD_WARN_BAND = 0.05

#: F-3 — bound the cache. Each entry holds a full constants package and is large.
MAX_CACHED_FLASHERS = 16

_FLASHER_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_BUILD_LOCKS: dict[tuple, threading.Lock] = {}


# ---------------------------------------------------------------------------
# F-4 — validation guard. Additive: valid input reaches the original body.
# ---------------------------------------------------------------------------

def validate_engine_input(
    T: float,
    P: float,
    components: Sequence[str],
    zs: Sequence[float],
) -> None:
    """Raise a typed engine exception for structurally invalid input.

    Deliberately duplicated by the Pydantic layer at the API boundary (§13:
    "Backend validation must independently repeat the validation"). This guard
    keeps the engine safe when called by tests, a batch job, or the future EMS
    — anything that bypasses FastAPI.
    """
    if not components:
        raise ComponentResolutionError("At least one component is required.")

    if len(components) != len(zs):
        raise InvalidCompositionError(
            "Component count does not match composition count.",
            details={"n_components": len(components), "n_compositions": len(zs)},
        )

    seen: dict[str, int] = {}
    for i, cas in enumerate(components):
        if cas in seen:
            raise InvalidCompositionError(
                f"Component {cas} appears more than once in this mixture. "
                "Modify the existing value instead of adding it again.",
                details={"component_id": cas, "first_index": seen[cas], "duplicate_index": i},
            )
        seen[cas] = i

    arr = np.asarray(zs, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise InvalidCompositionError("Composition contains non-finite values.")
    negative = [components[i] for i in np.where(arr < 0.0)[0]]
    if negative:
        raise InvalidCompositionError(
            "Composition values must not be negative.",
            details={"negative_components": negative},
        )
    if arr.sum() <= 0:
        # Original message preserved verbatim.
        raise InvalidCompositionError("Mole fractions must sum to a positive number.")

    if not np.isfinite(T) or T <= 0:
        raise InvalidConditionError(
            "Temperature must be greater than absolute zero.",
            details={"temperature_K": T},
        )
    if not np.isfinite(P) or P <= 0:
        raise InvalidConditionError(
            "Pressure must be greater than absolute zero.",
            details={"pressure_Pa": P},
        )


# ---------------------------------------------------------------------------
# Flasher construction and cache
# ---------------------------------------------------------------------------

def _build_flasher(components: list[str], kij_source: str) -> dict:
    """Build the FlashVLN. Body preserved from the original `_get_flasher`."""
    t0 = time.perf_counter()
    try:
        constants, correlations = ChemicalConstantsPackage.from_IDs(list(components))
    except Exception as exc:  # noqa: BLE001 — mapped to a typed engine error
        raise ComponentResolutionError(
            "One or more components could not be resolved by the thermo library.",
            details={"components": list(components), "library_error": str(exc)},
        ) from exc

    eos_kwargs = dict(Tcs=constants.Tcs, Pcs=constants.Pcs, omegas=constants.omegas)

    # F-2 — same fallback as the original (kijs omitted on failure), but the
    # failure is now recorded instead of being discarded by `except: pass`.
    kijs_applied = False
    kij_error: str | None = None
    try:
        eos_kwargs["kijs"] = IPDB.get_ip_asymmetric_matrix(kij_source, constants.CASs, "kij")
        kijs_applied = True
    except Exception as exc:  # noqa: BLE001
        kij_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Binary interaction parameter lookup failed; proceeding with kij=0. "
            "source=%s error=%s",
            kij_source,
            kij_error,
        )

    gas_phase = CEOSGas(PRMIX, eos_kwargs, HeatCapacityGases=correlations.HeatCapacityGases)
    liquid_phase = CEOSLiquid(PRMIX, eos_kwargs, HeatCapacityGases=correlations.HeatCapacityGases)
    # Two liquid phase objects => the solver may find up to two liquids, so
    # n_phases is in {1, 2, 3}. MODEL INVARIANT — see module docstring.
    flasher = FlashVLN(constants, correlations, liquids=[liquid_phase, liquid_phase], gas=gas_phase)

    build_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "Built flasher n_components=%d kij_source=%s kijs_applied=%s build_ms=%.1f",
        len(components),
        kij_source,
        kijs_applied,
        build_ms,
    )

    return {
        "flasher": flasher,
        "constants": constants,
        "components": list(components),
        "kij_source": kij_source,
        "kijs_applied": kijs_applied,
        "kij_error": kij_error,
        "build_ms": build_ms,
        "flash_lock": threading.Lock(),  # F-3
    }


def _get_flasher(components: Iterable[str], kij_source: str = DEFAULT_KIJ_SOURCE) -> dict:
    """Memoised flasher lookup.

    F-1: the key is (components, kij_source) — NOT the component tuple alone.
    Component order is significant (it maps positionally onto zs) so the tuple
    is deliberately not sorted, despite what the original comment claimed.
    """
    components = list(components)
    key = (tuple(components), kij_source)

    with _CACHE_LOCK:
        entry = _FLASHER_CACHE.get(key)
        if entry is not None:
            _FLASHER_CACHE.move_to_end(key)
            return entry
        build_lock = _BUILD_LOCKS.setdefault(key, threading.Lock())

    # F-3 — only one thread builds a given component set; the rest wait here
    # rather than each paying the multi-second ChemicalConstantsPackage cost.
    with build_lock:
        with _CACHE_LOCK:
            entry = _FLASHER_CACHE.get(key)
            if entry is not None:
                _FLASHER_CACHE.move_to_end(key)
                return entry

        entry = _build_flasher(components, kij_source)

        with _CACHE_LOCK:
            _FLASHER_CACHE[key] = entry
            _FLASHER_CACHE.move_to_end(key)
            while len(_FLASHER_CACHE) > MAX_CACHED_FLASHERS:
                evicted, _ = _FLASHER_CACHE.popitem(last=False)
                _BUILD_LOCKS.pop(evicted, None)
                logger.info("Evicted cached flasher n_components=%d", len(evicted[0]))
        return entry


def warm_cache(components: Sequence[str], kij_source: str = DEFAULT_KIJ_SOURCE) -> dict[str, Any]:
    """Pre-build a flasher at startup (F-3, cold-start latency cliff).

    /health/ready reports not-ready until this has completed, which turns a
    user-facing hang into a deployment concern.
    """
    entry = _get_flasher(components, kij_source)
    return {
        "n_components": len(entry["components"]),
        "kijs_applied": entry["kijs_applied"],
        "kij_error": entry["kij_error"],
        "build_ms": entry["build_ms"],
    }


def cache_stats() -> dict[str, Any]:
    with _CACHE_LOCK:
        return {
            "entries": len(_FLASHER_CACHE),
            "max_entries": MAX_CACHED_FLASHERS,
            "component_counts": [len(k[0]) for k in _FLASHER_CACHE],
        }


# ---------------------------------------------------------------------------
# The model. Thermodynamic body unchanged.
# ---------------------------------------------------------------------------

def analyze_stream(
    T: float,
    P: float,
    components: Sequence[str],
    zs: Sequence[float],
    water_cas: str = DEFAULT_WATER_CAS,
    kij_source: str = DEFAULT_KIJ_SOURCE,
    require_kijs: bool = False,
) -> dict[str, Any]:
    """Single entry point — unchanged contract, plus `metadata` and `warnings`.

    The flash determines the number and type of stable phases on its own; this
    function only LABELS whatever comes back. It never decides the case.

    Returns the original dict (V, L, n_phases, vapor, aqueous, organic, bulk)
    with two additive keys: `metadata` (traceability) and `warnings`.
    """
    validate_engine_input(T, P, components, zs)  # F-4

    entry = _get_flasher(components, kij_source)
    flasher = entry["flasher"]
    components = entry["components"]

    # F-2 — the caller decides whether a missing kij matrix is fatal. Default
    # False preserves the original behaviour exactly.
    if require_kijs and not entry["kijs_applied"]:
        raise InteractionParameterError(
            "Binary interaction parameters could not be loaded; refusing to "
            "calculate with kij=0 because water/hydrocarbon phase behaviour "
            "depends on them.",
            details={"kij_source": kij_source, "library_error": entry["kij_error"]},
        )

    warnings: list[str] = []
    if not entry["kijs_applied"]:
        warnings.append(
            f"Binary interaction parameters from '{kij_source}' were not applied "
            "(kij=0 assumed). Water/hydrocarbon phase splits are sensitive to this."
        )

    # Original: water_cas is used only if actually present in the slate.
    water_cas_resolved = water_cas if water_cas in components else None

    zs_arr = np.asarray(zs, dtype=float)
    sum_zs_original = float(zs_arr.sum())
    if zs_arr.sum() <= 0:
        raise InvalidCompositionError("Mole fractions must sum to a positive number.")
    # MODEL BEHAVIOUR — preserved. The API layer enforces |sum-1| <= 1e-6 before
    # calling in, so by the time control reaches here this is a float cleanup
    # rather than a hidden decision. See F-5.
    zs_arr = zs_arr / zs_arr.sum()

    t0 = time.perf_counter()
    try:
        # F-3 — flash() re-entrancy on a shared flasher is unverified.
        with entry["flash_lock"]:
            res = flasher.flash(T=T, P=P, zs=list(zs_arr))
    except Exception as exc:  # noqa: BLE001
        raise FlashConvergenceError(
            "The thermodynamic calculation failed to converge at the requested "
            "operating conditions.",
            details={"temperature_K": T, "pressure_Pa": P, "library_error": str(exc)},
        ) from exc
    flash_ms = (time.perf_counter() - t0) * 1000.0

    vapor_phase = res.gas
    liquid_phases = [p for p in res.phases if p is not vapor_phase]

    if water_cas_resolved is not None:
        water_idx = components.index(water_cas_resolved)
        liquid_phases = sorted(liquid_phases, key=lambda p: p.zs[water_idx], reverse=True)
    else:
        water_idx = None

    aqueous_phase = organic_phase = None
    if len(liquid_phases) == 1:
        only = liquid_phases[0]
        if water_idx is not None and only.zs[water_idx] > AQUEOUS_WATER_THRESHOLD:
            aqueous_phase = only
        else:
            organic_phase = only
        # F-8 — labels unchanged; the basis for them is now reported.
        if water_idx is None:
            warnings.append(
                "Water is not present in this mixture, so the single liquid phase "
                "is labelled 'organic' by default rather than by composition."
            )
        else:
            xw = float(only.zs[water_idx])
            if abs(xw - AQUEOUS_WATER_THRESHOLD) < AQUEOUS_THRESHOLD_WARN_BAND:
                warnings.append(
                    f"Single liquid phase is {xw * 100:.2f} mol% water, close to the "
                    f"{AQUEOUS_WATER_THRESHOLD:.2f} labelling threshold. The "
                    "aqueous/organic label is a naming convention here, not a "
                    "thermodynamic distinction."
                )
    elif len(liquid_phases) >= 2:
        aqueous_phase, organic_phase = liquid_phases[0], liquid_phases[1]
        if water_idx is not None:
            xw_aq = float(liquid_phases[0].zs[water_idx])
            xw_org = float(liquid_phases[1].zs[water_idx])
            if xw_aq < AQUEOUS_WATER_THRESHOLD:
                warnings.append(
                    f"Both liquid phases are water-lean (aqueous {xw_aq * 100:.2f} mol%, "
                    f"organic {xw_org * 100:.2f} mol%). Labels are assigned relatively, "
                    "by which phase holds more water."
                )

    def beta_of(phase):
        return 0.0 if phase is None else res.betas[res.phases.index(phase)]

    def phase_dict(phase):
        if phase is None:
            return None
        return {
            "beta": beta_of(phase),
            "MW": phase.MW(),
            "Cp": phase.Cp(),
            "rho_mass": phase.rho_mass(),
            "rho_molar_kmol_m3": phase.rho() / 1000.0,
        }

    return {
        "V": res.VF,
        "L": 1.0 - res.VF,
        "n_phases": len(res.phases),
        "vapor": phase_dict(vapor_phase),
        "aqueous": phase_dict(aqueous_phase),
        "organic": phase_dict(organic_phase),
        "bulk": {
            "MW": res.MW(),
            "Cp": res.Cp(),
            "Cp_mass_kJ_kgK": res.Cp_mass() / 1000.0,
            "rho_mass": res.rho_mass(),
            "rho_molar_kmol_m3": res.rho() / 1000.0,
        },
        "warnings": warnings,
        "metadata": {
            "model_version": MODEL_VERSION,
            "thermo_version": THERMO_VERSION,
            "chemicals_version": CHEMICALS_VERSION,
            "eos": "PRMIX (Peng-Robinson)",
            "flash_algorithm": "FlashVLN (gas + 2 liquids)",
            "kij_source": kij_source,
            "kijs_applied": entry["kijs_applied"],
            "kij_error": entry["kij_error"],
            "water_cas": water_cas_resolved,
            "aqueous_water_threshold": AQUEOUS_WATER_THRESHOLD,
            "n_components_declared": len(components),
            "n_components_present": int(np.count_nonzero(zs_arr)),
            "sum_zs_original": sum_zs_original,
            "flash_ms": flash_ms,
        },
    }


def describe_case(out: dict[str, Any]) -> str:
    """Human-readable label for whichever case the flash auto-detected.

    Preserved verbatim. Note F-9: a single dense supercritical phase is
    returned as res.gas and therefore reported here as "vapor-only". The
    presentation layer qualifies this; the model string is unchanged for
    compatibility.
    """
    has_v = out["vapor"] is not None
    has_aq = out["aqueous"] is not None
    has_org = out["organic"] is not None
    n_liq = int(has_aq) + int(has_org)
    if has_v and n_liq == 0:
        return "vapor-only"
    if not has_v and n_liq == 1:
        return "liquid-only (single liquid phase)"
    if has_v and n_liq == 1:
        return "two-phase vapor-liquid (VL)"
    if not has_v and n_liq == 2:
        return "two-phase liquid-liquid (LL, aqueous+organic)"
    if has_v and n_liq == 2:
        return "three-phase vapor-liquid-liquid (VLLE)"
    return "no phases found (check inputs)"
