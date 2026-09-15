"""Golden regression tests (§62) — MANDATORY gate on the calculation model.

These are the only tests that can answer "does the web application still agree
with the original script". Everything else tests plumbing.

They require a real `thermo` install and are skipped without one, so CI must run
them in the backend image, not in a bare Python job. A skipped golden suite is
NOT a passing golden suite — assert on the collected count in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

thermo = pytest.importorskip("thermo", reason="golden cases require a real thermo install")

from app.thermo.calculation_engine import analyze_stream, describe_case  # noqa: E402

CASES = sorted((Path(__file__).resolve().parents[2] / "tests" / "golden_cases").glob("case_*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_at_least_one_case_exists():
    """Guard against a silently empty suite."""
    assert CASES, "No golden cases found — the regression gate is not armed."


@pytest.mark.parametrize("case_path", CASES, ids=lambda p: p.stem)
def test_golden_case(case_path: Path):
    case = _load(case_path)
    inp = case["input"]
    expected = case["expected"]
    tol = case["tolerance"]

    result = analyze_stream(
        T=inp["T_K"],
        P=inp["P_Pa"],
        components=inp["components"],
        zs=inp["zs"],
        water_cas=inp.get("water_cas", "7732-18-5"),
        kij_source=inp.get("kij_source", "ChemSep PR"),
    )

    # F-2 surfaced as a first-class assertion: if interaction parameters were not
    # applied, the run is not comparable to a kij-bearing reference and the
    # failure message must say so rather than blaming the numbers.
    kijs_applied = result["metadata"]["kijs_applied"]
    frozen_kijs = (case.get("frozen") or {}).get("kijs_applied")
    if frozen_kijs is not None and frozen_kijs != kijs_applied:
        pytest.fail(
            f"Interaction-parameter state changed since this case was frozen: "
            f"frozen kijs_applied={frozen_kijs}, now {kijs_applied}. "
            f"kij_error={result['metadata']['kij_error']}. "
            "Numerical comparison is meaningless until this matches — see F-2."
        )

    assert describe_case(result) == expected["case"], (
        f"Phase case changed: expected {expected['case']!r}, got "
        f"{describe_case(result)!r}. This is a MODEL BEHAVIOUR change, not a "
        "tolerance issue. Investigate before touching the tolerances."
    )
    assert result["n_phases"] == expected["n_phases"]

    _close("V", result["V"], expected["V"], tol["V"])
    _close("L", result["L"], expected["L"], tol["L"])

    for phase in ("vapor", "aqueous", "organic"):
        exp_phase = expected.get(phase)
        got_phase = result.get(phase)
        if exp_phase is None:
            assert got_phase is None, f"Phase {phase!r} appeared but was not expected."
            continue
        assert got_phase is not None, f"Phase {phase!r} was expected but is absent."
        for key, exp_val in exp_phase.items():
            if exp_val is None or tol.get(key) is None:
                continue  # not captured at freeze time
            _close(f"{phase}.{key}", got_phase[key], exp_val, tol[key])

    for key, exp_val in expected["bulk"].items():
        if exp_val is None or tol.get(key) is None:
            continue
        _close(f"bulk.{key}", result["bulk"][key], exp_val, tol[key])


def _close(label: str, got: float, expected: float, atol: float) -> None:
    assert got == pytest.approx(expected, abs=atol), (
        f"{label}: expected {expected!r} +/- {atol!r}, got {got!r} "
        f"(delta {got - expected:+.6g})"
    )


@pytest.mark.parametrize("case_path", CASES, ids=lambda p: p.stem)
def test_case_declares_provenance(case_path: Path):
    """A golden case with unknown provenance is not evidence of anything."""
    case = _load(case_path)
    assert case.get("provenance") in ("USER_REPORTED_STDOUT", "frozen-from-engine"), (
        "Every golden case must declare where its expected values came from."
    )


@pytest.mark.parametrize("case_path", CASES, ids=lambda p: p.stem)
def test_determinism(case_path: Path):
    """The same input twice must give identical floats.

    Catches leaked state between runs — the F-3 concern — and any accidental
    dependence on cache warmth.
    """
    case = _load(case_path)
    inp = case["input"]
    kwargs = dict(
        T=inp["T_K"], P=inp["P_Pa"],
        components=inp["components"], zs=inp["zs"],
        water_cas=inp.get("water_cas", "7732-18-5"),
        kij_source=inp.get("kij_source", "ChemSep PR"),
    )
    a = analyze_stream(**kwargs)
    b = analyze_stream(**kwargs)
    assert a["bulk"]["MW"] == b["bulk"]["MW"]
    assert a["bulk"]["rho_mass"] == b["bulk"]["rho_mass"]
    assert a["V"] == b["V"]


def test_cache_key_includes_kij_source():
    """F-1 regression guard.

    Previously the cache was keyed on the component tuple alone, so this second
    call would have returned the FIRST flasher and silently used ChemSep PR
    parameters. Different kij sources must yield different cache entries.
    """
    from app.thermo.calculation_engine import _get_flasher

    components = ["7732-18-5", "74-82-8"]
    a = _get_flasher(components, "ChemSep PR")
    b = _get_flasher(components, "__nonexistent_source__")
    assert a is not b, "Cache key ignores kij_source — F-1 has regressed."
    assert a["kij_source"] == "ChemSep PR"
    assert b["kij_source"] == "__nonexistent_source__"
    # The bogus source must be reported as unavailable, not silently ignored.
    assert b["kijs_applied"] is False
    assert b["kij_error"] is not None


def test_engine_rejects_wrong_length_composition():
    """F-4 regression guard, run against the real engine path."""
    from app.core.exceptions import InvalidCompositionError

    with pytest.raises(InvalidCompositionError):
        analyze_stream(T=425.0, P=101325.0, components=["7732-18-5", "74-82-8"], zs=[1.0])
