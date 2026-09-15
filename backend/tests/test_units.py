"""Unit conversion tests (§61). No thermo install required."""

from __future__ import annotations

import pytest

from app.core import units as U


class TestTemperature:
    def test_celsius_roundtrip(self):
        assert U.temperature_to_kelvin(0, "C") == pytest.approx(273.15)
        assert U.temperature_to_kelvin(151.85, "C") == pytest.approx(425.0)
        assert U.kelvin_to_temperature(425.0, "C") == pytest.approx(151.85)

    def test_fahrenheit(self):
        assert U.temperature_to_kelvin(32, "F") == pytest.approx(273.15)
        assert U.temperature_to_kelvin(212, "F") == pytest.approx(373.15)

    def test_rankine(self):
        assert U.temperature_to_kelvin(491.67, "R") == pytest.approx(273.15, abs=1e-6)

    def test_unknown_unit_rejected(self):
        with pytest.raises(U.UnknownUnitError):
            U.temperature_to_kelvin(1, "degC")


class TestPressure:
    def test_kgcm2_gauge_uses_explicit_atm(self):
        """The gauge reference must be applied, and applied explicitly."""
        p = U.pressure_to_pascal_absolute(20.0, "kgcm2_g", p_atm=101325.0)
        assert p == pytest.approx(20.0 * 98066.5 + 101325.0)

    def test_gauge_reference_actually_matters(self):
        """Regression guard: a changed p_atm must change the answer.

        If this ever passes with both references equal, the gauge conversion has
        been silently dropped somewhere.
        """
        a = U.pressure_to_pascal_absolute(10.0, "bar_g", p_atm=101325.0)
        b = U.pressure_to_pascal_absolute(10.0, "bar_g", p_atm=95000.0)
        assert a != b
        assert a - b == pytest.approx(6325.0)

    def test_absolute_units_ignore_atm(self):
        a = U.pressure_to_pascal_absolute(10.0, "bar_a", p_atm=101325.0)
        b = U.pressure_to_pascal_absolute(10.0, "bar_a", p_atm=95000.0)
        assert a == b == pytest.approx(1e6)

    def test_reference_case_pressure(self):
        """169612.911830353 Pa is the reference case, ~0.696 bar(g)."""
        back = U.pascal_absolute_to_pressure(169612.911830353, "bar_g", 101325.0)
        assert back == pytest.approx(0.68287911830353, abs=1e-9)

    def test_roundtrip_all_units(self):
        for unit in U.PRESSURE_UNITS:
            pa = U.pressure_to_pascal_absolute(5.0, unit, 101325.0)
            back = U.pascal_absolute_to_pressure(pa, unit, 101325.0)
            assert back == pytest.approx(5.0), unit

    def test_gauge_flag(self):
        assert U.is_gauge("bar_g")
        assert U.is_gauge("kgcm2_g")
        assert not U.is_gauge("bar_a")
        assert not U.is_gauge("Pa")


class TestComposition:
    def test_percent_to_fraction(self):
        assert U.composition_to_fraction([72.5851, 27.4149], "mole_percent") == pytest.approx(
            [0.725851, 0.274149]
        )

    def test_fraction_passthrough(self):
        assert U.composition_to_fraction([0.5, 0.5], "mole_fraction") == [0.5, 0.5]

    def test_mass_to_mole(self):
        """50/50 mass water(18.015)/methane(16.043) -> mole fractions."""
        moles = U.mass_to_mole_fractions([0.5, 0.5], [18.01528, 16.04246])
        assert sum(moles) == pytest.approx(1.0)
        assert moles[1] > moles[0]  # lighter component has more moles

    def test_mass_basis_detection(self):
        assert U.is_mass_basis("mass_percent")
        assert not U.is_mass_basis("mole_fraction")

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError):
            U.mass_to_mole_fractions([0.5, 0.5], [18.0])
