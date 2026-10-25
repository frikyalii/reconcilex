"""Domain entity representations and SQLAlchemy 2.x / dataclass definitions for ReconcileX.

Covers all 13 required domain models:
- DataSource
- SchemaMapping
- Order
- Payment
- Refund
- InventoryRecord
- ReconciliationRule
- ReconciliationRun
- Discrepancy
- Investigation
- ResolutionAction
- Approval
- AuditEvent
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.db.base import quantize_amount, utcnow_iso
from app.models.enums import (
    ApprovalDecision,
    DiscrepancyStatus,
    InventoryStatus,
    InvestigationStatus,
    MatchConfidence,
    OrderStatus,
    PaymentSourceSystem,
    PaymentStatus,
    ReconciliationRunStatus,
    RefundStatus,
    ResolutionActionType,
    ResolutionStatus,
    RuleType,
    Severity,
    SourceType,
    TargetEntity,
)


def new_uuid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


@dataclass
class DataSource:
    name: str
    source_type: SourceType
    target_entity: TargetEntity
    connection_config: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    version: int = 1
    source_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class SchemaMapping:
    source_id: str
    field_mappings: dict[str, str]
    required_fields: list[str] = field(default_factory=list)
    validation_rules: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    mapping_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class Order:
    order_id: str
    source_record_id: str
    expected_amount: Decimal
    currency: str
    status: OrderStatus
    event_timestamp: str
    updated_timestamp: str
    source_id: str | None = None
    customer_id: str | None = None
    line_items: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        self.expected_amount = quantize_amount(self.expected_amount)
        self.currency = self.currency.strip().upper()


@dataclass
class Payment:
    transaction_id: str
    provider_charge_id: str
    order_id: str
    source_record_id: str
    amount: Decimal
    captured_amount: Decimal
    currency: str
    status: PaymentStatus
    event_timestamp: str
    updated_timestamp: str
    source_system: PaymentSourceSystem = PaymentSourceSystem.PAYMENT_GATEWAY
    source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        self.amount = quantize_amount(self.amount)
        self.captured_amount = quantize_amount(self.captured_amount)
        self.currency = self.currency.strip().upper()


@dataclass
class Refund:
    refund_id: str
    transaction_id: str
    order_id: str
    source_record_id: str
    amount: Decimal
    currency: str
    status: RefundStatus
    event_timestamp: str
    updated_timestamp: str
    provider_refund_id: str | None = None
    source_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        self.amount = quantize_amount(self.amount)
        self.currency = self.currency.strip().upper()


@dataclass
class InventoryRecord:
    inventory_record_id: str
    sku: str
    source_record_id: str
    delta_quantity: int
    available_quantity: int
    status: InventoryStatus
    event_timestamp: str
    updated_timestamp: str
    order_id: str | None = None
    source_id: str | None = None
    reserved_quantity: int = 0
    warehouse_id: str = "WH-MAIN"
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class ReconciliationRule:
    code: str
    name: str
    description: str
    rule_type: RuleType
    severity: Severity
    tolerance_amount: Decimal = Decimal("0.0000")
    max_delay_seconds: int = 3600
    is_active: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    rule_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        self.tolerance_amount = quantize_amount(self.tolerance_amount)


@dataclass
class ReconciliationRun:
    status: ReconciliationRunStatus = ReconciliationRunStatus.PENDING
    triggered_by: str = "system"
    idempotency_key: str | None = None
    source_snapshot: dict[str, Any] = field(default_factory=dict)
    rule_codes: list[str] = field(default_factory=list)
    input_counts: dict[str, int] = field(default_factory=dict)
    output_counts: dict[str, int] = field(default_factory=dict)
    discrepancy_count: int = 0
    error_summary: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    run_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class Discrepancy:
    run_id: str
    rule_id: str
    rule_code: str
    discrepancy_type: str
    severity: Severity
    match_confidence: MatchConfidence
    description: str
    status: DiscrepancyStatus = DiscrepancyStatus.OPEN
    order_id: str | None = None
    transaction_id: str | None = None
    refund_id: str | None = None
    sku: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    currency: str | None = None
    record_references: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    discrepancy_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class Investigation:
    discrepancy_id: str
    status: InvestigationStatus = InvestigationStatus.PENDING
    summary: str | None = None
    evidence_references: list[str] = field(default_factory=list)
    candidate_causes: list[dict[str, Any]] = field(default_factory=list)
    confidence_score: Decimal | None = None
    uncertainty_explanation: str | None = None
    missing_evidence: list[str] = field(default_factory=list)
    next_diagnostic_step: str | None = None
    validation_passed: bool = False
    validation_errors: list[str] = field(default_factory=list)
    llm_model_id: str | None = None
    latency_ms: int | None = None
    investigation_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class ResolutionAction:
    discrepancy_id: str
    action_type: ResolutionActionType
    rationale: str
    status: ResolutionStatus = ResolutionStatus.PROPOSED
    investigation_id: str | None = None
    idempotency_key: str | None = None
    requires_approval: bool = True
    proposed_by: str = "agent"
    action_payload: dict[str, Any] = field(default_factory=dict)
    evidence_references: list[str] = field(default_factory=list)
    execution_result: dict[str, Any] = field(default_factory=dict)
    verification_run_id: str | None = None
    executed_at: str | None = None
    verified_at: str | None = None
    resolution_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


@dataclass
class Approval:
    resolution_id: str
    decision: ApprovalDecision
    actor_id: str
    actor_role: str
    comment: str | None = None
    decided_at: str = field(default_factory=utcnow_iso)
    approval_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)


@dataclass
class AuditEvent:
    entity_type: str
    entity_id: str
    action: str
    actor_id: str
    actor_role: str
    correlation_id: str
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    audit_id: str = field(default_factory=new_uuid)
    created_at: str = field(default_factory=utcnow_iso)
