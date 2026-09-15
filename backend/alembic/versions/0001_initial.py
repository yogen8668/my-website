"""Initial schema (§27).

Revision ID: 0001_initial
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

#: JSONB on Postgres, JSON elsewhere (SQLite local dev).
JSONB = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(150), nullable=False, unique=True),
        sa.Column("email", sa.String(255)),
        sa.Column("full_name", sa.String(200)),
        sa.Column("role", sa.String(20), nullable=False, server_default="engineer"),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "mixtures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("composition_basis", sa.String(20), nullable=False, server_default="mole_fraction"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("modified_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mixtures_name", "mixtures", ["name"])

    op.create_table(
        "mixture_components",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("mixture_id", sa.String(36), sa.ForeignKey("mixtures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("component_id", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("formula", sa.String(60)),
        sa.Column("composition", sa.Float, nullable=False),
        # §14 — duplicate prevention at the storage layer as well as the API.
        sa.UniqueConstraint("mixture_id", "component_id", name="uq_mixture_component"),
    )
    op.create_index("ix_mixture_components_mixture_id", "mixture_components", ["mixture_id"])
    op.create_index("ix_mixture_components_component_id", "mixture_components", ["component_id"])

    op.create_table(
        "calculations",
        sa.Column("calculation_id", sa.String(40), primary_key=True),
        sa.Column("calculation_name", sa.String(200)),
        sa.Column("mixture_id", sa.String(36), sa.ForeignKey("mixtures.id", ondelete="SET NULL")),
        sa.Column("parent_calculation_id", sa.String(40)),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("case", sa.String(60), nullable=False),
        sa.Column("n_phases", sa.Integer, nullable=False),
        sa.Column("vapor_fraction", sa.Float, nullable=False),
        # §29 — chemicals is recorded alongside thermo: its DATA files change results.
        sa.Column("app_version", sa.String(20), nullable=False),
        sa.Column("model_version", sa.String(20), nullable=False),
        sa.Column("thermo_version", sa.String(20), nullable=False),
        sa.Column("chemicals_version", sa.String(20), nullable=False),
        sa.Column("execution_time_ms", sa.Float, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_calculations_created_at", "calculations", ["created_at"])
    op.create_index("ix_calculations_status", "calculations", ["status"])
    op.create_index("ix_calculations_model_version", "calculations", ["model_version"])
    op.create_index("ix_calculations_parent", "calculations", ["parent_calculation_id"])

    op.create_table(
        "calculation_inputs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("calculation_id", sa.String(40),
                  sa.ForeignKey("calculations.calculation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("composition_basis", sa.String(20), nullable=False),
        sa.Column("temperature_value", sa.Float, nullable=False),
        sa.Column("temperature_unit", sa.String(10), nullable=False),
        sa.Column("pressure_value", sa.Float, nullable=False),
        sa.Column("pressure_unit", sa.String(12), nullable=False),
        sa.Column("temperature_K", sa.Float, nullable=False),
        sa.Column("pressure_Pa", sa.Float, nullable=False),
        # Without the gauge reference, a gauge-input calculation is not reproducible.
        sa.Column("p_atm_Pa", sa.Float, nullable=False),
        sa.Column("sum_zs_original", sa.Float, nullable=False),
        sa.Column("normalization_applied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("kij_source", sa.String(60), nullable=False),
        # F-2 — whether the EOS actually had interaction parameters.
        sa.Column("kijs_applied", sa.Boolean, nullable=False),
        sa.Column("water_cas", sa.String(20)),
        sa.Column("components_snapshot", JSONB, nullable=False),
        sa.Column("request_snapshot", JSONB, nullable=False),
    )
    op.create_index("ix_calculation_inputs_calc", "calculation_inputs", ["calculation_id"])

    op.create_table(
        "calculation_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("calculation_id", sa.String(40),
                  sa.ForeignKey("calculations.calculation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("bulk_mw", sa.Float, nullable=False),
        sa.Column("bulk_cp_molar", sa.Float, nullable=False),
        sa.Column("bulk_cp_mass", sa.Float, nullable=False),
        sa.Column("bulk_density_mass", sa.Float, nullable=False),
        sa.Column("bulk_density_molar", sa.Float, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("display_units", JSONB, nullable=False),
        sa.Column("warnings", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("ix_calculation_results_calc", "calculation_results", ["calculation_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("request_id", sa.String(40)),
        sa.Column("user_id", sa.String(36)),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.String(60)),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])

    op.create_table(
        "system_config",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "calculation_sequence",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in (
        "calculation_sequence",
        "system_config",
        "audit_logs",
        "calculation_results",
        "calculation_inputs",
        "calculations",
        "mixture_components",
        "mixtures",
        "users",
    ):
        op.drop_table(table)
