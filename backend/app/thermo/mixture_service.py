"""Mixture resolution (§26).

A saved mixture is a composition DEFINITION. Resolving it means re-reading the
component identifiers through the CURRENTLY installed thermo library, never
replaying a stored thermodynamic result.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import DuplicateComponentError
from app.thermo.component_service import get_component_service


class MixtureService:
    def resolve(self, components: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve a stored mixture definition against the current library."""
        self.assert_no_duplicates([c["component_id"] for c in components])
        svc = get_component_service()
        resolved = {r["cas"]: r for r in svc.resolve_many([c["component_id"] for c in components])}
        out = []
        for c in components:
            info = resolved.get(c["component_id"], {})
            out.append({
                "component_id": c["component_id"],
                "cas": c["component_id"],
                "name": info.get("name") or c["component_id"],
                "formula": info.get("formula"),
                "mw": info.get("mw"),
                "resolved": info.get("resolved", False),
                "composition": c["composition"],
            })
        return out

    @staticmethod
    def assert_no_duplicates(component_ids: list[str]) -> None:
        seen: set[str] = set()
        for cid in component_ids:
            if cid in seen:
                raise DuplicateComponentError(
                    f"Component {cid} has already been added to this mixture. "
                    "Modify the existing value.",
                    details={"component_id": cid},
                )
            seen.add(cid)


def get_mixture_service() -> MixtureService:
    return MixtureService()
