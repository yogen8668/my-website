"""F-5 policy tests: reject out-of-tolerance sums at the SERVICE layer while the
engine keeps normalising, so no model number changes.

Requires thermo only for the happy path, which is marked accordingly.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.exceptions import CompositionSumError


def _payload(values, basis="mole_fraction", normalize=False):
    return {
        "calculation_name": "policy test",
        "composition_basis": basis,
        "components": [
            {"component_id": cid, "composition": v}
            for cid, v in zip(["7732-18-5", "74-82-8"], values)
        ],
        "temperature": {"value": 425.0, "unit": "K"},
        "pressure": {"value": 169612.911830353, "unit": "Pa"},
        "p_atm": 101325.0,
        "normalize": normalize,
        "water_cas": "7732-18-5",
        "kij_source": "ChemSep PR",
        "prune_zero_components": False,
    }


def test_out_of_tolerance_sum_is_rejected(monkeypatch):
    from app.services import calculation_service as svc

    monkeypatch.setattr(
        svc, "get_component_service",
        lambda: type("S", (), {"resolve_many": staticmethod(
            lambda ids: [{"cas": i, "name": i, "formula": None, "resolved": True} for i in ids]
        )})(),
    )

    settings = Settings(composition_sum_tolerance=1e-6)
    with pytest.raises(CompositionSumError) as exc:
        svc.calculate_thermodynamic_properties(_payload([0.7, 0.2]), settings=settings)

    d = exc.value.details
    assert d["composition_total"] == pytest.approx(0.9)
    assert d["required_total"] == 1.0
    assert d["tolerance"] == 1e-6
    assert d["deviation"] == pytest.approx(-0.1)


def test_error_envelope_shape():
    """§31 — the error must carry both totals so the UI can show them."""
    err = CompositionSumError(
        "Composition total does not equal 1.0 within tolerance.",
        details={"composition_total": 0.9824, "required_total": 1.0, "tolerance": 1e-6},
    )
    assert err.error_code == "COMPOSITION_SUM_INVALID"
    assert err.http_status == 422
    assert "composition_total" in err.details
