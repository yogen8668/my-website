"""Engine guard-clause tests (F-4, F-6, §13, §14).

These exercise validate_engine_input only, so they run without a thermo install.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import InvalidCompositionError, InvalidConditionError

pytest.importorskip("numpy")

from app.thermo.calculation_engine import validate_engine_input  # noqa: E402

OK_COMPONENTS = ["7732-18-5", "74-82-8"]
OK_ZS = [0.5, 0.5]


def test_valid_input_passes():
    validate_engine_input(425.0, 169612.9, OK_COMPONENTS, OK_ZS)


def test_length_mismatch_rejected():
    """F-4: the most dangerous missing check — this path could previously reach
    the flash with a wrong-length composition."""
    with pytest.raises(InvalidCompositionError) as exc:
        validate_engine_input(425.0, 1e5, OK_COMPONENTS, [1.0])
    assert exc.value.details["n_components"] == 2
    assert exc.value.details["n_compositions"] == 1


def test_duplicate_component_rejected():
    with pytest.raises(InvalidCompositionError) as exc:
        validate_engine_input(425.0, 1e5, ["74-82-8", "74-82-8"], [0.5, 0.5])
    assert exc.value.details["component_id"] == "74-82-8"


def test_negative_composition_rejected():
    """A negative fraction can pass a naive sum>0 test."""
    with pytest.raises(InvalidCompositionError):
        validate_engine_input(425.0, 1e5, OK_COMPONENTS, [-0.2, 1.2])


def test_zero_sum_rejected():
    with pytest.raises(InvalidCompositionError):
        validate_engine_input(425.0, 1e5, OK_COMPONENTS, [0.0, 0.0])


def test_non_finite_rejected():
    with pytest.raises(InvalidCompositionError):
        validate_engine_input(425.0, 1e5, OK_COMPONENTS, [float("nan"), 1.0])


def test_empty_components_rejected():
    with pytest.raises(Exception):
        validate_engine_input(425.0, 1e5, [], [])


@pytest.mark.parametrize("T", [0.0, -1.0, float("nan")])
def test_bad_temperature_rejected(T):
    with pytest.raises(InvalidConditionError):
        validate_engine_input(T, 1e5, OK_COMPONENTS, OK_ZS)


@pytest.mark.parametrize("P", [0.0, -101325.0, float("inf")])
def test_bad_pressure_rejected(P):
    with pytest.raises(InvalidConditionError):
        validate_engine_input(425.0, P, OK_COMPONENTS, OK_ZS)


def test_unnormalised_sum_is_allowed_by_the_engine():
    """The engine still auto-normalises — that is MODEL BEHAVIOUR (F-5).

    The rejection of out-of-tolerance sums lives in the service layer, so that
    the engine's numerical behaviour is unchanged. See test_composition_policy.
    """
    validate_engine_input(425.0, 1e5, OK_COMPONENTS, [0.7, 0.2])
