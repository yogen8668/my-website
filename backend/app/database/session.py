"""Database session management.

The engine is created lazily so the API — and in particular /health/ready —
still boots and reports honestly when Postgres is unreachable.
"""

from __future__ import annotations

import logging
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        if settings.database_url.startswith("postgresql"):
            kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)
        _engine = create_engine(settings.database_url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def engine_available() -> bool:
    return bool(get_settings().database_url)


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Commits on success, rolls back on any exception."""
    db = get_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
