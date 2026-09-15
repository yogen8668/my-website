# ---------------------------------------------------------------------------
# Backend image. thermo pulls numpy/scipy and compiles nothing at runtime, but
# the first ChemicalConstantsPackage build is slow -- so the image warms known
# component slates at startup (WARM_SLATES) rather than making a user wait.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libpq for psycopg; build tools only in the builder layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc gfortran \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /install
COPY requirements.txt .
RUN pip install --prefix=/install/deps -r requirements.txt

FROM base AS runtime
COPY --from=builder /install/deps /usr/local

# Non-root (§78).
RUN useradd --create-home --uid 10001 thermox
WORKDIR /app
COPY --chown=thermox:thermox . /app
USER thermox

EXPOSE 8000

# Readiness is a real check (database + thermo + an actual flash), so it is the
# right probe target -- see app/api/v1/health.py.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

# Uvicorn workers under Gunicorn. Keep workers modest: each holds its own
# flasher cache, and each cached 48-component package is large.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "180", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
