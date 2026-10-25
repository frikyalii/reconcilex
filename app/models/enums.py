"""Domain enumerations and state transition guards for ReconcileX."""

from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    CSV = "csv"
    POSTGRES = "postgres"
    SYNTHETIC = "synthetic"


class TargetEntity(str, Enum):
    ORDERS = "orders"
    PAYMENTS = "payments"
    REFUNDS = "refunds"
    INVENTORY = "inventory"
    FINANCE_LEDGER = "finance_ledger"
    EVENT_LOGS = "event_logs"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentSourceSystem(str, Enum):
    PAYMENT_GATEWAY = "payment_gateway"
    FINANCE_LEDGER = "finance_ledger"


class PaymentStatus(str, Enum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    VOIDED = "voided"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    SETTLED = "settled"


class RefundStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InventoryStatus(str, Enum):
    RESERVED = "reserved"
    DEDUCTED = "deducted"
    RESTOCKED = "restocked"
    ADJUSTED = "adjusted"


class RuleType(str, Enum):
    MISSING_PAYMENT = "missing_payment"
    MISSING_FINANCE_SETTLEMENT = "missing_finance_settlement"
    AMOUNT_MISMATCH = "amount_mismatch"
    MULTIPLE_CAPTURES = "multiple_captures"
    ORPHAN_PAYMENT = "orphan_payment"
    MISSING_OR_MISMATCHED_REFUND = "missing_or_mismatched_refund"
    EXCESSIVE_REFUND = "excessive_refund"
    INVENTORY_INCONSISTENCY = "inventory_inconsistency"
    CURRENCY_MISMATCH = "currency_mismatch"
    DUPLICATE_SOURCE_RECORD = "duplicate_source_record"
    STALE_EVENT = "stale_event"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MatchConfidence(str, Enum):
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"


class ReconciliationRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_DISCREPANCIES = "completed_with_discrepancies"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DiscrepancyStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    PENDING_APPROVAL = "pending_approval"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class InvestigationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


class ResolutionActionType(str, Enum):
    RETRY_WEBHOOK_SYNC = "retry_webhook_sync"
    REPROCESS_PAYMENT_EVENT = "reprocess_payment_event"
    REFRESH_DERIVED_LEDGER = "refresh_derived_ledger"
    SYNC_INVENTORY_RESERVATION = "sync_inventory_reservation"
    REQUEST_MANUAL_FINANCE_REVIEW = "request_manual_finance_review"
    ESCALATE_TO_ENGINEERING = "escalate_to_engineering"


class ResolutionStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED_PENDING_VERIFICATION = "executed_pending_verification"
    VERIFIED_RESOLVED = "verified_resolved"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


# Explicit state transition rules for reconciliation runs and resolution actions
ALLOWED_RUN_TRANSITIONS: dict[ReconciliationRunStatus, set[ReconciliationRunStatus]] = {
    ReconciliationRunStatus.PENDING: {
        ReconciliationRunStatus.RUNNING,
        ReconciliationRunStatus.CANCELLED,
        ReconciliationRunStatus.FAILED,
    },
    ReconciliationRunStatus.RUNNING: {
        ReconciliationRunStatus.COMPLETED,
        ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES,
        ReconciliationRunStatus.FAILED,
        ReconciliationRunStatus.CANCELLED,
    },
    ReconciliationRunStatus.FAILED: {
        ReconciliationRunStatus.PENDING,
        ReconciliationRunStatus.RUNNING,
    },
    ReconciliationRunStatus.COMPLETED: set(),
    ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES: set(),
    ReconciliationRunStatus.CANCELLED: set(),
}

ALLOWED_RESOLUTION_TRANSITIONS: dict[ResolutionStatus, set[ResolutionStatus]] = {
    ResolutionStatus.PROPOSED: {
        ResolutionStatus.APPROVED,
        ResolutionStatus.REJECTED,
    },
    ResolutionStatus.APPROVED: {
        ResolutionStatus.EXECUTING,
        ResolutionStatus.FAILED,
    },
    ResolutionStatus.EXECUTING: {
        ResolutionStatus.EXECUTED_PENDING_VERIFICATION,
        ResolutionStatus.FAILED,
    },
    ResolutionStatus.EXECUTED_PENDING_VERIFICATION: {
        ResolutionStatus.VERIFIED_RESOLVED,
        ResolutionStatus.VERIFICATION_FAILED,
    },
    ResolutionStatus.REJECTED: set(),
    ResolutionStatus.VERIFIED_RESOLVED: set(),
    ResolutionStatus.VERIFICATION_FAILED: {
        ResolutionStatus.EXECUTING,
    },
    ResolutionStatus.FAILED: {
        ResolutionStatus.EXECUTING,
    },
}


def validate_run_transition(
    current: ReconciliationRunStatus, target: ReconciliationRunStatus
) -> bool:
    """Return True if transitioning a ReconciliationRun from `current` to `target` is valid."""
    return target in ALLOWED_RUN_TRANSITIONS.get(current, set())


def validate_resolution_transition(
    current: ResolutionStatus, target: ResolutionStatus
) -> bool:
    """Return True if transitioning a ResolutionAction from `current` to `target` is valid."""
    return target in ALLOWED_RESOLUTION_TRANSITIONS.get(current, set())
