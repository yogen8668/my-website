"""SQLAlchemy models (§27, §28).

Relational structure for querying, plus JSONB snapshots for traceability. The
`thermo` chemical database is NOT copied here — only application data (§27).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: JSONB on Postgres, JSON on SQLite (local dev only).
JsonType = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Present so the schema is auth-ready (§34). No auth flow in V1."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: admin | engineer | viewer
    role: Mapped[str] = mapped_column(String(20), default="engineer")
    #: Argon2/bcrypt digest. NEVER a plaintext password, never logged.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Mixture(Base):
    __tablename__ = "mixtures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    composition_basis: Mapped[str] = mapped_column(String(20), default="mole_fraction")
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    components: Mapped[list["MixtureComponent"]] = relationship(
        back_populates="mixture", cascade="all, delete-orphan", order_by="MixtureComponent.position"
    )


class MixtureComponent(Base):
    __tablename__ = "mixture_components"
    __table_args__ = (
        # §14 — duplicate prevention enforced at the storage layer too.
        UniqueConstraint("mixture_id", "component_id", name="uq_mixture_component"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mixture_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mixtures.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: CAS Registry Number — the stable identifier (§10).
    component_id: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    formula: Mapped[str | None] = mapped_column(String(60), nullable=True)
    composition: Mapped[float] = mapped_column(Float)

    mixture: Mapped[Mixture] = relationship(back_populates="components")


class Calculation(Base):
    __tablename__ = "calculations"

    calculation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    calculation_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mixture_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("mixtures.id", ondelete="SET NULL"), nullable=True
    )
    #: Set when this record came from /recalculate.
    parent_calculation_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(30), index=True)
    case: Mapped[str] = mapped_column(String(60))
    n_phases: Mapped[int] = mapped_column(Integer)
    vapor_fraction: Mapped[float] = mapped_column(Float)

    #: §29 — all three, plus chemicals, because data files change results (F-10).
    app_version: Mapped[str] = mapped_column(String(20))
    model_version: Mapped[str] = mapped_column(String(20), index=True)
    thermo_version: Mapped[str] = mapped_column(String(20))
    chemicals_version: Mapped[str] = mapped_column(String(20))

    execution_time_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    inputs: Mapped["CalculationInput"] = relationship(
        back_populates="calculation", cascade="all, delete-orphan", uselist=False
    )
    results: Mapped["CalculationResult"] = relationship(
        back_populates="calculation", cascade="all, delete-orphan", uselist=False
    )


class CalculationInput(Base):
    """Everything needed to reproduce the run (§24)."""

    __tablename__ = "calculation_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    calculation_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("calculations.calculation_id", ondelete="CASCADE"), index=True
    )

    composition_basis: Mapped[str] = mapped_column(String(20))
    temperature_value: Mapped[float] = mapped_column(Float)
    temperature_unit: Mapped[str] = mapped_column(String(10))
    pressure_value: Mapped[float] = mapped_column(Float)
    pressure_unit: Mapped[str] = mapped_column(String(12))

    #: Canonical — what the model actually received.
    temperature_K: Mapped[float] = mapped_column(Float)
    pressure_Pa: Mapped[float] = mapped_column(Float)
    #: The gauge reference actually in force. Without this the run is not reproducible.
    p_atm_Pa: Mapped[float] = mapped_column(Float)

    sum_zs_original: Mapped[float] = mapped_column(Float)
    normalization_applied: Mapped[bool] = mapped_column(Boolean, default=False)

    kij_source: Mapped[str] = mapped_column(String(60))
    #: F-2 — whether the EOS actually had interaction parameters.
    kijs_applied: Mapped[bool] = mapped_column(Boolean)
    water_cas: Mapped[str | None] = mapped_column(String(20), nullable=True)

    #: §28 — component snapshot: id, name, formula, cas, composition, basis.
    components_snapshot: Mapped[list] = mapped_column(JsonType)
    #: Full request as received, for replay.
    request_snapshot: Mapped[dict] = mapped_column(JsonType)

    calculation: Mapped[Calculation] = relationship(back_populates="inputs")


class CalculationResult(Base):
    __tablename__ = "calculation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    calculation_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("calculations.calculation_id", ondelete="CASCADE"), index=True
    )

    #: Queryable bulk properties — the ones engineers filter and trend on.
    bulk_mw: Mapped[float] = mapped_column(Float)
    bulk_cp_molar: Mapped[float] = mapped_column(Float)
    bulk_cp_mass: Mapped[float] = mapped_column(Float)
    bulk_density_mass: Mapped[float] = mapped_column(Float)
    bulk_density_molar: Mapped[float] = mapped_column(Float)

    #: Full response payload, full precision, including per-phase results.
    payload: Mapped[dict] = mapped_column(JsonType)
    display_units: Mapped[dict] = mapped_column(JsonType)
    warnings: Mapped[list] = mapped_column(JsonType, default=list)

    calculation: Mapped[Calculation] = relationship(back_populates="results")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Never contains credentials — see app.core.logging.redact.
    detail: Mapped[dict] = mapped_column(JsonType, default=dict)


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class CalculationSequence(Base):
    """Backs the human-readable CALC-YYYYMMDD-NNNNNN identifier."""

    __tablename__ = "calculation_sequence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
