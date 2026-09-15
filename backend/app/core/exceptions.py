"""Engine and API exception hierarchy (F-11, §30, §31).

Engine exceptions carry an `error_code` and a machine-readable `details` dict.
The API layer maps them to the §31 error envelope. Raw tracebacks never reach a
user; they are logged internally with the request id.
"""

from __future__ import annotations

from typing import Any


class ThermoAppError(Exception):
    """Base class. `message` is engineer-facing and safe to display."""

    error_code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidCompositionError(ThermoAppError):
    error_code = "INVALID_COMPOSITION"
    http_status = 422


class CompositionSumError(InvalidCompositionError):
    """F-5 — |sum(z) - 1| exceeded the configured tolerance."""

    error_code = "COMPOSITION_SUM_INVALID"
    http_status = 422


class DuplicateComponentError(InvalidCompositionError):
    """§14 — the same component was added twice."""

    error_code = "DUPLICATE_COMPONENT"
    http_status = 422


class InvalidConditionError(ThermoAppError):
    error_code = "INVALID_CONDITION"
    http_status = 422


class ConditionOutOfRangeError(ThermoAppError):
    error_code = "CONDITION_OUT_OF_RANGE"
    http_status = 422


class ComponentResolutionError(ThermoAppError):
    error_code = "COMPONENT_NOT_RESOLVED"
    http_status = 422


class InteractionParameterError(ThermoAppError):
    """F-2 — kij matrix unavailable and `require_kijs` was set."""

    error_code = "INTERACTION_PARAMETERS_UNAVAILABLE"
    http_status = 503


class FlashConvergenceError(ThermoAppError):
    error_code = "CALCULATION_FAILED"
    http_status = 422


class UnitConversionError(ThermoAppError):
    error_code = "INVALID_UNIT"
    http_status = 422


class NotFoundError(ThermoAppError):
    error_code = "NOT_FOUND"
    http_status = 404


#: Engineer-facing message templates (§30). Kept together so wording stays
#: consistent between the API and the UI's error states.
USER_MESSAGES = {
    "COMPOSITION_SUM_INVALID": "Composition total does not equal 1.0 within tolerance.",
    "DUPLICATE_COMPONENT": "This component has already been added to the mixture.",
    "INVALID_CONDITION": "Operating condition is invalid.",
    "CONDITION_OUT_OF_RANGE": "Operating condition is outside the supported model range.",
    "COMPONENT_NOT_RESOLVED": "Component could not be resolved by the thermodynamic library.",
    "INTERACTION_PARAMETERS_UNAVAILABLE": "Binary interaction parameters are unavailable.",
    "CALCULATION_FAILED": "Thermodynamic calculation failed.",
    "INVALID_UNIT": "Unsupported unit.",
    "NOT_FOUND": "The requested record does not exist.",
    "INTERNAL_ERROR": "An internal error occurred. Reference the request id when reporting this.",
}
