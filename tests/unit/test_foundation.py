"""Unit tests for Phase 1 foundation: config, logging redaction, Decimal safety, state transitions, and DB schema."""

from __future__ import annotations

import json
import logging
import unittest
from decimal import Decimal

from app.core.config import Settings
from app.core.logging import (
    StructuredJsonFormatter,
    get_correlation_id,
    redact_log_message,
    redact_sensitive_value,
    set_correlation_id,
)
from app.db.base import format_decimal_db, quantize_amount
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.models.entities import Order, Payment, Refund
from app.models.enums import (
    OrderStatus,
    PaymentSourceSystem,
    PaymentStatus,
    ReconciliationRunStatus,
    RefundStatus,
    ResolutionStatus,
    validate_resolution_transition,
    validate_run_transition,
)
from app.schemas.domain import OrderCreate, PaymentCreate


class TestFoundationUnit(unittest.TestCase):
    """Verify core invariants established in Phase 1."""

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_decimal_quantization_rejects_binary_float(self) -> None:
        """Financial calculations and schemas must strictly reject binary floats."""
        with self.assertRaises(TypeError):
            quantize_amount(80000.00)  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            OrderCreate(
                order_id="DY1042",
                source_record_id="SRC-ORD-1042",
                expected_amount=80000.55,  # type: ignore[arg-type]
                currency="INR",
                status=OrderStatus.CONFIRMED,
                event_timestamp="2026-10-24T00:00:00Z",
                updated_timestamp="2026-10-24T00:00:00Z",
            )

        exact = quantize_amount("80000")
        self.assertEqual(exact, Decimal("80000.0000"))
        self.assertEqual(format_decimal_db(Decimal("80000.125")), "80000.1250")

    def test_currency_normalization_and_entity_creation(self) -> None:
        """Entities normalize currency codes and quantize monetary fields."""
        order = Order(
            order_id="DY1042",
            source_record_id="OMS-1042",
            expected_amount=Decimal("80000"),
            currency=" inr ",
            status=OrderStatus.CONFIRMED,
            event_timestamp="2026-10-24T01:00:00Z",
            updated_timestamp="2026-10-24T01:00:00Z",
        )
        self.assertEqual(order.currency, "INR")
        self.assertEqual(order.expected_amount, Decimal("80000.0000"))

        payment = Payment(
            transaction_id="TXN9001",
            provider_charge_id="CHG_9001",
            order_id="DY1042",
            source_record_id="PG-9001",
            amount=Decimal("80000"),
            captured_amount=Decimal("80000"),
            currency="inr",
            status=PaymentStatus.CAPTURED,
            source_system=PaymentSourceSystem.PAYMENT_GATEWAY,
            event_timestamp="2026-10-24T01:01:00Z",
            updated_timestamp="2026-10-24T01:01:00Z",
        )
        self.assertEqual(payment.captured_amount, Decimal("80000.0000"))

        refund = Refund(
            refund_id="REF5001",
            transaction_id="TXN9001",
            order_id="DY1042",
            source_record_id="RM-5001",
            amount=Decimal("15000.50"),
            currency="inr",
            status=RefundStatus.PROCESSED,
            event_timestamp="2026-10-24T02:00:00Z",
            updated_timestamp="2026-10-24T02:00:00Z",
        )
        self.assertEqual(refund.amount, Decimal("15000.5000"))

    def test_orphan_payment_representable_in_database(self) -> None:
        """Payments referencing nonexistent orders must be persistable for orphan detection."""
        settings = Settings(DATABASE_URL="sqlite:///:memory:")
        session = create_session_for_settings(settings)

        # Insert a payment for order DY9999 which does NOT exist in `orders`
        session.execute(
            """
            INSERT INTO payments (
                id, transaction_id, provider_charge_id, order_id, source_record_id,
                source_system, source_id, amount, captured_amount, currency, status,
                event_timestamp, updated_timestamp, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pay-uuid-1",
                "TXN_ORPHAN_01",
                "CHG_ORPHAN_01",
                "DY9999_NONEXISTENT",
                "PG-ORPHAN-1",
                "payment_gateway",
                None,
                "45000.0000",
                "45000.0000",
                "INR",
                "captured",
                "2026-10-24T01:00:00Z",
                "2026-10-24T01:00:00Z",
                "{}",
                "2026-10-24T01:00:00Z",
                "2026-10-24T01:00:00Z",
            ),
        )
        session.commit()

        row = session.fetchone(
            "SELECT transaction_id, order_id, captured_amount FROM payments WHERE transaction_id = ?",
            ("TXN_ORPHAN_01",),
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["order_id"], "DY9999_NONEXISTENT")
        self.assertEqual(Decimal(row["captured_amount"]), Decimal("45000.0000"))

    def test_state_transition_guards(self) -> None:
        """Verify explicit state transition machines for runs and resolution actions."""
        self.assertTrue(
            validate_run_transition(
                ReconciliationRunStatus.PENDING, ReconciliationRunStatus.RUNNING
            )
        )
        self.assertTrue(
            validate_run_transition(
                ReconciliationRunStatus.RUNNING,
                ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES,
            )
        )
        self.assertFalse(
            validate_run_transition(
                ReconciliationRunStatus.COMPLETED, ReconciliationRunStatus.RUNNING
            )
        )

        # Resolution action transitions: rejected actions cannot transition to executing
        self.assertTrue(
            validate_resolution_transition(
                ResolutionStatus.PROPOSED, ResolutionStatus.APPROVED
            )
        )
        self.assertTrue(
            validate_resolution_transition(
                ResolutionStatus.APPROVED, ResolutionStatus.EXECUTING
            )
        )
        self.assertFalse(
            validate_resolution_transition(
                ResolutionStatus.PROPOSED, ResolutionStatus.EXECUTING
            )
        )
        self.assertFalse(
            validate_resolution_transition(
                ResolutionStatus.REJECTED, ResolutionStatus.EXECUTING
            )
        )

    def test_structured_logging_and_sensitive_redaction(self) -> None:
        """Credentials, API keys, and Bearer tokens must be redacted from logs."""
        set_correlation_id("cid-test-1234")
        self.assertEqual(get_correlation_id(), "cid-test-1234")

        redacted_dict = redact_sensitive_value(
            "context",
            {
                "order_id": "DY1042",
                "api_key": "super-secret-key",
                "nested": {"password": "my-db-password", "Authorization": "Bearer eyJhbGciOi"},
            },
        )
        self.assertEqual(redacted_dict["order_id"], "DY1042")
        self.assertEqual(redacted_dict["api_key"], "***REDACTED***")
        self.assertEqual(redacted_dict["nested"]["password"], "***REDACTED***")
        self.assertEqual(redacted_dict["nested"]["Authorization"], "***REDACTED***")

        msg = redact_log_message("Header Authorization: Bearer abcdef123456 and secret=my_secret_val")
        self.assertNotIn("abcdef123456", msg)
        self.assertNotIn("my_secret_val", msg)

        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="reconcilex.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Webhook failed with token=secret999",
            args=(),
            exc_info=None,
        )
        record.context = {"api_key": "raw-key-value", "order_id": "DY1042"}
        formatted = json.loads(formatter.format(record))
        self.assertEqual(formatted["correlation_id"], "cid-test-1234")
        self.assertNotIn("secret999", formatted["message"])
        self.assertEqual(formatted["context"]["api_key"], "***REDACTED***")
        self.assertEqual(formatted["context"]["order_id"], "DY1042")


if __name__ == "__main__":
    unittest.main()
