"""FastAPI application (§33, §35, §78)."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.api.v1.health import set_warm_up_state
from app.core.config import get_settings
from app.core.exceptions import ThermoAppError
from app.core.logging import configure_logging, new_request_id, request_id_var
from app.core.versions import APP_VERSION

settings = get_settings()
configure_logging(settings.log_level, json_output=settings.app_env != "development")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="THERMOX Thermodynamic Calculation API",
    version=APP_VERSION,
    description=(
        "Peng-Robinson EOS stream property calculations over the `thermo` library.\n\n"
        "The calculation engine is independent of this API and of any UI, so the same "
        "engine can serve an Energy Management System, batch jobs, or automated tests."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or new_request_id()
    request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled exception", extra={"endpoint": request.url.path, "status": "ERROR"}
        )
        raise
    elapsed = (time.perf_counter() - started) * 1000.0
    response.headers["X-Request-ID"] = rid
    logger.info(
        "%s %s -> %s",
        request.method,
        request.url.path,
        response.status_code,
        extra={"endpoint": request.url.path, "execution_time_ms": elapsed},
    )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Defence in depth — Nginx sets these too (§67, §78)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.exception_handler(ThermoAppError)
async def thermo_error_handler(request: Request, exc: ThermoAppError):
    """§30, §31 — engineer-friendly message, no stack trace to the user."""
    logger.warning(
        "%s: %s", exc.error_code, exc.message,
        extra={"endpoint": request.url.path, "status": "ERROR"},
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "status": "ERROR",
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request_id_var.get(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "status": "ERROR",
            "error_code": "INVALID_INPUT",
            "message": "The request could not be validated.",
            "details": {"issues": [
                {"field": ".".join(str(p) for p in e["loc"][1:]), "problem": e["msg"]}
                for e in exc.errors()
            ]},
            "request_id": request_id_var.get(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    """Raw tracebacks are logged, never returned (§30)."""
    logger.exception("Internal error", extra={"endpoint": request.url.path, "status": "ERROR"})
    return JSONResponse(
        status_code=500,
        content={
            "status": "ERROR",
            "error_code": "INTERNAL_ERROR",
            "message": "An internal error occurred. Quote the request id when reporting this.",
            "details": {},
            "request_id": request_id_var.get(),
        },
    )


app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
async def warm_up() -> None:
    """F-3 — pre-build known component slates so no user request pays the
    multi-second flasher build. /health/ready stays false until this finishes."""
    slates = settings.warm_slate_list
    if not slates:
        set_warm_up_state("skipped", {})
        return

    set_warm_up_state("in_progress", {})
    from starlette.concurrency import run_in_threadpool

    from app.thermo.calculation_engine import warm_cache

    report = {}
    for i, slate in enumerate(slates):
        try:
            report[f"slate_{i}"] = await run_in_threadpool(warm_cache, slate)
            logger.info("Warmed slate %d (%d components)", i, len(slate))
        except Exception as exc:  # noqa: BLE001 — warm-up failure must not kill boot
            report[f"slate_{i}"] = {"error": str(exc)}
            logger.warning("Warm-up failed for slate %d: %s", i, exc)
    set_warm_up_state("ready", report)
