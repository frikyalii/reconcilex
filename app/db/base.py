"""Database schema definitions and DDL bootstrap for ReconcileX.

Provides:
1. SQLAlchemy 2.x DeclarativeBase when `sqlalchemy` is installed in the environment.
2. Complete, parameterized DDL schema definitions for all core tables (including
   `event_logs` for webhook/system evidence) so that both PostgreSQL (via
   SQLAlchemy/Alembic) and SQLite (in lightweight local/CI or air-gapped
   environments) enforce identical relational constraints, foreign keys,
   indexes, and fixed-precision monetary storage.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

try:
    from sqlalchemy.orm import DeclarativeBase  # type: ignore

    class Base(DeclarativeBase):
        """SQLAlchemy 2.x Declarative Base."""

        pass

    HAS_SQLALCHEMY = True
except ImportError:

    class Base:  # type: ignore
        """Fallback declarative base marker when running on built-in sqlite3 adapter."""

        pass

    HAS_SQLALCHEMY = False


FOUR_PLACES = Decimal("0.0001")


def utcnow_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def quantize_amount(value: Decimal | str | int) -> Decimal:
    """Convert a non-float monetary input into an exact 4-decimal-place Decimal.

    Rejects binary float inputs to prevent floating-point representation drift.
    """
    if isinstance(value, float):
        raise TypeError(
            "Binary float values are prohibited for financial amounts. "
            "Pass Decimal, str, or int instead."
        )
    dec = Decimal(str(value))
    return dec.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def format_decimal_db(value: Decimal | str | int) -> str:
    """Format a monetary Decimal as an exact fixed-scale string for persistence."""
    return str(quantize_amount(value))


# Complete relational schema covering all 13 required domain models + event_logs.
# Note: Payment.order_id intentionally does NOT enforce a strict FK to orders(order_id)
# so that orphan payments referencing nonexistent orders can be persisted and detected.
SCHEMA_DDL_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS data_sources (
        source_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        source_type TEXT NOT NULL CHECK (source_type IN ('csv', 'postgres', 'synthetic')),
        target_entity TEXT NOT NULL CHECK (target_entity IN ('orders', 'payments', 'refunds', 'inventory', 'finance_ledger', 'event_logs')),
        connection_config_json TEXT NOT NULL DEFAULT '{}',
        is_active INTEGER NOT NULL DEFAULT 1,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_mappings (
        mapping_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        field_mappings_json TEXT NOT NULL,
        required_fields_json TEXT NOT NULL DEFAULT '[]',
        validation_rules_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE CASCADE,
        UNIQUE (source_id, version)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL UNIQUE,
        source_record_id TEXT NOT NULL,
        source_id TEXT,
        customer_id TEXT,
        expected_amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled', 'refunded')),
        line_items_json TEXT NOT NULL DEFAULT '[]',
        event_timestamp TEXT NOT NULL,
        updated_timestamp TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);",
    """
    CREATE TABLE IF NOT EXISTS payments (
        id TEXT PRIMARY KEY,
        transaction_id TEXT NOT NULL UNIQUE,
        provider_charge_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        source_system TEXT NOT NULL DEFAULT 'payment_gateway' CHECK (source_system IN ('payment_gateway', 'finance_ledger')),
        source_id TEXT,
        amount TEXT NOT NULL,
        captured_amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('authorized', 'captured', 'failed', 'voided', 'refunded', 'partially_refunded', 'settled')),
        event_timestamp TEXT NOT NULL,
        updated_timestamp TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);",
    "CREATE INDEX IF NOT EXISTS idx_payments_provider_charge_id ON payments(provider_charge_id);",
    "CREATE INDEX IF NOT EXISTS idx_payments_source_system ON payments(source_system);",
    """
    CREATE TABLE IF NOT EXISTS refunds (
        id TEXT PRIMARY KEY,
        refund_id TEXT NOT NULL UNIQUE,
        transaction_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        provider_refund_id TEXT,
        source_record_id TEXT NOT NULL,
        source_id TEXT,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'processed', 'failed', 'cancelled')),
        reason TEXT,
        event_timestamp TEXT NOT NULL,
        updated_timestamp TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_refunds_order_id ON refunds(order_id);",
    "CREATE INDEX IF NOT EXISTS idx_refunds_transaction_id ON refunds(transaction_id);",
    """
    CREATE TABLE IF NOT EXISTS inventory_records (
        id TEXT PRIMARY KEY,
        inventory_record_id TEXT NOT NULL UNIQUE,
        sku TEXT NOT NULL,
        order_id TEXT,
        source_record_id TEXT NOT NULL,
        source_id TEXT,
        delta_quantity INTEGER NOT NULL,
        available_quantity INTEGER NOT NULL,
        reserved_quantity INTEGER NOT NULL DEFAULT 0,
        warehouse_id TEXT NOT NULL DEFAULT 'WH-MAIN',
        status TEXT NOT NULL CHECK (status IN ('reserved', 'deducted', 'restocked', 'adjusted')),
        event_timestamp TEXT NOT NULL,
        updated_timestamp TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_inventory_sku ON inventory_records(sku);",
    "CREATE INDEX IF NOT EXISTS idx_inventory_order_id ON inventory_records(order_id);",
    """
    CREATE TABLE IF NOT EXISTS event_logs (
        event_id TEXT PRIMARY KEY,
        source_record_id TEXT NOT NULL,
        source_id TEXT,
        event_type TEXT NOT NULL,
        order_id TEXT,
        transaction_id TEXT,
        refund_id TEXT,
        sku TEXT,
        delivery_status TEXT NOT NULL CHECK (delivery_status IN ('delivered', 'failed', 'retrying', 'dropped')),
        attempt_count INTEGER NOT NULL DEFAULT 1,
        error_code TEXT,
        message TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        event_timestamp TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES data_sources(source_id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_logs_order_id ON event_logs(order_id);",
    "CREATE INDEX IF NOT EXISTS idx_event_logs_transaction_id ON event_logs(transaction_id);",
    """
    CREATE TABLE IF NOT EXISTS reconciliation_rules (
        rule_id TEXT PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        rule_type TEXT NOT NULL CHECK (rule_type IN (
            'missing_payment',
            'missing_finance_settlement',
            'amount_mismatch',
            'multiple_captures',
            'orphan_payment',
            'missing_or_mismatched_refund',
            'excessive_refund',
            'inventory_inconsistency',
            'currency_mismatch',
            'duplicate_source_record',
            'stale_event'
        )),
        severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
        tolerance_amount TEXT NOT NULL DEFAULT '0.0000',
        max_delay_seconds INTEGER NOT NULL DEFAULT 3600,
        is_active INTEGER NOT NULL DEFAULT 1,
        config_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS reconciliation_runs (
        run_id TEXT PRIMARY KEY,
        idempotency_key TEXT UNIQUE,
        status TEXT NOT NULL CHECK (status IN (
            'pending',
            'running',
            'completed',
            'completed_with_discrepancies',
            'failed',
            'cancelled'
        )),
        triggered_by TEXT NOT NULL DEFAULT 'system',
        source_snapshot_json TEXT NOT NULL DEFAULT '{}',
        rule_codes_json TEXT NOT NULL DEFAULT '[]',
        input_counts_json TEXT NOT NULL DEFAULT '{}',
        output_counts_json TEXT NOT NULL DEFAULT '{}',
        discrepancy_count INTEGER NOT NULL DEFAULT 0,
        error_summary TEXT,
        started_at TEXT,
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_status ON reconciliation_runs(status);",
    """
    CREATE TABLE IF NOT EXISTS discrepancies (
        discrepancy_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        rule_code TEXT NOT NULL,
        discrepancy_type TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
        match_confidence TEXT NOT NULL CHECK (match_confidence IN ('confirmed', 'ambiguous')),
        status TEXT NOT NULL CHECK (status IN ('open', 'investigating', 'pending_approval', 'resolved', 'dismissed')),
        order_id TEXT,
        transaction_id TEXT,
        refund_id TEXT,
        sku TEXT,
        expected_value TEXT,
        actual_value TEXT,
        currency TEXT,
        description TEXT NOT NULL,
        record_references_json TEXT NOT NULL DEFAULT '{}',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES reconciliation_runs(run_id) ON DELETE CASCADE,
        FOREIGN KEY (rule_id) REFERENCES reconciliation_rules(rule_id) ON DELETE RESTRICT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_discrepancies_run_id ON discrepancies(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_discrepancies_order_id ON discrepancies(order_id);",
    "CREATE INDEX IF NOT EXISTS idx_discrepancies_status ON discrepancies(status);",
    """
    CREATE TABLE IF NOT EXISTS investigations (
        investigation_id TEXT PRIMARY KEY,
        discrepancy_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'inconclusive', 'failed')),
        summary TEXT,
        evidence_references_json TEXT NOT NULL DEFAULT '[]',
        candidate_causes_json TEXT NOT NULL DEFAULT '[]',
        confidence_score TEXT,
        uncertainty_explanation TEXT,
        missing_evidence_json TEXT NOT NULL DEFAULT '[]',
        next_diagnostic_step TEXT,
        validation_passed INTEGER NOT NULL DEFAULT 0,
        validation_errors_json TEXT NOT NULL DEFAULT '[]',
        llm_model_id TEXT,
        latency_ms INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (discrepancy_id) REFERENCES discrepancies(discrepancy_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_investigations_discrepancy_id ON investigations(discrepancy_id);",
    """
    CREATE TABLE IF NOT EXISTS resolution_actions (
        resolution_id TEXT PRIMARY KEY,
        discrepancy_id TEXT NOT NULL,
        investigation_id TEXT,
        idempotency_key TEXT UNIQUE,
        action_type TEXT NOT NULL CHECK (action_type IN (
            'retry_webhook_sync',
            'reprocess_payment_event',
            'refresh_derived_ledger',
            'sync_inventory_reservation',
            'request_manual_finance_review',
            'escalate_to_engineering'
        )),
        status TEXT NOT NULL CHECK (status IN (
            'proposed',
            'approved',
            'rejected',
            'executing',
            'executed_pending_verification',
            'verified_resolved',
            'verification_failed',
            'failed'
        )),
        requires_approval INTEGER NOT NULL DEFAULT 1,
        proposed_by TEXT NOT NULL DEFAULT 'agent',
        rationale TEXT NOT NULL,
        action_payload_json TEXT NOT NULL DEFAULT '{}',
        evidence_references_json TEXT NOT NULL DEFAULT '[]',
        execution_result_json TEXT NOT NULL DEFAULT '{}',
        verification_run_id TEXT,
        executed_at TEXT,
        verified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (discrepancy_id) REFERENCES discrepancies(discrepancy_id) ON DELETE CASCADE,
        FOREIGN KEY (investigation_id) REFERENCES investigations(investigation_id) ON DELETE SET NULL,
        FOREIGN KEY (verification_run_id) REFERENCES reconciliation_runs(run_id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_resolutions_discrepancy_id ON resolution_actions(discrepancy_id);",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        resolution_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
        actor_id TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        comment TEXT,
        decided_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (resolution_id) REFERENCES resolution_actions(resolution_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_approvals_resolution_id ON approvals(resolution_id);",
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        audit_id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        before_state_json TEXT NOT NULL DEFAULT '{}',
        after_state_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_correlation_id ON audit_events(correlation_id);",
]


def initialize_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Execute all schema DDL statements and enable foreign key constraints."""
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()
    try:
        for statement in SCHEMA_DDL_STATEMENTS:
            cursor.execute(statement)
        conn.commit()
    finally:
        cursor.close()
