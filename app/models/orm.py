"""SQLAlchemy 2.x Declarative ORM Models for PostgreSQL production deployments.

When `sqlalchemy` is installed (e.g., in the Docker / PostgreSQL production stack),
this module defines full `Mapped` / `mapped_column` SQLAlchemy 2.x ORM models with
Numeric(18, 4) fixed-precision monetary columns, explicit CheckConstraints,
ForeignKeys, and Indexes. When running in lightweight SQLite mode without
SQLAlchemy installed, it gracefully exposes metadata descriptors for inspection.
"""

from __future__ import annotations

from app.db.base import Base, HAS_SQLALCHEMY

if HAS_SQLALCHEMY:
    from decimal import Decimal
    from typing import Any, Optional
    from sqlalchemy import (  # type: ignore
        Boolean,
        CheckConstraint,
        ForeignKey,
        Index,
        Integer,
        JSON,
        Numeric,
        String,
        Text,
        UniqueConstraint,
    )
    from sqlalchemy.orm import Mapped, mapped_column  # type: ignore

    class DataSourceORM(Base):
        __tablename__ = "data_sources"

        source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
        source_type: Mapped[str] = mapped_column(String(32), nullable=False)
        target_entity: Mapped[str] = mapped_column(String(32), nullable=False)
        connection_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
        version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class SchemaMappingORM(Base):
        __tablename__ = "schema_mappings"
        __table_args__ = (UniqueConstraint("source_id", "version", name="uq_source_version"),)

        mapping_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        source_id: Mapped[str] = mapped_column(
            String(36), ForeignKey("data_sources.source_id", ondelete="CASCADE"), nullable=False
        )
        version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
        field_mappings_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
        required_fields_json: Mapped[list[str]] = mapped_column(JSON, default=list)
        validation_rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class OrderORM(Base):
        __tablename__ = "orders"
        __table_args__ = (
            Index("idx_orders_order_id", "order_id"),
            Index("idx_orders_status", "status"),
        )

        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
        source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
        source_id: Mapped[Optional[str]] = mapped_column(
            String(36), ForeignKey("data_sources.source_id", ondelete="SET NULL"), nullable=True
        )
        customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        expected_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
        currency: Mapped[str] = mapped_column(String(3), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        line_items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
        event_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class PaymentORM(Base):
        """Represents payment gateway records and finance settlement entries.

        `order_id` intentionally omits a hard ForeignKey to `orders.order_id` so
        orphan payments referencing nonexistent orders can be persisted and detected.
        """

        __tablename__ = "payments"
        __table_args__ = (
            Index("idx_payments_order_id", "order_id"),
            Index("idx_payments_provider_charge_id", "provider_charge_id"),
            Index("idx_payments_source_system", "source_system"),
        )

        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        transaction_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
        provider_charge_id: Mapped[str] = mapped_column(String(64), nullable=False)
        order_id: Mapped[str] = mapped_column(String(64), nullable=False)
        source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
        source_system: Mapped[str] = mapped_column(
            String(32), default="payment_gateway", nullable=False
        )
        source_id: Mapped[Optional[str]] = mapped_column(
            String(36), ForeignKey("data_sources.source_id", ondelete="SET NULL"), nullable=True
        )
        amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
        captured_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
        currency: Mapped[str] = mapped_column(String(3), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        event_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class RefundORM(Base):
        __tablename__ = "refunds"
        __table_args__ = (
            Index("idx_refunds_order_id", "order_id"),
            Index("idx_refunds_transaction_id", "transaction_id"),
        )

        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        refund_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
        transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
        order_id: Mapped[str] = mapped_column(String(64), nullable=False)
        provider_refund_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
        source_id: Mapped[Optional[str]] = mapped_column(
            String(36), ForeignKey("data_sources.source_id", ondelete="SET NULL"), nullable=True
        )
        amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
        currency: Mapped[str] = mapped_column(String(3), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        event_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class InventoryRecordORM(Base):
        __tablename__ = "inventory_records"
        __table_args__ = (
            Index("idx_inventory_sku", "sku"),
            Index("idx_inventory_order_id", "order_id"),
        )

        id: Mapped[str] = mapped_column(String(36), primary_key=True)
        inventory_record_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
        sku: Mapped[str] = mapped_column(String(64), nullable=False)
        order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
        source_id: Mapped[Optional[str]] = mapped_column(
            String(36), ForeignKey("data_sources.source_id", ondelete="SET NULL"), nullable=True
        )
        delta_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
        available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
        reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
        warehouse_id: Mapped[str] = mapped_column(String(64), default="WH-MAIN", nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        event_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
        metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class ReconciliationRuleORM(Base):
        __tablename__ = "reconciliation_rules"

        rule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
        name: Mapped[str] = mapped_column(String(128), nullable=False)
        description: Mapped[str] = mapped_column(Text, nullable=False)
        rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
        severity: Mapped[str] = mapped_column(String(32), nullable=False)
        tolerance_amount: Mapped[Decimal] = mapped_column(
            Numeric(18, 4), default=Decimal("0.0000"), nullable=False
        )
        max_delay_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
        is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
        config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class ReconciliationRunORM(Base):
        __tablename__ = "reconciliation_runs"
        __table_args__ = (Index("idx_reconciliation_runs_status", "status"),)

        run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        idempotency_key: Mapped[Optional[str]] = mapped_column(
            String(128), unique=True, nullable=True
        )
        status: Mapped[str] = mapped_column(String(64), nullable=False)
        triggered_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
        source_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        rule_codes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
        input_counts_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
        output_counts_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
        discrepancy_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
        error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        started_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        completed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class DiscrepancyORM(Base):
        __tablename__ = "discrepancies"
        __table_args__ = (
            Index("idx_discrepancies_run_id", "run_id"),
            Index("idx_discrepancies_order_id", "order_id"),
            Index("idx_discrepancies_status", "status"),
        )

        discrepancy_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        run_id: Mapped[str] = mapped_column(
            String(36), ForeignKey("reconciliation_runs.run_id", ondelete="CASCADE"), nullable=False
        )
        rule_id: Mapped[str] = mapped_column(
            String(36),
            ForeignKey("reconciliation_rules.rule_id", ondelete="RESTRICT"),
            nullable=False,
        )
        rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
        discrepancy_type: Mapped[str] = mapped_column(String(64), nullable=False)
        severity: Mapped[str] = mapped_column(String(32), nullable=False)
        match_confidence: Mapped[str] = mapped_column(String(32), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        transaction_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        refund_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        sku: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        expected_value: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
        actual_value: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
        currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
        description: Mapped[str] = mapped_column(Text, nullable=False)
        record_references_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class InvestigationORM(Base):
        __tablename__ = "investigations"
        __table_args__ = (Index("idx_investigations_discrepancy_id", "discrepancy_id"),)

        investigation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        discrepancy_id: Mapped[str] = mapped_column(
            String(36), ForeignKey("discrepancies.discrepancy_id", ondelete="CASCADE"), nullable=False
        )
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        evidence_references_json: Mapped[list[str]] = mapped_column(JSON, default=list)
        candidate_causes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
        confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
        uncertainty_explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        missing_evidence_json: Mapped[list[str]] = mapped_column(JSON, default=list)
        next_diagnostic_step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        validation_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
        validation_errors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
        llm_model_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
        latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class ResolutionActionORM(Base):
        __tablename__ = "resolution_actions"
        __table_args__ = (Index("idx_resolutions_discrepancy_id", "discrepancy_id"),)

        resolution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        discrepancy_id: Mapped[str] = mapped_column(
            String(36), ForeignKey("discrepancies.discrepancy_id", ondelete="CASCADE"), nullable=False
        )
        investigation_id: Mapped[Optional[str]] = mapped_column(
            String(36),
            ForeignKey("investigations.investigation_id", ondelete="SET NULL"),
            nullable=True,
        )
        idempotency_key: Mapped[Optional[str]] = mapped_column(
            String(128), unique=True, nullable=True
        )
        action_type: Mapped[str] = mapped_column(String(64), nullable=False)
        status: Mapped[str] = mapped_column(String(64), nullable=False)
        requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
        proposed_by: Mapped[str] = mapped_column(String(64), default="agent", nullable=False)
        rationale: Mapped[str] = mapped_column(Text, nullable=False)
        action_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        evidence_references_json: Mapped[list[str]] = mapped_column(JSON, default=list)
        execution_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        verification_run_id: Mapped[Optional[str]] = mapped_column(
            String(36),
            ForeignKey("reconciliation_runs.run_id", ondelete="SET NULL"),
            nullable=True,
        )
        executed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        verified_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
        updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class ApprovalORM(Base):
        __tablename__ = "approvals"
        __table_args__ = (Index("idx_approvals_resolution_id", "resolution_id"),)

        approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        resolution_id: Mapped[str] = mapped_column(
            String(36),
            ForeignKey("resolution_actions.resolution_id", ondelete="CASCADE"),
            nullable=False,
        )
        decision: Mapped[str] = mapped_column(String(32), nullable=False)
        actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
        actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
        comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        decided_at: Mapped[str] = mapped_column(String(64), nullable=False)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)

    class AuditEventORM(Base):
        __tablename__ = "audit_events"
        __table_args__ = (
            Index("idx_audit_entity", "entity_type", "entity_id"),
            Index("idx_audit_correlation_id", "correlation_id"),
        )

        audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
        entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
        entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
        action: Mapped[str] = mapped_column(String(64), nullable=False)
        actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
        actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
        correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
        before_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        after_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
        created_at: Mapped[str] = mapped_column(String(64), nullable=False)
