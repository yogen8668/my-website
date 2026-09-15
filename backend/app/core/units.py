"""Unit conversion layer (§17).

Display units and calculation units are strictly separated. Canonical units are
those the model itself consumes: kelvin and pascal absolute, mole fractions.

PRESSURE IS THE DANGEROUS ONE. Gauge <-> absolute needs a reference atmospheric
pressure and the model has no opinion about it. `P_atm` is therefore an explicit
per-calculation input, defaulted to 101325 Pa, echoed in the API response and
stored in the audit trail. A calculation whose gauge reference is not recorded
is not reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Standard atmosphere, Pa. Default gauge reference — override per site elevation.
P_ATM_DEFAULT = 101325.0

#: Exact, at standard gravity.
KGF_PER_CM2_TO_PA = 98066.5
PSI_TO_PA = 6894.757293168361
BAR_TO_PA = 100000.0


@dataclass(frozen=True)
class UnitDef:
    key: str
    label: str
    quantity: str
    gauge: bool = False


TEMPERATURE_UNITS = {
    "K": UnitDef("K", "K", "temperature"),
    "C": UnitDef("C", "\u00b0C", "temperature"),
    "F": UnitDef("F", "\u00b0F", "temperature"),
    "R": UnitDef("R", "\u00b0R", "temperature"),
}

PRESSURE_UNITS = {
    "Pa": UnitDef("Pa", "Pa", "pressure"),
    "kPa": UnitDef("kPa", "kPa", "pressure"),
    "MPa": UnitDef("MPa", "MPa", "pressure"),
    "bar_a": UnitDef("bar_a", "bar(a)", "pressure"),
    "bar_g": UnitDef("bar_g", "bar(g)", "pressure", gauge=True),
    "kgcm2_a": UnitDef("kgcm2_a", "kg/cm\u00b2(a)", "pressure"),
    "kgcm2_g": UnitDef("kgcm2_g", "kg/cm\u00b2(g)", "pressure", gauge=True),
    "psia": UnitDef("psia", "psia", "pressure"),
    "psig": UnitDef("psig", "psig", "pressure", gauge=True),
}

COMPOSITION_UNITS = {
    "mole_fraction": UnitDef("mole_fraction", "mole fraction", "composition"),
    "mole_percent": UnitDef("mole_percent", "mol %", "composition"),
    "mass_fraction": UnitDef("mass_fraction", "mass fraction", "composition"),
    "mass_percent": UnitDef("mass_percent", "mass %", "composition"),
}


class UnknownUnitError(ValueError):
    pass


# --- temperature -----------------------------------------------------------

def temperature_to_kelvin(value: float, unit: str) -> float:
    if unit == "K":
        return value
    if unit == "C":
        return value + 273.15
    if unit == "F":
        return (value - 32.0) * 5.0 / 9.0 + 273.15
    if unit == "R":
        return value * 5.0 / 9.0
    raise UnknownUnitError(f"Unknown temperature unit: {unit}")


def kelvin_to_temperature(value_K: float, unit: str) -> float:
    if unit == "K":
        return value_K
    if unit == "C":
        return value_K - 273.15
    if unit == "F":
        return (value_K - 273.15) * 9.0 / 5.0 + 32.0
    if unit == "R":
        return value_K * 9.0 / 5.0
    raise UnknownUnitError(f"Unknown temperature unit: {unit}")


# --- pressure --------------------------------------------------------------

def pressure_to_pascal_absolute(value: float, unit: str, p_atm: float = P_ATM_DEFAULT) -> float:
    """Convert a user pressure to absolute pascals.

    Gauge units add `p_atm`. The caller must store `p_atm` alongside the result.
    """
    if unit == "Pa":
        return value
    if unit == "kPa":
        return value * 1000.0
    if unit == "MPa":
        return value * 1e6
    if unit == "bar_a":
        return value * BAR_TO_PA
    if unit == "bar_g":
        return value * BAR_TO_PA + p_atm
    if unit == "kgcm2_a":
        return value * KGF_PER_CM2_TO_PA
    if unit == "kgcm2_g":
        return value * KGF_PER_CM2_TO_PA + p_atm
    if unit == "psia":
        return value * PSI_TO_PA
    if unit == "psig":
        return value * PSI_TO_PA + p_atm
    raise UnknownUnitError(f"Unknown pressure unit: {unit}")


def pascal_absolute_to_pressure(value_Pa: float, unit: str, p_atm: float = P_ATM_DEFAULT) -> float:
    if unit == "Pa":
        return value_Pa
    if unit == "kPa":
        return value_Pa / 1000.0
    if unit == "MPa":
        return value_Pa / 1e6
    if unit == "bar_a":
        return value_Pa / BAR_TO_PA
    if unit == "bar_g":
        return (value_Pa - p_atm) / BAR_TO_PA
    if unit == "kgcm2_a":
        return value_Pa / KGF_PER_CM2_TO_PA
    if unit == "kgcm2_g":
        return (value_Pa - p_atm) / KGF_PER_CM2_TO_PA
    if unit == "psia":
        return value_Pa / PSI_TO_PA
    if unit == "psig":
        return (value_Pa - p_atm) / PSI_TO_PA
    raise UnknownUnitError(f"Unknown pressure unit: {unit}")


def is_gauge(unit: str) -> bool:
    try:
        return PRESSURE_UNITS[unit].gauge
    except KeyError as exc:
        raise UnknownUnitError(f"Unknown pressure unit: {unit}") from exc


# --- composition -----------------------------------------------------------

def composition_to_fraction(values: list[float], basis: str) -> list[float]:
    """Scale percent bases to fractions. Does NOT convert mass -> mole.

    Mass -> mole requires molecular weights and therefore must happen in the
    model layer where `thermo` constants are available. Never in JavaScript.
    """
    if basis in ("mole_fraction", "mass_fraction"):
        return list(values)
    if basis in ("mole_percent", "mass_percent"):
        return [v / 100.0 for v in values]
    raise UnknownUnitError(f"Unknown composition basis: {basis}")


def is_mass_basis(basis: str) -> bool:
    return basis in ("mass_fraction", "mass_percent")


def mass_to_mole_fractions(mass_fracs: list[float], MWs: list[float]) -> list[float]:
    """Mass -> mole using molecular weights taken from the thermo constants package.

    n_i proportional to w_i / MW_i.
    """
    if len(mass_fracs) != len(MWs):
        raise ValueError("Mass fraction and molecular weight lists must be the same length.")
    moles = [w / mw if mw > 0 else 0.0 for w, mw in zip(mass_fracs, MWs)]
    total = sum(moles)
    if total <= 0:
        raise ValueError("Mass fractions must sum to a positive number.")
    return [m / total for m in moles]
