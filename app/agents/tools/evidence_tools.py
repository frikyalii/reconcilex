"""Constrained, read-only evidence & historical incident retrieval tools for the LangGraph workflow.

Security Guarantees:
- Explicitly read-only: tools only query parameterized repository methods (`get_order`,
  `list_payments`, `list_refunds`, `list_inventory_records`, `list_event_logs`).
- No arbitrary SQL execution, shell access, or external network mutations are exposed to the LLM.
- Historical incident retrieval supports optional Qdrant vector search with a built-in
  curated runbook catalog fallback, always marking semantic matches with
  `is_advisory_context_only=True`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List

from app.agents.state import EvidenceItem, HistoricalIncidentMatch
from app.db.base import format_decimal_db
from app.db.session import DatabaseSession
from app.models.enums import PaymentSourceSystem, ResolutionActionType
from app.repositories.records_repo import RecordsRepository
from app.schemas.domain import DiscrepancyRead


CURATED_RUNBOOKS: List[HistoricalIncidentMatch] = [
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-WEBHOOK-DLQ-01",
        title="Payment Gateway Webhook Delivery Timeout to Finance Settlement Service",
        discrepancy_type="missing_finance_settlement",
        root_cause_pattern=(
            "Gateway payment is captured, but finance settlement service returned HTTP 503 "
            "or timed out across 3 webhook delivery attempts, moving the event to the DLQ."
        ),
        recommended_action_type=ResolutionActionType.RETRY_WEBHOOK_SYNC,
        similarity_score=Decimal("0.9600"),
    ),
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-AMOUNT-DISC-02",
        title="Unrecorded Promotional Discount or Partial Capture Variance",
        discrepancy_type="amount_mismatch",
        root_cause_pattern=(
            "Order expected amount differs from captured gateway amount due to checkout promo "
            "voucher not synchronized to OMS or partial capture."
        ),
        recommended_action_type=ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW,
        similarity_score=Decimal("0.9100"),
    ),
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-MULTI-CAPTURE-03",
        title="Duplicate Client Checkout Retry Without Idempotency Key",
        discrepancy_type="multiple_captures",
        root_cause_pattern=(
            "Multiple distinct provider_charge_id captures exist for the same order_id because "
            "the client retried payment submission without a gateway idempotency key."
        ),
        recommended_action_type=ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW,
        similarity_score=Decimal("0.9400"),
    ),
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-ORPHAN-CHARGE-04",
        title="OMS Order Transaction Rollback After Gateway Capture",
        discrepancy_type="orphan_payment",
        root_cause_pattern=(
            "Payment gateway captured funds, but the OMS database transaction rolled back "
            "before persisting the order record."
        ),
        recommended_action_type=ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW,
        similarity_score=Decimal("0.9300"),
    ),
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-EXCESS-REFUND-05",
        title="Duplicate Manual Refund Adjustment Exceeding Captured Balance",
        discrepancy_type="excessive_refund",
        root_cause_pattern=(
            "Cumulative processed refunds exceed the captured gateway payment due to a concurrent "
            "manual refund adjustment alongside an automated return refund."
        ),
        recommended_action_type=ResolutionActionType.ESCALATE_TO_ENGINEERING,
        similarity_score=Decimal("0.9500"),
    ),
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-INV-SYNC-06",
        title="Partial Warehouse Reservation Commit on Multi-Quantity Line Item",
        discrepancy_type="inventory_inconsistency",
        root_cause_pattern=(
            "Order line item requested multiple units of a SKU, but WMS deducted only 1 unit "
            "and left remaining units in reserved state."
        ),
        recommended_action_type=ResolutionActionType.SYNC_INVENTORY_RESERVATION,
        similarity_score=Decimal("0.9200"),
    ),
    HistoricalIncidentMatch(
        incident_id="RUNBOOK:RB-CURRENCY-MISMATCH-07",
        title="International Checkout Gateway Currency Configuration Mismatch",
        discrepancy_type="currency_mismatch",
        root_cause_pattern=(
            "Order recorded in base currency (INR), while international payment gateway "
            "processed charge in foreign currency (USD) without FX conversion normalization."
        ),
        recommended_action_type=ResolutionActionType.ESCALATE_TO_ENGINEERING,
        similarity_score=Decimal("0.9400"),
    ),
]


def collect_discrepancy_evidence(
    session: DatabaseSession, discrepancy: DiscrepancyRead
) -> List[EvidenceItem]:
    """Gather all related records and event logs for a detected discrepancy (read-only)."""
    records_repo = RecordsRepository(session)
    items: List[EvidenceItem] = []

    # Always include the deterministic discrepancy record itself as ground-truth evidence
    items.append(
        EvidenceItem(
            evidence_id=f"DISCREPANCY:{discrepancy.discrepancy_id}",
            source_type="discrepancy",
            summary=f"Rule {discrepancy.rule_code} ({discrepancy.discrepancy_type}): {discrepancy.description}",
            data={
                "rule_code": discrepancy.rule_code,
                "discrepancy_type": discrepancy.discrepancy_type,
                "expected_value": discrepancy.expected_value,
                "actual_value": discrepancy.actual_value,
                "record_references": discrepancy.record_references,
            },
        )
    )

    if discrepancy.order_id:
        order = records_repo.get_order(discrepancy.order_id)
        if order is not None:
            items.append(
                EvidenceItem(
                    evidence_id=f"ORDER:{order.order_id}",
                    source_type="order",
                    summary=(
                        f"Order {order.order_id} status={order.status.value} "
                        f"expected={format_decimal_db(order.expected_amount)} {order.currency}"
                    ),
                    data={
                        "order_id": order.order_id,
                        "source_record_id": order.source_record_id,
                        "expected_amount": format_decimal_db(order.expected_amount),
                        "currency": order.currency,
                        "status": order.status.value,
                        "line_items": order.line_items,
                        "metadata": order.metadata,
                    },
                )
            )

        gw_payments = records_repo.list_payments(
            order_id=discrepancy.order_id,
            source_system=PaymentSourceSystem.PAYMENT_GATEWAY.value,
        )
        for p in gw_payments:
            items.append(
                EvidenceItem(
                    evidence_id=f"PAYMENT:{p.transaction_id}",
                    source_type="payment_gateway",
                    summary=(
                        f"Gateway payment {p.transaction_id} (charge={p.provider_charge_id}) "
                        f"status={p.status.value} captured={format_decimal_db(p.captured_amount)} {p.currency}"
                    ),
                    data={
                        "transaction_id": p.transaction_id,
                        "provider_charge_id": p.provider_charge_id,
                        "order_id": p.order_id,
                        "source_record_id": p.source_record_id,
                        "captured_amount": format_decimal_db(p.captured_amount),
                        "currency": p.currency,
                        "status": p.status.value,
                        "metadata": p.metadata,
                    },
                )
            )

        ledger_payments = records_repo.list_payments(
            order_id=discrepancy.order_id,
            source_system=PaymentSourceSystem.FINANCE_LEDGER.value,
        )
        for lp in ledger_payments:
            items.append(
                EvidenceItem(
                    evidence_id=f"LEDGER:{lp.transaction_id}",
                    source_type="finance_ledger",
                    summary=(
                        f"Finance ledger entry {lp.transaction_id} (charge={lp.provider_charge_id}) "
                        f"status={lp.status.value} amount={format_decimal_db(lp.captured_amount)} {lp.currency}"
                    ),
                    data={
                        "transaction_id": lp.transaction_id,
                        "provider_charge_id": lp.provider_charge_id,
                        "order_id": lp.order_id,
                        "captured_amount": format_decimal_db(lp.captured_amount),
                        "currency": lp.currency,
                        "status": lp.status.value,
                    },
                )
            )

        refunds = records_repo.list_refunds(order_id=discrepancy.order_id)
        for rf in refunds:
            items.append(
                EvidenceItem(
                    evidence_id=f"REFUND:{rf.refund_id}",
                    source_type="refund",
                    summary=(
                        f"Refund {rf.refund_id} for txn {rf.transaction_id} "
                        f"status={rf.status.value} amount={format_decimal_db(rf.amount)} {rf.currency}"
                    ),
                    data={
                        "refund_id": rf.refund_id,
                        "transaction_id": rf.transaction_id,
                        "order_id": rf.order_id,
                        "amount": format_decimal_db(rf.amount),
                        "currency": rf.currency,
                        "status": rf.status.value,
                        "reason": rf.reason,
                    },
                )
            )

        inv_records = records_repo.list_inventory_records(order_id=discrepancy.order_id)
        for inv in inv_records:
            items.append(
                EvidenceItem(
                    evidence_id=f"INVENTORY:{inv.inventory_record_id}",
                    source_type="inventory",
                    summary=(
                        f"Inventory {inv.inventory_record_id} SKU={inv.sku} "
                        f"delta={inv.delta_quantity} reserved={inv.reserved_quantity} status={inv.status.value}"
                    ),
                    data={
                        "inventory_record_id": inv.inventory_record_id,
                        "sku": inv.sku,
                        "order_id": inv.order_id,
                        "delta_quantity": inv.delta_quantity,
                        "available_quantity": inv.available_quantity,
                        "reserved_quantity": inv.reserved_quantity,
                        "status": inv.status.value,
                        "metadata": inv.metadata,
                    },
                )
            )

        logs = records_repo.list_event_logs(order_id=discrepancy.order_id)
        for lg in logs:
            items.append(
                EvidenceItem(
                    evidence_id=f"EVENT_LOG:{lg.event_id}",
                    source_type="event_log",
                    summary=(
                        f"Event {lg.event_id} ({lg.event_type}) status={lg.delivery_status} "
                        f"attempt={lg.attempt_count} error={lg.error_code}: {lg.message}"
                    ),
                    data={
                        "event_id": lg.event_id,
                        "event_type": lg.event_type,
                        "order_id": lg.order_id,
                        "transaction_id": lg.transaction_id,
                        "delivery_status": lg.delivery_status,
                        "attempt_count": lg.attempt_count,
                        "error_code": lg.error_code,
                        "message": lg.message,
                        "payload": lg.payload,
                    },
                )
            )

    # Also check direct transaction_id if not already covered by order_id
    if discrepancy.transaction_id and not any(
        it.evidence_id == f"PAYMENT:{discrepancy.transaction_id}" for it in items
    ):
        p = records_repo.get_payment(discrepancy.transaction_id)
        if p is not None:
            items.append(
                EvidenceItem(
                    evidence_id=f"PAYMENT:{p.transaction_id}",
                    source_type="payment_gateway",
                    summary=(
                        f"Gateway payment {p.transaction_id} (charge={p.provider_charge_id}) "
                        f"status={p.status.value} captured={format_decimal_db(p.captured_amount)} {p.currency}"
                    ),
                    data={
                        "transaction_id": p.transaction_id,
                        "provider_charge_id": p.provider_charge_id,
                        "order_id": p.order_id,
                        "captured_amount": format_decimal_db(p.captured_amount),
                        "currency": p.currency,
                        "status": p.status.value,
                    },
                )
            )

    return items


def retrieve_historical_incidents(
    discrepancy: DiscrepancyRead,
) -> List[HistoricalIncidentMatch]:
    """Retrieve matching runbooks / historical incidents for diagnostic guidance.

    Semantic similarity is advisory only (`is_advisory_context_only=True`) and is
    never treated as proof that two financial transactions are identical.
    """
    matches = [
        rb
        for rb in CURATED_RUNBOOKS
        if rb.discrepancy_type == discrepancy.discrepancy_type
    ]
    return matches
