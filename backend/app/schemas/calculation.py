"""Pydantic schemas — the API contract (§18, §19, §31)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CompositionBasis = Literal["mole_fraction", "mole_percent", "mass_fraction", "mass_percent"]
TemperatureUnit = Literal["K", "C", "F", "R"]
PressureUnit = Literal["Pa", "kPa", "MPa", "bar_a", "bar_g", "kgcm2_a", "kgcm2_g", "psia", "psig"]


# --- shared ----------------------------------------------------------------

class Quantity(BaseModel):
    value: float
    unit: str


class ErrorResponse(BaseModel):
    """§31 error envelope."""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "status": "ERROR",
            "error_code": "COMPOSITION_SUM_INVALID",
            "message": "Composition total does not equal 1.0 within tolerance.",
            "details": {"composition_total": 0.9824, "required_total": 1.0, "tolerance": 1e-6},
            "request_id": "9f2c1ab74e0d5c81",
        }
    })

    status: Literal["ERROR"] = "ERROR"
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


# --- components ------------------------------------------------------------

class ComponentSummary(BaseModel):
    """Only what the UI needs. thermo objects never cross the wire (§7)."""

    id: str = Field(description="CAS Registry Number — the stable identifier (§10)")
    name: str
    formula: str | None = None
    cas: str
    mw: float | None = Field(default=None, description="Molecular weight, kg/kmol")
    resolved: bool = Field(default=True, description="False when thermo could not resolve the CAS")


class ComponentDetail(ComponentSummary):
    """§15 — informational only. No thermodynamic properties are computed here."""

    Tc: float | None = Field(default=None, description="Critical temperature, K")
    Pc: float | None = Field(default=None, description="Critical pressure, Pa")
    omega: float | None = Field(default=None, description="Acentric factor")
    Tb: float | None = Field(default=None, description="Normal boiling point, K")
    synonyms: list[str] = Field(default_factory=list)


class ComponentListResponse(BaseModel):
    items: list[ComponentSummary]
    total: int


# --- calculation request ---------------------------------------------------

class ComponentInput(BaseModel):
    component_id: str = Field(description="CAS Registry Number")
    composition: float = Field(ge=0.0, description="In the units given by composition_basis")

    @field_validator("component_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class CalculationRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "calculation_name": "C3 Splitter Overhead",
            "composition_basis": "mole_fraction",
            "components": [
                {"component_id": "7732-18-5", "composition": 0.269748141292868},
                {"component_id": "1333-74-0", "composition": 0.258141212597959},
                {"component_id": "74-85-1", "composition": 0.24503640666357},
                {"component_id": "74-84-0", "composition": 0.171763402033696},
                {"component_id": "74-82-8", "composition": 0.0400793391652614},
            ],
            "temperature": {"value": 151.85, "unit": "C"},
            "pressure": {"value": 0.696, "unit": "bar_g"},
            "p_atm": 101325.0,
        }
    })

    calculation_name: str | None = Field(default=None, max_length=200)
    mixture_id: str | None = Field(default=None, description="Optional saved-mixture provenance")
    composition_basis: CompositionBasis = "mole_fraction"
    components: list[ComponentInput] = Field(min_length=1)

    temperature: Quantity
    pressure: Quantity

    p_atm: float | None = Field(
        default=None,
        gt=0,
        description="Gauge reference pressure in Pa. Required semantics for any gauge "
                    "pressure unit; defaults to 101325 and is always echoed back and stored.",
    )

    water_cas: str = Field(default="7732-18-5", description="CAS used to identify the aqueous phase")
    kij_source: str = Field(default="ChemSep PR")

    #: Off by default — F-7 pruning may change results and needs a both-ways golden case.
    prune_zero_components: bool = False

    normalize: bool = Field(
        default=False,
        description="Explicit opt-in. When False, a composition outside tolerance is "
                    "REJECTED rather than silently normalised (F-5).",
    )

    @field_validator("temperature")
    @classmethod
    def _t_unit(cls, v: Quantity) -> Quantity:
        if v.unit not in ("K", "C", "F", "R"):
            raise ValueError(f"Unsupported temperature unit: {v.unit}")
        return v

    @field_validator("pressure")
    @classmethod
    def _p_unit(cls, v: Quantity) -> Quantity:
        allowed = {"Pa", "kPa", "MPa", "bar_a", "bar_g", "kgcm2_a", "kgcm2_g", "psia", "psig"}
        if v.unit not in allowed:
            raise ValueError(f"Unsupported pressure unit: {v.unit}")
        return v

    @model_validator(mode="after")
    def _no_duplicates(self) -> "CalculationRequest":
        """§14 — enforced here AND in the engine guard clause."""
        seen: set[str] = set()
        for c in self.components:
            if c.component_id in seen:
                raise ValueError(
                    f"Component {c.component_id} appears more than once. "
                    "Modify the existing value instead of adding it again."
                )
            seen.add(c.component_id)
        return self


# --- calculation response --------------------------------------------------

class PhaseResult(BaseModel):
    """Exactly the fields the model returns. Nothing added (Stage 3 unapproved)."""

    beta: Quantity = Field(description="Phase mole fraction of total stream")
    molecular_weight: Quantity
    cp_molar: Quantity
    density_mass: Quantity
    density_molar: Quantity


class BulkResult(BaseModel):
    molecular_weight: Quantity
    cp_molar: Quantity
    cp_mass: Quantity
    density_mass: Quantity
    density_molar: Quantity


class PhaseSplit(BaseModel):
    case: str = Field(description="describe_case() output — the auto-detected phase case")
    n_phases: int
    vapor_fraction: float
    liquid_fraction: float


class ComponentSnapshot(BaseModel):
    """§28 — frozen so historical records stay readable if the library changes."""

    component_id: str
    name: str
    formula: str | None = None
    cas: str
    composition: float
    composition_basis: str
    mole_fraction_normalized: float


class CanonicalInputs(BaseModel):
    """§24 — what the model actually received, after unit conversion."""

    temperature_K: float
    pressure_Pa: float
    p_atm_Pa: float
    mole_fractions: list[float]
    sum_zs_original: float
    normalization_applied: bool


class CalculationMetadata(BaseModel):
    app_version: str
    model_version: str
    thermo_version: str
    chemicals_version: str
    eos: str
    flash_algorithm: str
    kij_source: str
    kijs_applied: bool
    kij_error: str | None = None
    water_cas: str | None = None
    aqueous_water_threshold: float
    n_components_declared: int
    n_components_present: int


class CalculationResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "calculation_id": "CALC-20260906-000001",
            "status": "SUCCESS",
            "execution_time_ms": 42,
            "phase_split": {
                "case": "vapor-only", "n_phases": 1,
                "vapor_fraction": 1.0, "liquid_fraction": 0.0,
            },
            "bulk": {
                "molecular_weight": {"value": 18.846, "unit": "kg/kmol"},
                "cp_molar": {"value": 45.539, "unit": "J/(mol*K)"},
                "cp_mass": {"value": 2.416, "unit": "kJ/(kg*K)"},
                "density_mass": {"value": 0.908, "unit": "kg/m3"},
                "density_molar": {"value": 0.0482, "unit": "kmol/m3"},
            },
            "warnings": [],
        }
    })

    calculation_id: str
    status: Literal["SUCCESS", "SUCCESS_WITH_WARNINGS"] = "SUCCESS"
    calculation_name: str | None = None
    created_at: datetime

    phase_split: PhaseSplit
    vapor: PhaseResult | None = None
    aqueous: PhaseResult | None = None
    organic: PhaseResult | None = None
    bulk: BulkResult

    components: list[ComponentSnapshot]
    inputs_as_entered: dict[str, Any]
    canonical_inputs: CanonicalInputs
    display_units: dict[str, str]

    metadata: CalculationMetadata
    execution_time_ms: float
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CalculationListItem(BaseModel):
    calculation_id: str
    created_at: datetime
    calculation_name: str | None = None
    mixture_name: str | None = None
    temperature_display: str
    pressure_display: str
    case: str
    status: str
    model_version: str


class CalculationListResponse(BaseModel):
    items: list[CalculationListItem]
    total: int
    limit: int
    offset: int


# --- mixtures --------------------------------------------------------------

class MixtureComponentInput(BaseModel):
    component_id: str
    composition: float = Field(ge=0.0)


class MixtureCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    composition_basis: CompositionBasis = "mole_fraction"
    components: list[MixtureComponentInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "MixtureCreate":
        seen: set[str] = set()
        for c in self.components:
            if c.component_id in seen:
                raise ValueError(f"Component {c.component_id} appears more than once.")
            seen.add(c.component_id)
        return self


class MixtureResponse(BaseModel):
    """A saved mixture is a COMPOSITION DEFINITION, never a stored result (§26)."""

    id: str
    name: str
    description: str | None = None
    composition_basis: str
    components: list[ComponentSnapshot]
    created_by: str | None = None
    created_at: datetime
    modified_at: datetime


class MixtureListResponse(BaseModel):
    items: list[MixtureResponse]
    total: int


# --- health ---------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    database: str
    thermo_library: str
    calculation_engine: str
    warm_up: str
    details: dict[str, Any] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    app_version: str
    model_version: str
    thermo_version: str
    chemicals_version: str
    fluids_version: str
    numpy_version: str
    scipy_version: str
    python_version: str
