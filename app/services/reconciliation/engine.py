"""Deterministic reconciliation engine for ReconcileX.

Compares normalized records across Orders, Payment Gateway, Finance Settlement Ledger,
Refunds, and Inventory using configurable rules and exact `Decimal` arithmetic.

Key Engineering Guarantees:
1. Zero binary floating-point arithmetic: all financial comparisons use quantized
   4-decimal-place `Decimal` values (`ROUND_HALF_UP`).
2. Provider-charge deduplication: multiple internal payment records sharing the
   same `provider_charge_id` are flagged under `RULE_DUPLICATE_SOURCE_RECORD` rather
   than falsely treated as multiple real provider charges; only distinct
   `provider_charge_id` captures trigger `RULE_MULTIPLE_CAPTURES`.
3. Confirmed vs. Ambiguous separation: each finding carries `match_confidence`
   (`confirmed` or `ambiguous`) based on whether tolerance bands, pending settlements,
   or partial-capture windows apply.
4. Reproducible input snapshots: every run computes a deterministic SHA-256 digest
   of the normalized input state and active rule configuration (`source_snapshot`).
5. Durable job lifecycle: supports both synchronous execution and asynchronous
   Celery worker execution (`enqueue_run`, `execute_existing_run`, `retry_run`, `cancel_run`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.db.base import format_decimal_db, quantize_amount, utcnow_iso
from app.db.session import DatabaseSession
from app.models.enums import (
    InventoryStatus,
    MatchConfidence,
    OrderStatus,
    PaymentSourceSystem,
    PaymentStatus,
    ReconciliationRunStatus,
    RefundStatus,
    RuleType,
    Severity,
)
from app.repositories.audit_repo import AuditRepository
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.repositories.records_repo import RecordsRepository
from app.schemas.domain import (
    DiscrepancyCreate,
    DiscrepancyRead,
    InventoryRecordRead,
    OrderRead,
    PaymentRead,
    ReconciliationRuleCreate,
    ReconciliationRuleRead,
    ReconciliationRunRead,
    RefundRead,
)


DEFAULT_RECONCILIATION_RULES: List[ReconciliationRuleCreate] = [
    ReconciliationRuleCreate(
        code="RULE_MISSING_PAYMENT",
        name="Missing Gateway Payment for Confirmed Order",
        description=(
            "Flags confirmed, shipped, or delivered orders that have no captured, settled, "
            "or refunded payment record in the payment gateway."
        ),
        rule_type=RuleType.MISSING_PAYMENT,
        severity=Severity.HIGH,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_MISSING_FINANCE_SETTLEMENT",
        name="Missing Finance Ledger Settlement for Captured Gateway Payment",
        description=(
            "Flags captured or settled gateway payments that have no corresponding settlement "
            "record in the finance database matching provider_charge_id or order_id."
        ),
        rule_type=RuleType.MISSING_FINANCE_SETTLEMENT,
        severity=Severity.CRITICAL,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_AMOUNT_MISMATCH",
        name="Order vs. Payment Captured Amount Mismatch",
        description=(
            "Flags orders where the captured payment amount differs from the order expected_amount "
            "beyond the configured tolerance_amount."
        ),
        rule_type=RuleType.AMOUNT_MISMATCH,
        severity=Severity.HIGH,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
        config={"ambiguous_band_amount": "10.0000"},
    ),
    ReconciliationRuleCreate(
        code="RULE_MULTIPLE_CAPTURES",
        name="Multiple Distinct Provider Captures for Single Order",
        description=(
            "Flags orders associated with multiple captured gateway payment records that carry "
            "distinct provider_charge_id values."
        ),
        rule_type=RuleType.MULTIPLE_CAPTURES,
        severity=Severity.CRITICAL,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_ORPHAN_PAYMENT",
        name="Payment Referencing Nonexistent Order",
        description="Flags payment records whose order_id does not exist in the Order Management System.",
        rule_type=RuleType.ORPHAN_PAYMENT,
        severity=Severity.HIGH,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_MISSING_OR_MISMATCHED_REFUND",
        name="Missing or Unlinked Refund Record",
        description=(
            "Flags orders marked as 'refunded' that lack a processed refund record, or refunds "
            "referencing a nonexistent payment transaction_id."
        ),
        rule_type=RuleType.MISSING_OR_MISMATCHED_REFUND,
        severity=Severity.HIGH,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_EXCESSIVE_REFUND",
        name="Cumulative Refund Exceeds Captured Amount",
        description=(
            "Flags orders or transactions where the cumulative processed refund amount exceeds "
            "the total captured payment amount beyond tolerance."
        ),
        rule_type=RuleType.EXCESSIVE_REFUND,
        severity=Severity.CRITICAL,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_INVENTORY_INCONSISTENCY",
        name="Order Line-Item Quantity vs. Inventory Deduction Mismatch",
        description=(
            "Flags confirmed/shipped/delivered orders where total deducted inventory units per SKU "
            "do not match the ordered SKU quantities."
        ),
        rule_type=RuleType.INVENTORY_INCONSISTENCY,
        severity=Severity.MEDIUM,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_CURRENCY_MISMATCH",
        name="Cross-System Currency Mismatch",
        description="Flags payments or refunds whose normalized 3-letter currency code differs from the order currency.",
        rule_type=RuleType.CURRENCY_MISMATCH,
        severity=Severity.CRITICAL,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_DUPLICATE_SOURCE_RECORD",
        name="Duplicate Provider Charge or Source Record",
        description=(
            "Flags multiple internal records within the same source system that share the same "
            "provider_charge_id or source_record_id."
        ),
        rule_type=RuleType.DUPLICATE_SOURCE_RECORD,
        severity=Severity.MEDIUM,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
    ReconciliationRuleCreate(
        code="RULE_STALE_EVENT",
        name="Stale or Delayed Order / Payment Event",
        description=(
            "Flags pending orders, authorized-only payments, or unsettled gateway captures that "
            "have exceeded max_delay_seconds relative to the reconciliation reference timestamp."
        ),
        rule_type=RuleType.STALE_EVENT,
        severity=Severity.MEDIUM,
        tolerance_amount=Decimal("0.0000"),
        max_delay_seconds=3600,
    ),
]


def parse_iso_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    if not ts_str:
        return None
    cleaned = ts_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


class ReconciliationEngine:
    """Deterministic reconciliation engine evaluating normalized records against active rules."""

    def __init__(self, session: DatabaseSession, batch_size: int = 1000) -> None:
        self._session = session
        self._batch_size = batch_size
        self._recon_repo = ReconciliationRepository(session)
        self._records_repo = RecordsRepository(session)
        self._audit_repo = AuditRepository(session)

    def ensure_default_rules(self) -> List[ReconciliationRuleRead]:
        """Seed the 11 default reconciliation rules idempotently if not already present."""
        existing_by_code = {r.code: r for r in self._recon_repo.list_rules(limit=500)}
        for rule_def in DEFAULT_RECONCILIATION_RULES:
            if rule_def.code not in existing_by_code:
                created = self._recon_repo.create_rule(rule_def)
                existing_by_code[created.code] = created
        return self._recon_repo.list_rules(active_only=True, limit=500)

    def enqueue_run(
        self,
        triggered_by: str = "system",
        idempotency_key: Optional[str] = None,
        rule_codes: Optional[List[str]] = None,
        reference_timestamp: Optional[str] = None,
    ) -> ReconciliationRunRead:
        """Create a durable `pending` ReconciliationRun in the database prior to async worker dispatch."""
        if idempotency_key:
            existing = self._recon_repo.get_run_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        self.ensure_default_rules()
        all_active_rules = self._recon_repo.list_rules(active_only=True, limit=500)
        if rule_codes:
            requested_set = set(rule_codes)
            active_rules = [r for r in all_active_rules if r.code in requested_set]
        else:
            active_rules = all_active_rules

        initial_snapshot: Dict[str, Any] = {}
        if reference_timestamp:
            initial_snapshot["reference_timestamp"] = reference_timestamp

        run = self._recon_repo.create_run(
            triggered_by=triggered_by,
            idempotency_key=idempotency_key,
            rule_codes=[r.code for r in active_rules],
            source_snapshot=initial_snapshot,
        )
        self._audit_repo.record_event(
            entity_type="reconciliation_run",
            entity_id=run.run_id,
            action="reconciliation_run_enqueued",
            actor_id=triggered_by,
            actor_role="system",
            after_state={"status": run.status.value, "idempotency_key": idempotency_key},
        )
        return run

    def execute_run(
        self,
        triggered_by: str = "system",
        idempotency_key: Optional[str] = None,
        rule_codes: Optional[List[str]] = None,
        reference_timestamp: Optional[str] = None,
    ) -> Tuple[ReconciliationRunRead, List[DiscrepancyRead]]:
        """Create (or look up by idempotency_key) and execute a reconciliation run."""
        run = self.enqueue_run(
            triggered_by=triggered_by,
            idempotency_key=idempotency_key,
            rule_codes=rule_codes,
            reference_timestamp=reference_timestamp,
        )
        return self.execute_existing_run(
            run_id=run.run_id,
            reference_timestamp=reference_timestamp,
        )

    def execute_existing_run(
        self,
        run_id: str,
        reference_timestamp: Optional[str] = None,
    ) -> Tuple[ReconciliationRunRead, List[DiscrepancyRead]]:
        """Execute a durable `pending` or `failed` ReconciliationRun idempotently."""
        run = self._recon_repo.get_run(run_id)
        if run is None:
            raise KeyError(f"ReconciliationRun '{run_id}' not found.")

        # Idempotent short-circuit: if already completed, return persisted findings
        if run.status in (
            ReconciliationRunStatus.COMPLETED,
            ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES,
        ):
            findings = self._recon_repo.list_discrepancies_for_run(run.run_id)
            return run, findings

        if run.status == ReconciliationRunStatus.CANCELLED:
            raise ValueError(
                f"Cannot execute ReconciliationRun '{run_id}' because its status is 'cancelled'."
            )

        # If retrying a failed run, clear any partial discrepancies from the failed attempt
        if run.status == ReconciliationRunStatus.FAILED:
            self._recon_repo.clear_discrepancies_for_run(run.run_id)

        self.ensure_default_rules()
        all_active_rules = self._recon_repo.list_rules(active_only=True, limit=500)
        if run.rule_codes:
            requested_set = set(run.rule_codes)
            active_rules = [r for r in all_active_rules if r.code in requested_set]
        else:
            active_rules = all_active_rules

        rules_by_type: Dict[RuleType, ReconciliationRuleRead] = {
            r.rule_type: r for r in active_rules
        }

        started_at = utcnow_iso()
        run = self._recon_repo.update_run_state(
            run.run_id,
            target_status=ReconciliationRunStatus.RUNNING,
            clear_error_summary=True,
            started_at=started_at,
        )

        try:
            orders = self._load_all_orders()
            gateway_payments = self._load_payments_by_system(
                PaymentSourceSystem.PAYMENT_GATEWAY.value
            )
            ledger_payments = self._load_payments_by_system(
                PaymentSourceSystem.FINANCE_LEDGER.value
            )
            refunds = self._load_all_refunds()
            inventory_records = self._load_all_inventory()

            input_counts = {
                "orders": len(orders),
                "payment_gateway_records": len(gateway_payments),
                "finance_ledger_records": len(ledger_payments),
                "refund_records": len(refunds),
                "inventory_records": len(inventory_records),
            }

            eff_ref_ts = reference_timestamp or run.source_snapshot.get(
                "reference_timestamp"
            )
            snapshot = self._compute_input_snapshot(
                orders=orders,
                gateway_payments=gateway_payments,
                ledger_payments=ledger_payments,
                refunds=refunds,
                inventory_records=inventory_records,
                active_rules=active_rules,
            )
            if eff_ref_ts:
                snapshot["reference_timestamp"] = eff_ref_ts

            ref_dt = parse_iso_timestamp(str(eff_ref_ts)) if eff_ref_ts else None

            draft_discrepancies = self._evaluate_all_rules(
                run_id=run.run_id,
                rules_by_type=rules_by_type,
                orders=orders,
                gateway_payments=gateway_payments,
                ledger_payments=ledger_payments,
                refunds=refunds,
                inventory_records=inventory_records,
                reference_dt=ref_dt,
            )

            draft_discrepancies.sort(
                key=lambda d: (
                    d.rule_code,
                    d.order_id or "",
                    d.transaction_id or "",
                    d.refund_id or "",
                    d.sku or "",
                    d.description,
                )
            )

            persisted_discrepancies: List[DiscrepancyRead] = []
            by_severity: Dict[str, int] = defaultdict(int)
            by_confidence: Dict[str, int] = defaultdict(int)

            for item in draft_discrepancies:
                saved = self._recon_repo.create_discrepancy(item)
                persisted_discrepancies.append(saved)
                by_severity[saved.severity.value] += 1
                by_confidence[saved.match_confidence.value] += 1

            output_counts = {
                "total_discrepancies": len(persisted_discrepancies),
                "confirmed_discrepancies": by_confidence.get("confirmed", 0),
                "ambiguous_discrepancies": by_confidence.get("ambiguous", 0),
                "critical_severity": by_severity.get("critical", 0),
                "high_severity": by_severity.get("high", 0),
                "medium_severity": by_severity.get("medium", 0),
                "low_severity": by_severity.get("low", 0),
            }

            final_status = (
                ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES
                if len(persisted_discrepancies) > 0
                else ReconciliationRunStatus.COMPLETED
            )
            completed_at = utcnow_iso()
            run = self._recon_repo.update_run_state(
                run.run_id,
                target_status=final_status,
                source_snapshot=snapshot,
                input_counts=input_counts,
                output_counts=output_counts,
                discrepancy_count=len(persisted_discrepancies),
                clear_error_summary=True,
                completed_at=completed_at,
            )

            self._audit_repo.record_event(
                entity_type="reconciliation_run",
                entity_id=run.run_id,
                action="reconciliation_run_completed",
                actor_id=run.triggered_by,
                actor_role="system",
                after_state={
                    "status": final_status.value,
                    "discrepancy_count": len(persisted_discrepancies),
                    "snapshot_hash": snapshot.get("snapshot_sha256"),
                },
            )
            return run, persisted_discrepancies

        except Exception as exc:
            self._recon_repo.update_run_state(
                run.run_id,
                target_status=ReconciliationRunStatus.FAILED,
                error_summary=f"{type(exc).__name__}: {exc}",
                completed_at=utcnow_iso(),
            )
            raise

    def cancel_run(
        self, run_id: str, actor_id: str = "operator", actor_role: str = "operator"
    ) -> ReconciliationRunRead:
        """Cancel a `pending` or `running` reconciliation run."""
        run = self._recon_repo.get_run(run_id)
        if run is None:
            raise KeyError(f"ReconciliationRun '{run_id}' not found.")
        updated = self._recon_repo.update_run_state(
            run_id=run_id,
            target_status=ReconciliationRunStatus.CANCELLED,
            completed_at=utcnow_iso(),
        )
        self._audit_repo.record_event(
            entity_type="reconciliation_run",
            entity_id=run_id,
            action="reconciliation_run_cancelled",
            actor_id=actor_id,
            actor_role=actor_role,
            before_state={"status": run.status.value},
            after_state={"status": updated.status.value},
        )
        return updated

    def _evaluate_all_rules(
        self,
        run_id: str,
        rules_by_type: Dict[RuleType, ReconciliationRuleRead],
        orders: List[OrderRead],
        gateway_payments: List[PaymentRead],
        ledger_payments: List[PaymentRead],
        refunds: List[RefundRead],
        inventory_records: List[InventoryRecordRead],
        reference_dt: Optional[datetime],
    ) -> List[DiscrepancyCreate]:
        findings: List[DiscrepancyCreate] = []

        orders_by_id: Dict[str, OrderRead] = {o.order_id: o for o in orders}
        gw_by_order: Dict[str, List[PaymentRead]] = defaultdict(list)
        gw_by_txn: Dict[str, PaymentRead] = {}
        for p in gateway_payments:
            gw_by_order[p.order_id].append(p)
            gw_by_txn[p.transaction_id] = p

        ledger_by_charge_id: Dict[str, List[PaymentRead]] = defaultdict(list)
        ledger_by_order: Dict[str, List[PaymentRead]] = defaultdict(list)
        for lp in ledger_payments:
            ledger_by_charge_id[lp.provider_charge_id].append(lp)
            ledger_by_order[lp.order_id].append(lp)

        refunds_by_order: Dict[str, List[RefundRead]] = defaultdict(list)
        for rf in refunds:
            refunds_by_order[rf.order_id].append(rf)

        inv_by_order_sku: Dict[Tuple[str, str], List[InventoryRecordRead]] = defaultdict(list)
        for inv in inventory_records:
            if inv.order_id:
                inv_by_order_sku[(inv.order_id, inv.sku)].append(inv)

        # Rule 9: DUPLICATE_SOURCE_RECORD
        if RuleType.DUPLICATE_SOURCE_RECORD in rules_by_type:
            rule = rules_by_type[RuleType.DUPLICATE_SOURCE_RECORD]
            by_charge: Dict[str, List[PaymentRead]] = defaultdict(list)
            for p in gateway_payments:
                by_charge[p.provider_charge_id].append(p)
            for charge_id, group in sorted(by_charge.items()):
                if len(group) > 1:
                    txn_ids = [g.transaction_id for g in group]
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.CONFIRMED,
                            order_id=group[0].order_id,
                            transaction_id=group[0].transaction_id,
                            expected_value="1 record per provider_charge_id",
                            actual_value=f"{len(group)} records ({', '.join(txn_ids)})",
                            currency=group[0].currency,
                            description=(
                                f"Multiple internal payment gateway records ({', '.join(txn_ids)}) "
                                f"share the exact same provider_charge_id '{charge_id}' for order '{group[0].order_id}'."
                            ),
                            record_references={
                                "provider_charge_id": charge_id,
                                "transaction_ids": txn_ids,
                                "source_record_ids": [g.source_record_id for g in group],
                            },
                            evidence={
                                "duplicate_count": len(group),
                                "provider_charge_id": charge_id,
                            },
                        )
                    )

        # Rule 4: ORPHAN_PAYMENT
        if RuleType.ORPHAN_PAYMENT in rules_by_type:
            rule = rules_by_type[RuleType.ORPHAN_PAYMENT]
            for p in gateway_payments:
                if p.order_id not in orders_by_id:
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.CONFIRMED,
                            order_id=p.order_id,
                            transaction_id=p.transaction_id,
                            expected_value=f"Existing order '{p.order_id}'",
                            actual_value="Order not found in OMS",
                            currency=p.currency,
                            description=(
                                f"Payment '{p.transaction_id}' (provider_charge_id='{p.provider_charge_id}', "
                                f"captured={format_decimal_db(p.captured_amount)} {p.currency}) references "
                                f"order '{p.order_id}', which does not exist in the Order Management System."
                            ),
                            record_references={
                                "transaction_id": p.transaction_id,
                                "provider_charge_id": p.provider_charge_id,
                                "source_record_id": p.source_record_id,
                                "missing_order_id": p.order_id,
                            },
                            evidence={
                                "captured_amount": format_decimal_db(p.captured_amount),
                                "currency": p.currency,
                                "payment_status": p.status.value,
                            },
                        )
                    )

        active_capture_statuses = {
            PaymentStatus.CAPTURED,
            PaymentStatus.SETTLED,
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        }

        for order in orders:
            ord_payments = gw_by_order.get(order.order_id, [])
            captured_payments = [
                p for p in ord_payments if p.status in active_capture_statuses
            ]

            # Rule 1: MISSING_PAYMENT
            if RuleType.MISSING_PAYMENT in rules_by_type:
                rule = rules_by_type[RuleType.MISSING_PAYMENT]
                if order.status in (
                    OrderStatus.CONFIRMED,
                    OrderStatus.SHIPPED,
                    OrderStatus.DELIVERED,
                ) and not captured_payments:
                    auth_only = [
                        p for p in ord_payments if p.status == PaymentStatus.AUTHORIZED
                    ]
                    confidence = (
                        MatchConfidence.AMBIGUOUS
                        if auth_only
                        else MatchConfidence.CONFIRMED
                    )
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=confidence,
                            order_id=order.order_id,
                            expected_value=f"{format_decimal_db(order.expected_amount)} {order.currency}",
                            actual_value="0.0000 (no captured gateway payment)",
                            currency=order.currency,
                            description=(
                                f"Order '{order.order_id}' has status '{order.status.value}' and expects "
                                f"{format_decimal_db(order.expected_amount)} {order.currency}, but has no "
                                f"captured payment record in the payment gateway."
                            ),
                            record_references={
                                "order_id": order.order_id,
                                "order_source_record_id": order.source_record_id,
                                "authorized_transaction_ids": [
                                    p.transaction_id for p in auth_only
                                ],
                            },
                            evidence={
                                "order_status": order.status.value,
                                "expected_amount": format_decimal_db(order.expected_amount),
                                "gateway_record_count": len(ord_payments),
                            },
                        )
                    )

            unique_captures_by_charge: Dict[str, PaymentRead] = {}
            for cp in captured_payments:
                if cp.provider_charge_id not in unique_captures_by_charge:
                    unique_captures_by_charge[cp.provider_charge_id] = cp
            distinct_captures = list(unique_captures_by_charge.values())

            # Rule 3: MULTIPLE_CAPTURES
            if RuleType.MULTIPLE_CAPTURES in rules_by_type and len(distinct_captures) > 1:
                rule = rules_by_type[RuleType.MULTIPLE_CAPTURES]
                total_captured = sum(
                    (p.captured_amount for p in distinct_captures), Decimal("0.0000")
                )
                if total_captured > order.expected_amount + rule.tolerance_amount:
                    txn_ids = [p.transaction_id for p in distinct_captures]
                    charge_ids = [p.provider_charge_id for p in distinct_captures]
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.CONFIRMED,
                            order_id=order.order_id,
                            transaction_id=distinct_captures[0].transaction_id,
                            expected_value=f"1 capture totaling {format_decimal_db(order.expected_amount)} {order.currency}",
                            actual_value=(
                                f"{len(distinct_captures)} distinct captures totaling "
                                f"{format_decimal_db(total_captured)} {order.currency}"
                            ),
                            currency=order.currency,
                            description=(
                                f"Order '{order.order_id}' has {len(distinct_captures)} distinct captured "
                                f"provider charges ({', '.join(charge_ids)}) totaling "
                                f"{format_decimal_db(total_captured)} {order.currency} against expected "
                                f"{format_decimal_db(order.expected_amount)} {order.currency}."
                            ),
                            record_references={
                                "order_id": order.order_id,
                                "transaction_ids": txn_ids,
                                "provider_charge_ids": charge_ids,
                                "source_record_ids": [
                                    p.source_record_id for p in distinct_captures
                                ],
                            },
                            evidence={
                                "expected_amount": format_decimal_db(order.expected_amount),
                                "total_captured_amount": format_decimal_db(total_captured),
                                "distinct_capture_count": len(distinct_captures),
                            },
                        )
                    )

            # Rule 8: CURRENCY_MISMATCH
            if RuleType.CURRENCY_MISMATCH in rules_by_type:
                rule = rules_by_type[RuleType.CURRENCY_MISMATCH]
                for p in distinct_captures:
                    if p.currency.upper() != order.currency.upper():
                        findings.append(
                            DiscrepancyCreate(
                                run_id=run_id,
                                rule_id=rule.rule_id,
                                rule_code=rule.code,
                                discrepancy_type=rule.rule_type.value,
                                severity=rule.severity,
                                match_confidence=MatchConfidence.CONFIRMED,
                                order_id=order.order_id,
                                transaction_id=p.transaction_id,
                                expected_value=order.currency.upper(),
                                actual_value=p.currency.upper(),
                                currency=order.currency.upper(),
                                description=(
                                    f"Order '{order.order_id}' is denominated in '{order.currency}', "
                                    f"but payment '{p.transaction_id}' was captured in '{p.currency}'."
                                ),
                                record_references={
                                    "order_id": order.order_id,
                                    "order_source_record_id": order.source_record_id,
                                    "transaction_id": p.transaction_id,
                                    "payment_source_record_id": p.source_record_id,
                                },
                                evidence={
                                    "order_currency": order.currency,
                                    "payment_currency": p.currency,
                                    "captured_amount": format_decimal_db(p.captured_amount),
                                },
                            )
                        )

            # Rule 2b: AMOUNT_MISMATCH
            if RuleType.AMOUNT_MISMATCH in rules_by_type and len(distinct_captures) == 1:
                rule = rules_by_type[RuleType.AMOUNT_MISMATCH]
                p = distinct_captures[0]
                if p.currency.upper() == order.currency.upper():
                    diff = abs(order.expected_amount - p.captured_amount)
                    if diff > rule.tolerance_amount:
                        ambiguous_band = quantize_amount(
                            str(rule.config.get("ambiguous_band_amount", "0.0000"))
                        )
                        confidence = (
                            MatchConfidence.AMBIGUOUS
                            if ambiguous_band > Decimal("0.0000") and diff <= ambiguous_band
                            else MatchConfidence.CONFIRMED
                        )
                        findings.append(
                            DiscrepancyCreate(
                                run_id=run_id,
                                rule_id=rule.rule_id,
                                rule_code=rule.code,
                                discrepancy_type=rule.rule_type.value,
                                severity=rule.severity,
                                match_confidence=confidence,
                                order_id=order.order_id,
                                transaction_id=p.transaction_id,
                                expected_value=format_decimal_db(order.expected_amount),
                                actual_value=format_decimal_db(p.captured_amount),
                                currency=order.currency,
                                description=(
                                    f"Order '{order.order_id}' expected {format_decimal_db(order.expected_amount)} "
                                    f"{order.currency}, but gateway payment '{p.transaction_id}' captured "
                                    f"{format_decimal_db(p.captured_amount)} {p.currency} "
                                    f"(difference={format_decimal_db(diff)} {order.currency})."
                                ),
                                record_references={
                                    "order_id": order.order_id,
                                    "order_source_record_id": order.source_record_id,
                                    "transaction_id": p.transaction_id,
                                    "provider_charge_id": p.provider_charge_id,
                                    "payment_source_record_id": p.source_record_id,
                                },
                                evidence={
                                    "expected_amount": format_decimal_db(order.expected_amount),
                                    "captured_amount": format_decimal_db(p.captured_amount),
                                    "absolute_difference": format_decimal_db(diff),
                                    "tolerance_amount": format_decimal_db(rule.tolerance_amount),
                                },
                            )
                        )

            # Rule 2: MISSING_FINANCE_SETTLEMENT (Scenario DY1042)
            if RuleType.MISSING_FINANCE_SETTLEMENT in rules_by_type and len(distinct_captures) == 1:
                rule = rules_by_type[RuleType.MISSING_FINANCE_SETTLEMENT]
                p = distinct_captures[0]
                if (
                    p.currency.upper() == order.currency.upper()
                    and abs(order.expected_amount - p.captured_amount) <= rule.tolerance_amount
                ):
                    matching_ledger = ledger_by_charge_id.get(
                        p.provider_charge_id
                    ) or ledger_by_order.get(order.order_id)
                    if not matching_ledger:
                        findings.append(
                            DiscrepancyCreate(
                                run_id=run_id,
                                rule_id=rule.rule_id,
                                rule_code=rule.code,
                                discrepancy_type=rule.rule_type.value,
                                severity=rule.severity,
                                match_confidence=MatchConfidence.CONFIRMED,
                                order_id=order.order_id,
                                transaction_id=p.transaction_id,
                                expected_value=f"Settled finance ledger entry for {format_decimal_db(p.captured_amount)} {p.currency}",
                                actual_value="Missing in finance_ledger",
                                currency=p.currency,
                                description=(
                                    f"Order '{order.order_id}' has a successful gateway capture "
                                    f"'{p.transaction_id}' (provider_charge_id='{p.provider_charge_id}') for "
                                    f"{format_decimal_db(p.captured_amount)} {p.currency}, but the finance "
                                    f"settlement database has no corresponding payment entry."
                                ),
                                record_references={
                                    "order_id": order.order_id,
                                    "order_source_record_id": order.source_record_id,
                                    "transaction_id": p.transaction_id,
                                    "provider_charge_id": p.provider_charge_id,
                                    "gateway_source_record_id": p.source_record_id,
                                },
                                evidence={
                                    "expected_amount": format_decimal_db(order.expected_amount),
                                    "gateway_captured_amount": format_decimal_db(p.captured_amount),
                                    "finance_ledger_entries_found": 0,
                                },
                            )
                        )

            # Rule 5 & 6: MISSING_OR_MISMATCHED_REFUND and EXCESSIVE_REFUND
            ord_refunds = refunds_by_order.get(order.order_id, [])
            processed_refunds = [
                r for r in ord_refunds if r.status == RefundStatus.PROCESSED
            ]

            if RuleType.MISSING_OR_MISMATCHED_REFUND in rules_by_type:
                rule = rules_by_type[RuleType.MISSING_OR_MISMATCHED_REFUND]
                if order.status == OrderStatus.REFUNDED and not processed_refunds:
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.CONFIRMED,
                            order_id=order.order_id,
                            expected_value=f"Processed refund for order '{order.order_id}'",
                            actual_value="0 processed refund records",
                            currency=order.currency,
                            description=(
                                f"Order '{order.order_id}' is marked as 'refunded', but no processed "
                                f"refund record exists in the Refund Management System."
                            ),
                            record_references={
                                "order_id": order.order_id,
                                "order_source_record_id": order.source_record_id,
                            },
                            evidence={
                                "order_status": order.status.value,
                                "total_refund_records": len(ord_refunds),
                            },
                        )
                    )

            if RuleType.EXCESSIVE_REFUND in rules_by_type and processed_refunds:
                rule = rules_by_type[RuleType.EXCESSIVE_REFUND]
                total_captured = sum(
                    (p.captured_amount for p in distinct_captures), Decimal("0.0000")
                )
                total_refunded = sum(
                    (r.amount for r in processed_refunds), Decimal("0.0000")
                )
                if total_refunded > total_captured + rule.tolerance_amount:
                    excess = total_refunded - total_captured
                    ref_ids = [r.refund_id for r in processed_refunds]
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.CONFIRMED,
                            order_id=order.order_id,
                            transaction_id=processed_refunds[0].transaction_id,
                            refund_id=processed_refunds[0].refund_id,
                            expected_value=f"<= {format_decimal_db(total_captured)} {order.currency}",
                            actual_value=f"{format_decimal_db(total_refunded)} {order.currency}",
                            currency=order.currency,
                            description=(
                                f"Order '{order.order_id}' has cumulative processed refunds ({', '.join(ref_ids)}) "
                                f"totaling {format_decimal_db(total_refunded)} {order.currency}, exceeding "
                                f"the captured amount of {format_decimal_db(total_captured)} {order.currency} "
                                f"by {format_decimal_db(excess)} {order.currency}."
                            ),
                            record_references={
                                "order_id": order.order_id,
                                "refund_ids": ref_ids,
                                "refund_source_record_ids": [
                                    r.source_record_id for r in processed_refunds
                                ],
                                "transaction_id": processed_refunds[0].transaction_id,
                            },
                            evidence={
                                "captured_amount": format_decimal_db(total_captured),
                                "total_refunded_amount": format_decimal_db(total_refunded),
                                "excess_refund_amount": format_decimal_db(excess),
                            },
                        )
                    )

            # Rule 7: INVENTORY_INCONSISTENCY
            if RuleType.INVENTORY_INCONSISTENCY in rules_by_type and order.status in (
                OrderStatus.CONFIRMED,
                OrderStatus.SHIPPED,
                OrderStatus.DELIVERED,
            ):
                rule = rules_by_type[RuleType.INVENTORY_INCONSISTENCY]
                for item in order.line_items:
                    sku = str(item.get("sku") or "").strip()
                    expected_qty = int(item.get("quantity") or 0)
                    if not sku or expected_qty <= 0:
                        continue
                    inv_records = inv_by_order_sku.get((order.order_id, sku), [])
                    if inv_records:
                        deducted_units = sum(
                            abs(inv.delta_quantity)
                            for inv in inv_records
                            if inv.status == InventoryStatus.DEDUCTED and inv.delta_quantity < 0
                        )
                        if deducted_units != expected_qty:
                            findings.append(
                                DiscrepancyCreate(
                                    run_id=run_id,
                                    rule_id=rule.rule_id,
                                    rule_code=rule.code,
                                    discrepancy_type=rule.rule_type.value,
                                    severity=rule.severity,
                                    match_confidence=MatchConfidence.CONFIRMED,
                                    order_id=order.order_id,
                                    sku=sku,
                                    expected_value=f"-{expected_qty} units deducted",
                                    actual_value=f"-{deducted_units} units deducted",
                                    currency=order.currency,
                                    description=(
                                        f"Order '{order.order_id}' requires {expected_qty} unit(s) of SKU '{sku}', "
                                        f"but Inventory Management recorded only {deducted_units} unit(s) deducted."
                                    ),
                                    record_references={
                                        "order_id": order.order_id,
                                        "sku": sku,
                                        "inventory_record_ids": [
                                            inv.inventory_record_id for inv in inv_records
                                        ],
                                        "inventory_source_record_ids": [
                                            inv.source_record_id for inv in inv_records
                                        ],
                                    },
                                    evidence={
                                        "ordered_quantity": expected_qty,
                                        "deducted_quantity": deducted_units,
                                        "reserved_quantity": sum(
                                            inv.reserved_quantity for inv in inv_records
                                        ),
                                    },
                                )
                            )

            # Rule 10: STALE_EVENT
            if RuleType.STALE_EVENT in rules_by_type and reference_dt is not None:
                rule = rules_by_type[RuleType.STALE_EVENT]
                ord_dt = parse_iso_timestamp(order.updated_timestamp)
                if (
                    order.status == OrderStatus.PENDING
                    and ord_dt is not None
                    and (reference_dt - ord_dt).total_seconds() > rule.max_delay_seconds
                ):
                    age_sec = int((reference_dt - ord_dt).total_seconds())
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.AMBIGUOUS,
                            order_id=order.order_id,
                            expected_value=f"Processed within {rule.max_delay_seconds}s",
                            actual_value=f"Pending for {age_sec}s",
                            currency=order.currency,
                            description=(
                                f"Order '{order.order_id}' has remained in 'pending' status for "
                                f"{age_sec}s, exceeding the {rule.max_delay_seconds}s processing window."
                            ),
                            record_references={
                                "order_id": order.order_id,
                                "order_source_record_id": order.source_record_id,
                            },
                            evidence={
                                "age_seconds": age_sec,
                                "max_delay_seconds": rule.max_delay_seconds,
                                "updated_timestamp": order.updated_timestamp,
                            },
                        )
                    )

        if RuleType.MISSING_OR_MISMATCHED_REFUND in rules_by_type:
            rule = rules_by_type[RuleType.MISSING_OR_MISMATCHED_REFUND]
            for rf in refunds:
                if rf.transaction_id not in gw_by_txn:
                    findings.append(
                        DiscrepancyCreate(
                            run_id=run_id,
                            rule_id=rule.rule_id,
                            rule_code=rule.code,
                            discrepancy_type=rule.rule_type.value,
                            severity=rule.severity,
                            match_confidence=MatchConfidence.CONFIRMED,
                            order_id=rf.order_id,
                            transaction_id=rf.transaction_id,
                            refund_id=rf.refund_id,
                            expected_value=f"Existing gateway transaction '{rf.transaction_id}'",
                            actual_value="Transaction not found in payment_gateway",
                            currency=rf.currency,
                            description=(
                                f"Refund '{rf.refund_id}' references transaction_id '{rf.transaction_id}', "
                                f"which does not exist in the payment gateway."
                            ),
                            record_references={
                                "refund_id": rf.refund_id,
                                "refund_source_record_id": rf.source_record_id,
                                "missing_transaction_id": rf.transaction_id,
                            },
                            evidence={
                                "refund_amount": format_decimal_db(rf.amount),
                                "refund_status": rf.status.value,
                            },
                        )
                    )

        return findings

    def _load_all_orders(self) -> List[OrderRead]:
        result: List[OrderRead] = []
        offset = 0
        while True:
            batch = self._records_repo.list_orders(limit=self._batch_size, offset=offset)
            if not batch:
                break
            result.extend(batch)
            if len(batch) < self._batch_size:
                break
            offset += self._batch_size
        return result

    def _load_payments_by_system(self, source_system: str) -> List[PaymentRead]:
        result: List[PaymentRead] = []
        offset = 0
        while True:
            batch = self._records_repo.list_payments(
                source_system=source_system, limit=self._batch_size, offset=offset
            )
            if not batch:
                break
            result.extend(batch)
            if len(batch) < self._batch_size:
                break
            offset += self._batch_size
        return result

    def _load_all_refunds(self) -> List[RefundRead]:
        result: List[RefundRead] = []
        offset = 0
        while True:
            batch = self._records_repo.list_refunds(limit=self._batch_size, offset=offset)
            if not batch:
                break
            result.extend(batch)
            if len(batch) < self._batch_size:
                break
            offset += self._batch_size
        return result

    def _load_all_inventory(self) -> List[InventoryRecordRead]:
        result: List[InventoryRecordRead] = []
        offset = 0
        while True:
            batch = self._records_repo.list_inventory_records(
                limit=self._batch_size, offset=offset
            )
            if not batch:
                break
            result.extend(batch)
            if len(batch) < self._batch_size:
                break
            offset += self._batch_size
        return result

    @staticmethod
    def _compute_input_snapshot(
        orders: List[OrderRead],
        gateway_payments: List[PaymentRead],
        ledger_payments: List[PaymentRead],
        refunds: List[RefundRead],
        inventory_records: List[InventoryRecordRead],
        active_rules: List[ReconciliationRuleRead],
    ) -> Dict[str, Any]:
        """Compute a reproducible SHA-256 snapshot digest of all input records and active rules."""
        canonical_payload = {
            "orders": [
                (
                    o.order_id,
                    o.source_record_id,
                    format_decimal_db(o.expected_amount),
                    o.currency,
                    o.status.value,
                )
                for o in sorted(orders, key=lambda x: x.order_id)
            ],
            "gateway_payments": [
                (
                    p.transaction_id,
                    p.provider_charge_id,
                    p.order_id,
                    format_decimal_db(p.captured_amount),
                    p.currency,
                    p.status.value,
                )
                for p in sorted(gateway_payments, key=lambda x: x.transaction_id)
            ],
            "ledger_payments": [
                (
                    p.transaction_id,
                    p.provider_charge_id,
                    p.order_id,
                    format_decimal_db(p.captured_amount),
                    p.currency,
                    p.status.value,
                )
                for p in sorted(ledger_payments, key=lambda x: x.transaction_id)
            ],
            "refunds": [
                (
                    r.refund_id,
                    r.transaction_id,
                    r.order_id,
                    format_decimal_db(r.amount),
                    r.currency,
                    r.status.value,
                )
                for r in sorted(refunds, key=lambda x: x.refund_id)
            ],
            "inventory": [
                (
                    i.inventory_record_id,
                    i.sku,
                    i.order_id or "",
                    i.delta_quantity,
                    i.status.value,
                )
                for i in sorted(inventory_records, key=lambda x: x.inventory_record_id)
            ],
            "rules": [
                (
                    rl.code,
                    rl.rule_type.value,
                    format_decimal_db(rl.tolerance_amount),
                    rl.max_delay_seconds,
                )
                for rl in sorted(active_rules, key=lambda x: x.code)
            ],
        }
        digest = hashlib.sha256(
            json.dumps(canonical_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "snapshot_sha256": digest,
            "order_ids": [o.order_id for o in sorted(orders, key=lambda x: x.order_id)],
            "active_rule_codes": [rl.code for rl in sorted(active_rules, key=lambda x: x.code)],
        }
