"""Synthetic dataset generator for ReconcileX (Doyuf luxury e-commerce scenarios).

Provides realistic, deterministic synthetic datasets covering:
1. DY1001 — Clean exact match across Order, Payment Gateway, Finance Ledger, and Inventory.
2. DY1042 — Primary Specification Scenario: Order confirmed for INR 80,000, Payment Gateway
   captured TXN9001 for INR 80,000, Finance Ledger has NO matching payment entry, and
   Event Logs show 3 failed payment webhook delivery attempts.
3. DY1043 — Payment Amount Mismatch: Order expects INR 125,000, Payment Gateway captured
   INR 120,000 (short capture of INR 5,000).
4. DY1044 — Multiple Captured Payment Records: Order expects INR 60,000, Payment Gateway
   has two distinct captured charges (TXN9003A and TXN9003B) for INR 60,000 each due to
   client retry without idempotency key.
5. DY9999_ORPHAN — Orphan Payment: Payment Gateway captured TXN9004 for INR 45,000
   referencing order DY9999_ORPHAN which does not exist in the Order Management System.
6. DY1045 — Missing / Excessive Refund: Order captured INR 50,000 (TXN9005), Refund system
   recorded two processed refunds (REF5001 for INR 35,000 and REF5002 for INR 25,000 = INR 60,000),
   exceeding the captured refundable amount by INR 10,000.
7. DY1046 — Currency Mismatch: Order recorded as INR 95,000, Payment Gateway captured
   USD 95,000 (TXN9006).
8. DY1047 — Inventory Quantity Inconsistency: Order confirmed for 3 units of SKU-LUX-BAG-01,
   but Inventory Management System only deducted 1 unit (`delta_quantity = -1`).
9. DY1048 — Missing Payment for Confirmed Order: Order confirmed 4 hours ago for INR 110,000,
   zero payment records in gateway or finance ledger (stale/missing payment).
"""

from __future__ import annotations

from typing import Any, Dict, List


def get_synthetic_orders() -> List[Dict[str, Any]]:
    return [
        {
            "source_record_id": "OMS-ORD-1001",
            "order_id": "DY1001",
            "customer_id": "CUST-LUX-001",
            "expected_amount": "55000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-SILK-SCARF-01", "quantity": 1, "unit_price": "55000.0000"}],
            "event_timestamp": "2026-10-24T01:00:00Z",
            "updated_timestamp": "2026-10-24T01:05:00Z",
            "metadata": {"channel": "ios_app", "tier": "vip"},
        },
        {
            "source_record_id": "OMS-ORD-1042",
            "order_id": "DY1042",
            "customer_id": "CUST-LUX-042",
            "expected_amount": "80000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-COUTURE-GOWN-42", "quantity": 1, "unit_price": "80000.0000"}],
            "event_timestamp": "2026-10-24T02:00:00Z",
            "updated_timestamp": "2026-10-24T02:01:00Z",
            "metadata": {"channel": "web_checkout", "campaign": "festive_luxury"},
        },
        {
            "source_record_id": "OMS-ORD-1043",
            "order_id": "DY1043",
            "customer_id": "CUST-LUX-043",
            "expected_amount": "125000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-WATCH-CHRONO-09", "quantity": 1, "unit_price": "125000.0000"}],
            "event_timestamp": "2026-10-24T02:15:00Z",
            "updated_timestamp": "2026-10-24T02:16:00Z",
            "metadata": {"discount_applied": "5000_unrecorded_in_oms"},
        },
        {
            "source_record_id": "OMS-ORD-1044",
            "order_id": "DY1044",
            "customer_id": "CUST-LUX-044",
            "expected_amount": "60000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-LEATHER-TOTE-04", "quantity": 1, "unit_price": "60000.0000"}],
            "event_timestamp": "2026-10-24T02:30:00Z",
            "updated_timestamp": "2026-10-24T02:31:00Z",
            "metadata": {"channel": "android_app"},
        },
        {
            "source_record_id": "OMS-ORD-1045",
            "order_id": "DY1045",
            "customer_id": "CUST-LUX-045",
            "expected_amount": "50000.0000",
            "currency": "INR",
            "status": "refunded",
            "line_items": [{"sku": "SKU-CASHMERE-COAT-02", "quantity": 1, "unit_price": "50000.0000"}],
            "event_timestamp": "2026-10-24T03:00:00Z",
            "updated_timestamp": "2026-10-24T04:00:00Z",
            "metadata": {"return_reason": "size_exchange"},
        },
        {
            "source_record_id": "OMS-ORD-1046",
            "order_id": "DY1046",
            "customer_id": "CUST-LUX-046",
            "expected_amount": "95000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-DIAMOND-RING-07", "quantity": 1, "unit_price": "95000.0000"}],
            "event_timestamp": "2026-10-24T03:15:00Z",
            "updated_timestamp": "2026-10-24T03:16:00Z",
            "metadata": {"channel": "international_web"},
        },
        {
            "source_record_id": "OMS-ORD-1047",
            "order_id": "DY1047",
            "customer_id": "CUST-LUX-047",
            "expected_amount": "90000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-LUX-BAG-01", "quantity": 3, "unit_price": "30000.0000"}],
            "event_timestamp": "2026-10-24T03:30:00Z",
            "updated_timestamp": "2026-10-24T03:35:00Z",
            "metadata": {"expected_sku_qty": 3},
        },
        {
            "source_record_id": "OMS-ORD-1048",
            "order_id": "DY1048",
            "customer_id": "CUST-LUX-048",
            "expected_amount": "110000.0000",
            "currency": "INR",
            "status": "confirmed",
            "line_items": [{"sku": "SKU-VELVET-BLAZER-11", "quantity": 1, "unit_price": "110000.0000"}],
            "event_timestamp": "2026-10-24T00:10:00Z",
            "updated_timestamp": "2026-10-24T00:12:00Z",
            "metadata": {"note": "confirmed_without_gateway_capture"},
        },
    ]


def get_synthetic_gateway_payments() -> List[Dict[str, Any]]:
    return [
        # DY1001: clean capture
        {
            "source_record_id": "PG-REC-9000",
            "transaction_id": "TXN9000",
            "provider_charge_id": "CHG_RZ_9000",
            "order_id": "DY1001",
            "source_system": "payment_gateway",
            "amount": "55000.0000",
            "captured_amount": "55000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T01:01:00Z",
            "updated_timestamp": "2026-10-24T01:01:05Z",
            "metadata": {"gateway": "razorpay_lux"},
        },
        # DY1042: captured in gateway, but missing in finance ledger due to webhook failures
        {
            "source_record_id": "PG-REC-9001",
            "transaction_id": "TXN9001",
            "provider_charge_id": "CHG_RZ_9001",
            "order_id": "DY1042",
            "source_system": "payment_gateway",
            "amount": "80000.0000",
            "captured_amount": "80000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T02:00:30Z",
            "updated_timestamp": "2026-10-24T02:00:35Z",
            "metadata": {"gateway": "razorpay_lux", "webhook_status": "failed_3_retries"},
        },
        # DY1043: amount mismatch (captured 120000 vs expected 125000)
        {
            "source_record_id": "PG-REC-9002",
            "transaction_id": "TXN9002",
            "provider_charge_id": "CHG_RZ_9002",
            "order_id": "DY1043",
            "source_system": "payment_gateway",
            "amount": "120000.0000",
            "captured_amount": "120000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T02:15:20Z",
            "updated_timestamp": "2026-10-24T02:15:25Z",
            "metadata": {"gateway": "razorpay_lux"},
        },
        # DY1044: duplicate/multiple captures with distinct provider_charge_ids
        {
            "source_record_id": "PG-REC-9003A",
            "transaction_id": "TXN9003A",
            "provider_charge_id": "CHG_RZ_9003_FIRST",
            "order_id": "DY1044",
            "source_system": "payment_gateway",
            "amount": "60000.0000",
            "captured_amount": "60000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T02:30:10Z",
            "updated_timestamp": "2026-10-24T02:30:12Z",
            "metadata": {"attempt": 1},
        },
        {
            "source_record_id": "PG-REC-9003B",
            "transaction_id": "TXN9003B",
            "provider_charge_id": "CHG_RZ_9003_SECOND",
            "order_id": "DY1044",
            "source_system": "payment_gateway",
            "amount": "60000.0000",
            "captured_amount": "60000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T02:30:45Z",
            "updated_timestamp": "2026-10-24T02:30:48Z",
            "metadata": {"attempt": 2},
        },
        # DY9999_ORPHAN: orphan payment referencing nonexistent order
        {
            "source_record_id": "PG-REC-9004",
            "transaction_id": "TXN9004",
            "provider_charge_id": "CHG_RZ_9004",
            "order_id": "DY9999_ORPHAN",
            "source_system": "payment_gateway",
            "amount": "45000.0000",
            "captured_amount": "45000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T02:45:00Z",
            "updated_timestamp": "2026-10-24T02:45:05Z",
            "metadata": {"note": "checkout_order_rollback_after_capture"},
        },
        # DY1045: captured 50000
        {
            "source_record_id": "PG-REC-9005",
            "transaction_id": "TXN9005",
            "provider_charge_id": "CHG_RZ_9005",
            "order_id": "DY1045",
            "source_system": "payment_gateway",
            "amount": "50000.0000",
            "captured_amount": "50000.0000",
            "currency": "INR",
            "status": "refunded",
            "event_timestamp": "2026-10-24T03:01:00Z",
            "updated_timestamp": "2026-10-24T04:00:00Z",
            "metadata": {},
        },
        # DY1046: currency mismatch (USD instead of INR)
        {
            "source_record_id": "PG-REC-9006",
            "transaction_id": "TXN9006",
            "provider_charge_id": "CHG_STRIPE_9006",
            "order_id": "DY1046",
            "source_system": "payment_gateway",
            "amount": "95000.0000",
            "captured_amount": "95000.0000",
            "currency": "USD",
            "status": "captured",
            "event_timestamp": "2026-10-24T03:15:30Z",
            "updated_timestamp": "2026-10-24T03:15:35Z",
            "metadata": {"gateway": "stripe_intl"},
        },
        # DY1047: clean payment for inventory mismatch order
        {
            "source_record_id": "PG-REC-9007",
            "transaction_id": "TXN9007",
            "provider_charge_id": "CHG_RZ_9007",
            "order_id": "DY1047",
            "source_system": "payment_gateway",
            "amount": "90000.0000",
            "captured_amount": "90000.0000",
            "currency": "INR",
            "status": "captured",
            "event_timestamp": "2026-10-24T03:31:00Z",
            "updated_timestamp": "2026-10-24T03:31:05Z",
            "metadata": {},
        },
    ]


def get_synthetic_finance_ledger_payments() -> List[Dict[str, Any]]:
    """Finance settlement database entries.

    Notice that DY1042 (TXN9001) is intentionally absent here because its
    settlement webhook failed 3 times.
    """
    return [
        {
            "source_record_id": "FIN-LED-9000",
            "transaction_id": "FIN-TXN9000",
            "provider_charge_id": "CHG_RZ_9000",
            "order_id": "DY1001",
            "source_system": "finance_ledger",
            "amount": "55000.0000",
            "captured_amount": "55000.0000",
            "currency": "INR",
            "status": "settled",
            "event_timestamp": "2026-10-24T01:02:00Z",
            "updated_timestamp": "2026-10-24T01:02:00Z",
            "metadata": {"ledger_batch": "BATCH-20261024-01", "gateway_txn_id": "TXN9000"},
        },
        {
            "source_record_id": "FIN-LED-9002",
            "transaction_id": "FIN-TXN9002",
            "provider_charge_id": "CHG_RZ_9002",
            "order_id": "DY1043",
            "source_system": "finance_ledger",
            "amount": "120000.0000",
            "captured_amount": "120000.0000",
            "currency": "INR",
            "status": "settled",
            "event_timestamp": "2026-10-24T02:16:00Z",
            "updated_timestamp": "2026-10-24T02:16:00Z",
            "metadata": {"ledger_batch": "BATCH-20261024-02", "gateway_txn_id": "TXN9002"},
        },
        {
            "source_record_id": "FIN-LED-9005",
            "transaction_id": "FIN-TXN9005",
            "provider_charge_id": "CHG_RZ_9005",
            "order_id": "DY1045",
            "source_system": "finance_ledger",
            "amount": "50000.0000",
            "captured_amount": "50000.0000",
            "currency": "INR",
            "status": "settled",
            "event_timestamp": "2026-10-24T03:02:00Z",
            "updated_timestamp": "2026-10-24T03:02:00Z",
            "metadata": {"ledger_batch": "BATCH-20261024-03", "gateway_txn_id": "TXN9005"},
        },
        {
            "source_record_id": "FIN-LED-9007",
            "transaction_id": "FIN-TXN9007",
            "provider_charge_id": "CHG_RZ_9007",
            "order_id": "DY1047",
            "source_system": "finance_ledger",
            "amount": "90000.0000",
            "captured_amount": "90000.0000",
            "currency": "INR",
            "status": "settled",
            "event_timestamp": "2026-10-24T03:32:00Z",
            "updated_timestamp": "2026-10-24T03:32:00Z",
            "metadata": {"ledger_batch": "BATCH-20261024-03", "gateway_txn_id": "TXN9007"},
        },
    ]


def get_synthetic_refunds() -> List[Dict[str, Any]]:
    return [
        {
            "source_record_id": "RFM-5001",
            "refund_id": "REF5001",
            "transaction_id": "TXN9005",
            "order_id": "DY1045",
            "provider_refund_id": "RFD_RZ_5001",
            "amount": "35000.0000",
            "currency": "INR",
            "status": "processed",
            "reason": "Partial return batch 1",
            "event_timestamp": "2026-10-24T03:45:00Z",
            "updated_timestamp": "2026-10-24T03:46:00Z",
            "metadata": {},
        },
        {
            "source_record_id": "RFM-5002",
            "refund_id": "REF5002",
            "transaction_id": "TXN9005",
            "order_id": "DY1045",
            "provider_refund_id": "RFD_RZ_5002",
            "amount": "25000.0000",
            "currency": "INR",
            "status": "processed",
            "reason": "Duplicate manual refund adjustment",
            "event_timestamp": "2026-10-24T03:55:00Z",
            "updated_timestamp": "2026-10-24T03:56:00Z",
            "metadata": {},
        },
    ]


def get_synthetic_inventory_records() -> List[Dict[str, Any]]:
    return [
        {
            "source_record_id": "INV-REC-1001",
            "inventory_record_id": "INV-1001",
            "sku": "SKU-SILK-SCARF-01",
            "order_id": "DY1001",
            "delta_quantity": -1,
            "available_quantity": 19,
            "reserved_quantity": 0,
            "warehouse_id": "WH-MUMBAI-LUX",
            "status": "deducted",
            "event_timestamp": "2026-10-24T01:02:00Z",
            "updated_timestamp": "2026-10-24T01:02:00Z",
            "metadata": {},
        },
        {
            "source_record_id": "INV-REC-1042",
            "inventory_record_id": "INV-1042",
            "sku": "SKU-COUTURE-GOWN-42",
            "order_id": "DY1042",
            "delta_quantity": -1,
            "available_quantity": 4,
            "reserved_quantity": 0,
            "warehouse_id": "WH-MUMBAI-LUX",
            "status": "deducted",
            "event_timestamp": "2026-10-24T02:01:00Z",
            "updated_timestamp": "2026-10-24T02:01:00Z",
            "metadata": {},
        },
        # DY1047 ordered 3 units of SKU-LUX-BAG-01, but only 1 unit was deducted
        {
            "source_record_id": "INV-REC-1047",
            "inventory_record_id": "INV-1047",
            "sku": "SKU-LUX-BAG-01",
            "order_id": "DY1047",
            "delta_quantity": -1,
            "available_quantity": 14,
            "reserved_quantity": 2,
            "warehouse_id": "WH-DELHI-LUX",
            "status": "deducted",
            "event_timestamp": "2026-10-24T03:32:00Z",
            "updated_timestamp": "2026-10-24T03:32:00Z",
            "metadata": {"error": "partial_reservation_commit"},
        },
    ]


def get_synthetic_event_logs() -> List[Dict[str, Any]]:
    return [
        {
            "event_id": "EVT-DY1042-WH-1",
            "source_record_id": "LOG-WH-9001-1",
            "event_type": "payment.captured.webhook",
            "order_id": "DY1042",
            "transaction_id": "TXN9001",
            "delivery_status": "failed",
            "attempt_count": 1,
            "error_code": "HTTP_503_FINANCE_INGEST_TIMEOUT",
            "message": "Webhook delivery to finance-settlement-service timed out after 5000ms (attempt 1/3).",
            "payload": {"provider_charge_id": "CHG_RZ_9001", "amount": "80000.0000", "currency": "INR"},
            "event_timestamp": "2026-10-24T02:00:36Z",
        },
        {
            "event_id": "EVT-DY1042-WH-2",
            "source_record_id": "LOG-WH-9001-2",
            "event_type": "payment.captured.webhook",
            "order_id": "DY1042",
            "transaction_id": "TXN9001",
            "delivery_status": "failed",
            "attempt_count": 2,
            "error_code": "HTTP_503_FINANCE_INGEST_TIMEOUT",
            "message": "Webhook delivery to finance-settlement-service timed out after 5000ms (attempt 2/3).",
            "payload": {"provider_charge_id": "CHG_RZ_9001", "amount": "80000.0000", "currency": "INR"},
            "event_timestamp": "2026-10-24T02:01:06Z",
        },
        {
            "event_id": "EVT-DY1042-WH-3",
            "source_record_id": "LOG-WH-9001-3",
            "event_type": "payment.captured.webhook",
            "order_id": "DY1042",
            "transaction_id": "TXN9001",
            "delivery_status": "dropped",
            "attempt_count": 3,
            "error_code": "WEBHOOK_MAX_RETRIES_EXCEEDED",
            "message": "Payment webhook delivery failed three times; event moved to dead-letter queue.",
            "payload": {"provider_charge_id": "CHG_RZ_9001", "amount": "80000.0000", "currency": "INR"},
            "event_timestamp": "2026-10-24T02:02:06Z",
        },
    ]
