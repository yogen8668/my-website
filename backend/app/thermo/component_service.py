"""Component resolution against the INSTALLED thermo library (§6, §9, §10).

There is no hand-maintained component list. Every selectable component is
resolved from `chemicals`/`thermo` at runtime, and CAS is the identifier —
which is what the original model already used.

IMPORTANT: the exact search surface of `chemicals.identifiers` varies between
versions. This module probes for what is available and degrades gracefully
rather than assuming an API. Run `python -m app.thermo.component_service` after
install to print what was detected.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

from app.core.exceptions import ComponentResolutionError

logger = logging.getLogger(__name__)

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _search_backend() -> dict[str, Any]:
    """Probe the installed library once and report what search surface exists."""
    backend: dict[str, Any] = {"available": False, "db": None, "CAS_from_any": None, "search": None}
    try:
        from chemicals import identifiers as ident  # type: ignore

        backend["CAS_from_any"] = getattr(ident, "CAS_from_any", None)
        backend["search"] = getattr(ident, "search_chemical", None)
        for db_name in ("pubchem_db", "ChemicalMetadataDB"):
            db = getattr(ident, db_name, None)
            if db is not None:
                backend["db"] = db
                break
        backend["available"] = backend["CAS_from_any"] is not None or backend["search"] is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("chemicals.identifiers unavailable: %s", exc)
    return backend


@lru_cache(maxsize=1)
def backend_info() -> dict[str, Any]:
    b = _search_backend()
    return {
        "available": b["available"],
        "has_CAS_from_any": b["CAS_from_any"] is not None,
        "has_search_chemical": b["search"] is not None,
        "has_metadata_db": b["db"] is not None,
    }


class ComponentService:
    """Search and resolve components. Stateless apart from an LRU cache."""

    def __init__(self) -> None:
        self._backend = _search_backend()

    # -- resolution ---------------------------------------------------------

    @staticmethod
    def is_cas(value: str) -> bool:
        return bool(_CAS_RE.match(value.strip()))

    @lru_cache(maxsize=4096)  # noqa: B019 — service is a singleton
    def _metadata(self, query: str) -> dict[str, Any] | None:
        """Return normalised metadata for one identifier, or None."""
        q = query.strip()
        search = self._backend.get("search")
        if search is not None:
            try:
                hit = search(q)
                return {
                    "cas": getattr(hit, "CASs", None) or getattr(hit, "CAS", None),
                    "name": getattr(hit, "common_name", None) or getattr(hit, "name", None) or q,
                    "formula": getattr(hit, "formula", None),
                    "mw": getattr(hit, "MW", None),
                    "synonyms": list(getattr(hit, "synonyms", []) or [])[:12],
                }
            except Exception:  # noqa: BLE001 — fall through to CAS_from_any
                pass

        cas_from_any = self._backend.get("CAS_from_any")
        if cas_from_any is not None:
            try:
                cas = cas_from_any(q)
                return {"cas": cas, "name": q, "formula": None, "mw": None, "synonyms": []}
            except Exception:  # noqa: BLE001
                return None
        return None

    def get_component(self, component_id: str) -> dict[str, Any]:
        meta = self._metadata(component_id)
        if meta is None or not meta.get("cas"):
            raise ComponentResolutionError(
                f"Component '{component_id}' could not be resolved by the thermo library.",
                details={"query": component_id},
            )
        cas = meta["cas"]
        return {
            "id": cas,
            "cas": cas,
            "name": meta.get("name") or cas,
            "formula": meta.get("formula"),
            "mw": meta.get("mw"),
            "resolved": True,
        }

    def validate_component(self, component_id: str) -> bool:
        try:
            self.get_component(component_id)
            return True
        except ComponentResolutionError:
            return False

    def resolve_many(self, component_ids: list[str]) -> list[dict[str, Any]]:
        """Resolve a slate, reporting unresolved entries rather than raising.

        The UI needs to show which of 48 CAS numbers failed, not just that one did.
        """
        out: list[dict[str, Any]] = []
        for cid in component_ids:
            try:
                out.append(self.get_component(cid))
            except ComponentResolutionError:
                out.append({
                    "id": cid, "cas": cid, "name": cid,
                    "formula": None, "mw": None, "resolved": False,
                })
        return out

    # -- detail (§15 — informational only, no thermodynamics) ---------------

    def get_component_detail(self, component_id: str) -> dict[str, Any]:
        base = self.get_component(component_id)
        meta = self._metadata(component_id) or {}
        detail = dict(base)
        detail["synonyms"] = meta.get("synonyms", [])
        try:
            from thermo import ChemicalConstantsPackage  # local import: expensive

            constants, _ = ChemicalConstantsPackage.from_IDs([base["cas"]])
            detail.update({
                "mw": constants.MWs[0],
                "Tc": constants.Tcs[0],
                "Pc": constants.Pcs[0],
                "omega": constants.omegas[0],
                "Tb": (constants.Tbs[0] if getattr(constants, "Tbs", None) else None),
            })
        except Exception as exc:  # noqa: BLE001
            logger.info("Constant lookup failed for %s: %s", component_id, exc)
        return detail

    # -- search -------------------------------------------------------------

    def search(self, query: str, limit: int = 25) -> tuple[list[dict[str, Any]], int]:
        """Search by name, formula, or CAS (§7).

        Strategy: an exact identifier resolution first (covers CAS, formula and
        exact names, which is what `search_chemical` handles well), then a
        substring scan of the metadata DB when one is exposed. `chemicals` does
        not ship a general fuzzy index, so this is deliberately conservative —
        it would rather return few correct hits than many wrong ones.
        """
        q = (query or "").strip()
        if not q:
            return [], 0

        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        try:
            hit = self.get_component(q)
            results.append(hit)
            seen.add(hit["cas"])
        except ComponentResolutionError:
            pass

        db = self._backend.get("db")
        if db is not None and len(results) < limit:
            needle = q.lower()
            try:
                for cas, obj in self._iter_db(db):
                    if len(results) >= limit:
                        break
                    if cas in seen:
                        continue
                    name = (getattr(obj, "common_name", "") or "").lower()
                    formula = (getattr(obj, "formula", "") or "").lower()
                    if needle in name or needle == formula:
                        results.append({
                            "id": cas,
                            "cas": cas,
                            "name": getattr(obj, "common_name", None) or cas,
                            "formula": getattr(obj, "formula", None),
                            "mw": getattr(obj, "MW", None),
                            "resolved": True,
                        })
                        seen.add(cas)
            except Exception as exc:  # noqa: BLE001
                logger.info("Metadata DB scan unavailable: %s", exc)

        return results, len(results)

    @staticmethod
    def _iter_db(db: Any):
        """Yield (cas, record) pairs from whichever DB shape is installed."""
        for attr in ("CAS_index", "cas_index", "index_CAS"):
            index = getattr(db, attr, None)
            if isinstance(index, dict):
                for key, obj in index.items():
                    cas = getattr(obj, "CASs", None) or getattr(obj, "CAS", None) or str(key)
                    yield str(cas), obj
                return
        return


@lru_cache(maxsize=1)
def get_component_service() -> ComponentService:
    return ComponentService()


if __name__ == "__main__":
    import json

    print("Detected search backend:")
    print(json.dumps(backend_info(), indent=2))
    svc = get_component_service()
    for probe in ("methane", "74-82-8", "H2O", "ethylene", "benzene"):
        try:
            print(f"{probe:>12} -> {svc.get_component(probe)}")
        except ComponentResolutionError as exc:
            print(f"{probe:>12} -> UNRESOLVED ({exc.message})")
