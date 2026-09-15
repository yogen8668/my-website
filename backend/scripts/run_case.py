"""CLI over the engine — the original script's __main__ block, extracted.

The engine module itself is IO-free; all printing lives here.

    python -m scripts.run_case tests/golden_cases/case_001_reference.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.thermo.calculation_engine import analyze_stream, describe_case  # noqa: E402


def main(path: str) -> int:
    case = json.loads(Path(path).read_text())
    inp = case["input"]

    result = analyze_stream(
        T=inp["T_K"],
        P=inp["P_Pa"],
        components=inp["components"],
        zs=inp["zs"],
        water_cas=inp.get("water_cas", "7732-18-5"),
        kij_source=inp.get("kij_source", "ChemSep PR"),
    )

    print("Detected case  :", describe_case(result))
    print(f"Vapor fraction : {result['V']:.6f}")
    print(f"Liquid fraction: {result['L']:.6f}")
    print()

    for label, key in (("Vapor  ", "vapor"), ("Aqueous", "aqueous"), ("Organic", "organic")):
        p = result[key]
        if p:
            print(f"{label} : beta={p['beta']:.4f}  MW={p['MW']:.3f}  "
                  f"Cp={p['Cp']:.3f} J/mol/K  rho={p['rho_mass']:.3f} kg/m3")

    print()
    b = result["bulk"]
    print(f"Bulk    : MW={b['MW']:.3f}  Cp={b['Cp']:.3f} J/mol/K "
          f"({b['Cp_mass_kJ_kgK']:.3f} kJ/kg-K)  rho={b['rho_mass']:.3f} kg/m3")
    print()
    print("--- metadata ---")
    print(json.dumps(result["metadata"], indent=2, default=str))
    if result["warnings"]:
        print("--- warnings ---")
        for w in result["warnings"]:
            print(" -", w)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
