"""Version resolution (§29, F-10).

Reproducibility depends on packaged DATA files, not only on code. A patch
release of `chemicals` that corrects one omega or adds a kij pair changes
results with no code change here. So `chemicals` is recorded with the same
weight as `thermo`, and the golden regression suite gates every bump.
"""

from __future__ import annotations

import platform


def _resolve(module_name: str) -> str:
    try:
        module = __import__(module_name)
        return str(getattr(module, "__version__", "unknown"))
    except Exception:  # noqa: BLE001 — health endpoints must not crash on this
        return "not-installed"


#: Bumped by hand when the calculation service changes behaviour.
MODEL_VERSION = "1.0.0"
APP_VERSION = "1.0.0"

THERMO_VERSION = _resolve("thermo")
CHEMICALS_VERSION = _resolve("chemicals")
FLUIDS_VERSION = _resolve("fluids")
NUMPY_VERSION = _resolve("numpy")
SCIPY_VERSION = _resolve("scipy")
PYTHON_VERSION = platform.python_version()


def version_report() -> dict[str, str]:
    return {
        "app_version": APP_VERSION,
        "model_version": MODEL_VERSION,
        "thermo_version": THERMO_VERSION,
        "chemicals_version": CHEMICALS_VERSION,
        "fluids_version": FLUIDS_VERSION,
        "numpy_version": NUMPY_VERSION,
        "scipy_version": SCIPY_VERSION,
        "python_version": PYTHON_VERSION,
    }
