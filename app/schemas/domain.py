"""Pydantic request and response schemas for ReconcileX domain models and ingestion reports."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, validator

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
from app.schemas.common import DecimalSafeBaseModel, validate_non_float_decimal


class SchemaMappingInline(DecimalSafeBaseModel):
    """Inline schema mapping configuration when creating or updating a DataSource."""

    field_mappings: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps canonical target field name -> source column/key name",
    )
    required_fields: List[str] = Field(
        default_factory=list,
        description="Canonical fields that must be non-empty in every source row",
    )
    validation_rules: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional field-level validation rules (e.g. allowed_currencies)",
    )


class DataSourceCreate(DecimalSafeBaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    source_type: SourceType
    target_entity: TargetEntity
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    schema_mapping: Optional[SchemaMappingInline] = None


class SchemaMappingCreate(DecimalSafeBaseModel):
    source_id: str
    field_mappings: Dict[str, str]
    required_fields: List[str] = Field(default_factory=list)
    validation_rules: Dict[str, Any] = Field(default_factory=dict)


class SchemaMappingRead(SchemaMappingCreate):
    mapping_id: str
    version: int
    created_at: str
    updated_at: str


class DataSourceRead(DecimalSafeBaseModel):
    source_id: str
    name: str
    source_type: SourceType
    target_entity: TargetEntity
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    version: int
    schema_mapping: Optional[SchemaMappingRead] = None
    created_at: str
    updated_at: str


class RowValidationError(DecimalSafeBaseModel):
    """Detailed row-level validation error produced during source validation or import."""

    row_number: int = Field(..., ge=1, description="1-based row index in the source dataset")
    source_record_id: Optional[str] = Field(
        default=None, description="Source record identifier if present"
    )
    field: Optional[str] = Field(default=None, description="Canonical or source field that failed")
    error_code: str = Field(..., description="Machine-readable validation error code")
    message: str = Field(..., description="Human-readable explanation of why the row is invalid")
    raw_row: Dict[str, Any] = Field(default_factory=dict, description="Original raw row values")


class SourceImportRequest(DecimalSafeBaseModel):
    """Optional inline payload for validating or importing data into a configured source."""

    csv_content: Optional[str] = Field(
        default=None,
        description="Raw CSV text content (used when not reading from connection_config.file_path)",
    )
    rows: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Inline list of raw dictionary rows for synthetic or programmatic ingestion",
    )
    scenario_name: Optional[str] = Field(
        default=None,
        description="Named synthetic dataset preset (e.g., 'doyuf_full_suite' or 'dy1042_missing_ledger')",
    )


class SourceValidationReport(DecimalSafeBaseModel):
    """Report returned by POST /api/v1/sources/{source_id}/validate."""

    source_id: str
    source_name: str
    source_type: SourceType
    target_entity: TargetEntity
    total_rows: int
    valid_rows: int
    invalid_rows: int
    is_valid: bool
    row_errors: List[RowValidationError] = Field(default_factory=list)


class SourceImportReport(DecimalSafeBaseModel):
    """Report returned by POST /api/v1/sources/{source_id}/import."""

    source_id: str
    source_name: str
    source_type: SourceType
    target_entity: TargetEntity
    total_rows: int
    imported_rows: int
    skipped_duplicate_rows: int = 0
    invalid_rows: int
    row_errors: List[RowValidationError] = Field(default_factory=list)
    imported_ids: List[str] = Field(default_factory=list)


class OrderCreate(DecimalSafeBaseModel):
    order_id: str = Field(..., min_length=1, max_length=64)
    source_record_id: str = Field(..., min_length=1, max_length=128)
    expected_amount: Decimal
    currency: str = Field(default="INR", min_length=3, max_length=3)
    status: OrderStatus
    event_timestamp: str
    updated_timestamp: str
    source_id: Optional[str] = None
    customer_id: Optional[str] = None
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_amount = validator("expected_amount", pre=True, allow_reuse=True)(
        validate_non_float_decimal
    )

    @validator("currency")
    def _normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class OrderRead(OrderCreate):
    id: str
    created_at: str
    updated_at: str


class PaymentCreate(DecimalSafeBaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=64)
    provider_charge_id: str = Field(..., min_length=1, max_length=64)
    order_id: str = Field(..., min_length=1, max_length=64)
    source_record_id: str = Field(..., min_length=1, max_length=128)
    amount: Decimal
    captured_amount: Decimal
    currency: str = Field(default="INR", min_length=3, max_length=3)
    status: PaymentStatus
    source_system: PaymentSourceSystem = PaymentSourceSystem.PAYMENT_GATEWAY
    event_timestamp: str
    updated_timestamp: str
    source_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_amounts = validator("amount", "captured_amount", pre=True, allow_reuse=True)(
        validate_non_float_decimal
    )

    @validator("currency")
    def _normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class PaymentRead(PaymentCreate):
    id: str
    created_at: str
    updated_at: str


class RefundCreate(DecimalSafeBaseModel):
    refund_id: str = Field(..., min_length=1, max_length=64)
    transaction_id: str = Field(..., min_length=1, max_length=64)
    order_id: str = Field(..., min_length=1, max_length=64)
    source_record_id: str = Field(..., min_length=1, max_length=128)
    amount: Decimal
    currency: str = Field(default="INR", min_length=3, max_length=3)
    status: RefundStatus
    event_timestamp: str
    updated_timestamp: str
    provider_refund_id: Optional[str] = None
    source_id: Optional[str] = None
    reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_amount = validator("amount", pre=True, allow_reuse=True)(
        validate_non_float_decimal
    )

    @validator("currency")
    def _normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class RefundRead(RefundCreate):
    id: str
    created_at: str
    updated_at: str


class InventoryRecordCreate(DecimalSafeBaseModel):
    inventory_record_id: str = Field(..., min_length=1, max_length=64)
    sku: str = Field(..., min_length=1, max_length=64)
    source_record_id: str = Field(..., min_length=1, max_length=128)
    delta_quantity: int
    available_quantity: int = Field(..., ge=0)
    reserved_quantity: int = Field(default=0, ge=0)
    warehouse_id: str = Field(default="WH-MAIN", min_length=1, max_length=64)
    status: InventoryStatus
    event_timestamp: str
    updated_timestamp: str
    order_id: Optional[str] = None
    source_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InventoryRecordRead(InventoryRecordCreate):
    id: str
    created_at: str
    updated_at: str


class EventLogCreate(DecimalSafeBaseModel):
    event_id: Optional[str] = None
    source_record_id: str = Field(..., min_length=1, max_length=128)
    source_id: Optional[str] = None
    event_type: str = Field(..., min_length=1, max_length=64)
    order_id: Optional[str] = None
    transaction_id: Optional[str] = None
    refund_id: Optional[str] = None
    sku: Optional[str] = None
    delivery_status: Literal["delivered", "failed", "retrying", "dropped"] = "failed"
    attempt_count: int = Field(default=1, ge=1)
    error_code: Optional[str] = None
    message: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    event_timestamp: str


class EventLogRead(EventLogCreate):
    event_id: str
    created_at: str


class ReconciliationRuleCreate(DecimalSafeBaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str
    rule_type: RuleType
    severity: Severity
    tolerance_amount: Decimal = Decimal("0.0000")
    max_delay_seconds: int = Field(default=3600, ge=0)
    is_active: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)

    _validate_tolerance = validator("tolerance_amount", pre=True, allow_reuse=True)(
        validate_non_float_decimal
    )


class ReconciliationRuleRead(ReconciliationRuleCreate):
    rule_id: str
    created_at: str
    updated_at: str


class ReconciliationRunCreate(DecimalSafeBaseModel):
    """Request payload for triggering a deterministic reconciliation run."""

    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional client idempotency key to prevent duplicate runs",
    )
    triggered_by: str = Field(
        default="api_operator",
        max_length=64,
        description="Actor or system triggering the reconciliation run",
    )
    rule_codes: Optional[List[str]] = Field(
        default=None,
        description="Subset of active rule codes to evaluate; evaluates all active rules when omitted",
    )
    reference_timestamp: Optional[str] = Field(
        default=None,
        description="Optional ISO-8601 reference timestamp for deterministic stale-event window checks",
    )
    async_execution: bool = Field(
        default=False,
        description="When True (Phase 4 Celery mode), queues background execution and returns 202 Accepted",
    )


class ReconciliationRunRead(DecimalSafeBaseModel):
    run_id: str
    idempotency_key: Optional[str] = None
    status: ReconciliationRunStatus
    triggered_by: str
    source_snapshot: Dict[str, Any]
    rule_codes: List[str]
    input_counts: Dict[str, int]
    output_counts: Dict[str, int]
    discrepancy_count: int
    error_summary: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str
    updated_at: str


class DiscrepancyCreate(DecimalSafeBaseModel):
    run_id: str
    rule_id: str
    rule_code: str
    discrepancy_type: str
    severity: Severity
    match_confidence: MatchConfidence
    description: str
    status: DiscrepancyStatus = DiscrepancyStatus.OPEN
    order_id: Optional[str] = None
    transaction_id: Optional[str] = None
    refund_id: Optional[str] = None
    sku: Optional[str] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    currency: Optional[str] = None
    record_references: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)


class DiscrepancyRead(DiscrepancyCreate):
    discrepancy_id: str
    created_at: str
    updated_at: str


class InvestigationRead(DecimalSafeBaseModel):
    investigation_id: str
    discrepancy_id: str
    status: InvestigationStatus
    summary: Optional[str] = None
    evidence_references: List[str]
    candidate_causes: List[Dict[str, Any]]
    confidence_score: Optional[Decimal] = None
    uncertainty_explanation: Optional[str] = None
    missing_evidence: List[str]
    next_diagnostic_step: Optional[str] = None
    validation_passed: bool
    validation_errors: List[str]
    llm_model_id: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: str
    updated_at: str


class ResolutionActionCreate(DecimalSafeBaseModel):
    """Request payload for creating a proposed ResolutionAction."""

    discrepancy_id: str = Field(..., min_length=1, max_length=64)
    investigation_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    action_type: ResolutionActionType
    requires_approval: bool = True
    proposed_by: str = Field(default="operator", max_length=64)
    rationale: str = Field(..., min_length=1)
    action_payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_references: List[str] = Field(default_factory=list)


class ResolutionDecisionRequest(DecimalSafeBaseModel):
    """Request payload for approving or rejecting a proposed ResolutionAction."""

    comment: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Operator justification for approving or rejecting the resolution action",
    )


class ResolutionExecuteRequest(DecimalSafeBaseModel):
    """Request payload for executing an approved ResolutionAction."""

    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional execution idempotency key",
    )
    simulate_noop: bool = Field(
        default=False,
        description="If True, simulates an incomplete recovery action to test post-resolution verification failure",
    )


class ResolutionActionRead(DecimalSafeBaseModel):
    resolution_id: str
    discrepancy_id: str
    investigation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    action_type: ResolutionActionType
    status: ResolutionStatus
    requires_approval: bool
    proposed_by: str
    rationale: str
    action_payload: Dict[str, Any]
    evidence_references: List[str]
    execution_result: Dict[str, Any]
    verification_run_id: Optional[str] = None
    executed_at: Optional[str] = None
    verified_at: Optional[str] = None
    created_at: str
    updated_at: str


class ApprovalRead(DecimalSafeBaseModel):
    approval_id: str
    resolution_id: str
    decision: ApprovalDecision
    actor_id: str
    actor_role: str
    comment: Optional[str] = None
    decided_at: str
    created_at: str


class AuditEventRead(DecimalSafeBaseModel):
    audit_id: str
    entity_type: str
    entity_id: str
    action: str
    actor_id: str
    actor_role: str
    correlation_id: str
    before_state: Dict[str, Any]
    after_state: Dict[str, Any]
    metadata: Dict[str, Any]
    created_at: str
