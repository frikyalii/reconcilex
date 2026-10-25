"""Initial ReconcileX schema: 13 core tables + event_logs.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-10-24 06:30:00
"""

from __future__ import annotations

from app.db.base import SCHEMA_DDL_STATEMENTS

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

DROP_TABLES_ORDER: list[str] = [
    "audit_events",
    "approvals",
    "resolution_actions",
    "investigations",
    "discrepancies",
    "reconciliation_runs",
    "reconciliation_rules",
    "event_logs",
    "inventory_records",
    "refunds",
    "payments",
    "orders",
    "schema_mappings",
    "data_sources",
]


def upgrade() -> None:
    """Apply initial schema DDL statements."""
    from alembic import op  # type: ignore

    for ddl in SCHEMA_DDL_STATEMENTS:
        op.execute(ddl)


def downgrade() -> None:
    """Drop all ReconcileX tables in reverse dependency order."""
    from alembic import op  # type: ignore

    for table_name in DROP_TABLES_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table_name};")
