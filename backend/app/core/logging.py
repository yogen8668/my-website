"""Structured logging (§32).

Never logs passwords, tokens, API secrets or credentials. The request id is
bound to a contextvar so every line inside a request carries it without being
threaded through call signatures.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
calculation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("calculation_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")

#: Defence in depth — keys matching these are redacted if a dict ever reaches a log.
_REDACT_KEYS = {"password", "token", "secret", "secret_key", "authorization", "api_key", "credentials"}


def redact(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACT_KEYS else redact(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [redact(v) for v in payload]
    return payload


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.calculation_id = calculation_id_var.get()
        record.user_id = user_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "calculation_id": getattr(record, "calculation_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "message": record.getMessage(),
        }
        for key in ("endpoint", "execution_time_ms", "model_version", "thermo_version", "status"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class ConsoleFormatter(logging.Formatter):
    """Pipe-delimited, matching the §32 example."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        calc = getattr(record, "calculation_id", "-")
        base = f"{ts} | {record.levelname:<5} | {calc} | {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # thermo/numba are extremely chatty at DEBUG.
    for noisy in ("numba", "matplotlib", "thermo", "chemicals"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]
