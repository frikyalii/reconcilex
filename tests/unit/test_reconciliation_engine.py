"""Unit tests for Phase 3: Deterministic Reconciliation Engine.

Verifies all mandatory Section 8 deterministic engine test cases:
1. Exact match produces no finding (DY1001).
2. Missing payment is detected (DY1048 -> RULE_MISSING_PAYMENT).
3. Missing finance settlement ledger entry is detected (DY1042 -> RULE_MISSING_FINANCE_SETTLEMENT).
4. Amount mismatch is detected (DY1043 -> RULE_AMOUNT_MISMATCH).
5. Orphan payment is detected (DY9999_ORPHAN -> RULE_ORPHAN_PAYMENT).
6. Duplicate provider_charge_id records are flagged as duplicate source records, NOT multiple real captures.
7. Multiple distinct captured provider charges are flagged under RULE_MULTIPLE_CAPTURES (DY1044).
8. Currency mismatch is detected (DY1046 -> RULE_CURRENCY_MISMATCH).
9. Partial refund within captured balance produces no excessive refund finding.
10. Refund greater than refundable amount is flagged under RULE_EXCESSIVE_REFUND (DY1045).
11. Inventory quantity inconsistency is detected (DY1047 -> RULE_INVENTORY_INCONSISTENCY).
12. Stale pending order exceeding max_delay_seconds is flagged under RULE_STALE_EVENT.
13. Re-running the same immutable input produces equivalent findings and identical snapshot_sha256.
"""

from __future__ import annotations

from decimal import Decimal
import unittest

from app.core.config import Settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.models.enums import (
    InventoryStatus,
    MatchConfidence,
    OrderStatus,
    PaymentSourceSystem,
    PaymentStatus,
    ReconciliationRunStatus,
    RefundStatus,
)
from app.repositories.records_repo import RecordsRepository
from app.schemas.domain import (
    InventoryRecordCreate,
    OrderCreate,
    PaymentCreate,
    RefundCreate,
)
from app.services.reconciliation.engine import ReconciliationEngine
from scripts.seed_synthetic import seed_all_synthetic_data


class TestDeterministicReconciliationEngine(unittest.TestCase):
    """Test all deterministic reconciliation rules and edge cases."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.settings = Settings(DATABASE_URL="sqlite:///:memory:")
        self.session = create_session_for_settings(self.settings)
        self.records_repo = RecordsRepository(self.session)
        self.engine = ReconciliationEngine(self.session)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_exact_match_and_partial_refund_produce_zero_findings(self) -> None:
        """A clean order with exact gateway capture, finance settlement, inventory deduction, and valid partial refund produces 0 discrepancies."""
        self.records_repo.create_order(
            OrderCreate(
                order_id="DY_CLEAN_01",
                source_record_id="OMS-CLEAN-01",
                expected_amount=Decimal("50000.0000"),
                currency="INR",
                status=OrderStatus.CONFIRMED,
                line_items=[{"sku": "SKU-SILK-01", "quantity": 2}],
                event_timestamp="2026-10-24T01:00:00Z",
                updated_timestamp="2026-10-24T01:00:00Z",
            )
        )
        self.records_repo.create_payment(
            PaymentCreate(
                transaction_id="TXN_CLEAN_01",
                provider_charge_id="CHG_CLEAN_01",
                order_id="DY_CLEAN_01",
                source_record_id="PG-CLEAN-01",
                source_system=PaymentSourceSystem.PAYMENT_GATEWAY,
                amount=Decimal("50000.0000"),
                captured_amount=Decimal("50000.0000"),
                currency="INR",
                status=PaymentStatus.PARTIALLY_REFUNDED,
                event_timestamp="2026-10-24T01:01:00Z",
                updated_timestamp="2026-10-24T01:01:00Z",
            )
        )
        self.records_repo.create_payment(
            PaymentCreate(
                transaction_id="FIN_CLEAN_01",
                provider_charge_id="CHG_CLEAN_01",
                order_id="DY_CLEAN_01",
                source_record_id="LED-CLEAN-01",
                source_system=PaymentSourceSystem.FINANCE_LEDGER,
                amount=Decimal("50000.0000"),
                captured_amount=Decimal("50000.0000"),
                currency="INR",
                status=PaymentStatus.SETTLED,
                event_timestamp="2026-10-24T01:02:00Z",
                updated_timestamp="2026-10-24T01:02:00Z",
            )
        )
        # Valid partial refund of INR 15,000 against INR 50,000 captured
        self.records_repo.create_refund(
            RefundCreate(
                refund_id="REF_PARTIAL_01",
                transaction_id="TXN_CLEAN_01",
                order_id="DY_CLEAN_01",
                source_record_id="RFM-PARTIAL-01",
                amount=Decimal("15000.0000"),
                currency="INR",
                status=RefundStatus.PROCESSED,
                event_timestamp="2026-10-24T01:30:00Z",
                updated_timestamp="2026-10-24T01:30:00Z",
            )
        )
        self.records_repo.create_inventory_record(
            InventoryRecordCreate(
                inventory_record_id="INV_CLEAN_01",
                sku="SKU-SILK-01",
                order_id="DY_CLEAN_01",
                source_record_id="WMS-CLEAN-01",
                delta_quantity=-2,
                available_quantity=10,
                reserved_quantity=0,
                status=InventoryStatus.DEDUCTED,
                event_timestamp="2026-10-24T01:02:00Z",
                updated_timestamp="2026-10-24T01:02:00Z",
            )
        )

        run, findings = self.engine.execute_run(triggered_by="unit_test")
        self.assertEqual(run.status, ReconciliationRunStatus.COMPLETED)
        self.assertEqual(run.discrepancy_count, 0)
        self.assertEqual(len(findings), 0)

    def test_duplicate_provider_charge_id_vs_multiple_real_captures(self) -> None:
        """Two internal gateway records sharing the same provider_charge_id must trigger RULE_DUPLICATE_SOURCE_RECORD and NOT RULE_MULTIPLE_CAPTURES."""
        self.records_repo.create_order(
            OrderCreate(
                order_id="DY_DUP_INTERNAL",
                source_record_id="OMS-DUP-1",
                expected_amount=Decimal("40000.0000"),
                currency="INR",
                status=OrderStatus.CONFIRMED,
                event_timestamp="2026-10-24T01:00:00Z",
                updated_timestamp="2026-10-24T01:00:00Z",
            )
        )
        # Two internal rows with the SAME provider_charge_id 'CHG_SAME_99'
        self.records_repo.create_payment(
            PaymentCreate(
                transaction_id="TXN_DUP_A",
                provider_charge_id="CHG_SAME_99",
                order_id="DY_DUP_INTERNAL",
                source_record_id="PG-DUP-A",
                source_system=PaymentSourceSystem.PAYMENT_GATEWAY,
                amount=Decimal("40000.0000"),
                captured_amount=Decimal("40000.0000"),
                currency="INR",
                status=PaymentStatus.CAPTURED,
                event_timestamp="2026-10-24T01:01:00Z",
                updated_timestamp="2026-10-24T01:01:00Z",
            )
        )
        self.records_repo.create_payment(
            PaymentCreate(
                transaction_id="TXN_DUP_B",
                provider_charge_id="CHG_SAME_99",
                order_id="DY_DUP_INTERNAL",
                source_record_id="PG-DUP-B",
                source_system=PaymentSourceSystem.PAYMENT_GATEWAY,
                amount=Decimal("40000.0000"),
                captured_amount=Decimal("40000.0000"),
                currency="INR",
                status=PaymentStatus.CAPTURED,
                event_timestamp="2026-10-24T01:01:02Z",
                updated_timestamp="2026-10-24T01:01:02Z",
            )
        )
        self.records_repo.create_payment(
            PaymentCreate(
                transaction_id="FIN_DUP_1",
                provider_charge_id="CHG_SAME_99",
                order_id="DY_DUP_INTERNAL",
                source_record_id="LED-DUP-1",
                source_system=PaymentSourceSystem.FINANCE_LEDGER,
                amount=Decimal("40000.0000"),
                captured_amount=Decimal("40000.0000"),
                currency="INR",
                status=PaymentStatus.SETTLED,
                event_timestamp="2026-10-24T01:02:00Z",
                updated_timestamp="2026-10-24T01:02:00Z",
            )
        )

        _, findings = self.engine.execute_run(triggered_by="unit_test")
        codes = [f.rule_code for f in findings]
        self.assertIn("RULE_DUPLICATE_SOURCE_RECORD", codes)
        self.assertNotIn("RULE_MULTIPLE_CAPTURES", codes)

    def test_full_synthetic_doyuf_suite_and_reproducibility(self) -> None:
        """Verify all synthetic discrepancies are detected and re-running produces equivalent findings."""
        seed_all_synthetic_data(self.session)

        # Also add a stale pending order to test RULE_STALE_EVENT
        self.records_repo.create_order(
            OrderCreate(
                order_id="DY1049_STALE",
                source_record_id="OMS-ORD-1049",
                expected_amount=Decimal("32000.0000"),
                currency="INR",
                status=OrderStatus.PENDING,
                event_timestamp="2026-10-24T00:00:00Z",
                updated_timestamp="2026-10-24T00:00:00Z",
            )
        )

        run1, findings1 = self.engine.execute_run(
            triggered_by="unit_test_run1",
            reference_timestamp="2026-10-24T05:00:00Z",
        )
        self.assertEqual(
            run1.status, ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES
        )

        by_rule_order = {(f.rule_code, f.order_id): f for f in findings1}

        # 1. Clean order DY1001 must NOT appear in any discrepancy
        dy1001_findings = [f for f in findings1 if f.order_id == "DY1001"]
        self.assertEqual(len(dy1001_findings), 0)

        # 2. Primary scenario DY1042 -> RULE_MISSING_FINANCE_SETTLEMENT
        self.assertIn(("RULE_MISSING_FINANCE_SETTLEMENT", "DY1042"), by_rule_order)
        dy1042_disc = by_rule_order[("RULE_MISSING_FINANCE_SETTLEMENT", "DY1042")]
        self.assertEqual(dy1042_disc.transaction_id, "TXN9001")
        self.assertEqual(dy1042_disc.match_confidence, MatchConfidence.CONFIRMED)
        self.assertEqual(
            dy1042_disc.record_references["provider_charge_id"], "CHG_RZ_9001"
        )

        # 3. Amount mismatch DY1043 -> RULE_AMOUNT_MISMATCH
        self.assertIn(("RULE_AMOUNT_MISMATCH", "DY1043"), by_rule_order)
        self.assertEqual(
            by_rule_order[("RULE_AMOUNT_MISMATCH", "DY1043")].evidence[
                "absolute_difference"
            ],
            "5000.0000",
        )

        # 4. Multiple distinct captures DY1044 -> RULE_MULTIPLE_CAPTURES
        self.assertIn(("RULE_MULTIPLE_CAPTURES", "DY1044"), by_rule_order)

        # 5. Orphan payment DY9999_ORPHAN -> RULE_ORPHAN_PAYMENT
        self.assertIn(("RULE_ORPHAN_PAYMENT", "DY9999_ORPHAN"), by_rule_order)

        # 6. Excessive refund DY1045 -> RULE_EXCESSIVE_REFUND
        self.assertIn(("RULE_EXCESSIVE_REFUND", "DY1045"), by_rule_order)
        self.assertEqual(
            by_rule_order[("RULE_EXCESSIVE_REFUND", "DY1045")].evidence[
                "excess_refund_amount"
            ],
            "10000.0000",
        )

        # 7. Currency mismatch DY1046 -> RULE_CURRENCY_MISMATCH
        self.assertIn(("RULE_CURRENCY_MISMATCH", "DY1046"), by_rule_order)

        # 8. Inventory inconsistency DY1047 -> RULE_INVENTORY_INCONSISTENCY
        self.assertIn(("RULE_INVENTORY_INCONSISTENCY", "DY1047"), by_rule_order)

        # 9. Missing payment DY1048 -> RULE_MISSING_PAYMENT
        self.assertIn(("RULE_MISSING_PAYMENT", "DY1048"), by_rule_order)

        # 10. Stale pending order DY1049_STALE -> RULE_STALE_EVENT (ambiguous confidence)
        self.assertIn(("RULE_STALE_EVENT", "DY1049_STALE"), by_rule_order)
        self.assertEqual(
            by_rule_order[("RULE_STALE_EVENT", "DY1049_STALE")].match_confidence,
            MatchConfidence.AMBIGUOUS,
        )

        # 11. Re-running on the same immutable input produces identical snapshot_sha256 and equivalent findings
        run2, findings2 = self.engine.execute_run(
            triggered_by="unit_test_run2",
            reference_timestamp="2026-10-24T05:00:00Z",
        )
        self.assertNotEqual(run1.run_id, run2.run_id)
        self.assertEqual(
            run1.source_snapshot["snapshot_sha256"],
            run2.source_snapshot["snapshot_sha256"],
        )
        self.assertEqual(len(findings1), len(findings2))
        for f1, f2 in zip(findings1, findings2):
            self.assertEqual(f1.rule_code, f2.rule_code)
            self.assertEqual(f1.order_id, f2.order_id)
            self.assertEqual(f1.transaction_id, f2.transaction_id)
            self.assertEqual(f1.expected_value, f2.expected_value)
            self.assertEqual(f1.actual_value, f2.actual_value)
            self.assertEqual(f1.description, f2.description)
            self.assertEqual(f1.record_references, f2.record_references)


if __name__ == "__main__":
    unittest.main()
