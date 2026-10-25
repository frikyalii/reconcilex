"""API integration tests for Phase 2: Sources, Validation, Import, Orders, Payments, Refunds, Inventory, and EventLogs."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.session import reset_in_memory_databases
from app.main import create_app


class TestPhase2CrudAndIngestionAPI(unittest.TestCase):
    """End-to-end API tests for Phase 2 endpoints."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.test_settings = Settings(
            APP_NAME="ReconcileX-Phase2-Test",
            APP_VERSION="0.2.0",
            ENVIRONMENT="testing",
            DATABASE_URL="sqlite:///:memory:",
        )
        self.app = create_app(self.test_settings)
        self.app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_orders_payments_refunds_inventory_crud_api(self) -> None:
        """Verify POST and GET endpoints for orders, payments (including orphan), refunds, and inventory."""
        # 1. Create Order DY1042
        r_order = self.client.post(
            "/api/v1/orders",
            json={
                "order_id": "DY1042",
                "source_record_id": "OMS-1042",
                "expected_amount": "80000.0000",
                "currency": "INR",
                "status": "confirmed",
                "line_items": [{"sku": "SKU-COUTURE-GOWN-42", "quantity": 1}],
                "event_timestamp": "2026-10-24T02:00:00Z",
                "updated_timestamp": "2026-10-24T02:01:00Z",
            },
        )
        self.assertEqual(r_order.status_code, 201)
        self.assertEqual(r_order.json()["order_id"], "DY1042")
        self.assertEqual(str(r_order.json()["expected_amount"]), "80000.0000")

        # Duplicate order_id returns 409 Conflict
        r_dup = self.client.post(
            "/api/v1/orders",
            json={
                "order_id": "DY1042",
                "source_record_id": "OMS-1042-DUP",
                "expected_amount": "80000.0000",
                "currency": "INR",
                "status": "confirmed",
                "event_timestamp": "2026-10-24T02:00:00Z",
                "updated_timestamp": "2026-10-24T02:01:00Z",
            },
        )
        self.assertEqual(r_dup.status_code, 409)

        # GET /api/v1/orders/DY1042
        r_get_order = self.client.get("/api/v1/orders/DY1042")
        self.assertEqual(r_get_order.status_code, 200)
        self.assertEqual(r_get_order.json()["source_record_id"], "OMS-1042")

        # 2. Create Payment TXN9001 for DY1042 and Orphan Payment TXN9004 for nonexistent DY9999
        r_pay = self.client.post(
            "/api/v1/payments",
            json={
                "transaction_id": "TXN9001",
                "provider_charge_id": "CHG_9001",
                "order_id": "DY1042",
                "source_record_id": "PG-9001",
                "amount": "80000.0000",
                "captured_amount": "80000.0000",
                "currency": "INR",
                "status": "captured",
                "source_system": "payment_gateway",
                "event_timestamp": "2026-10-24T02:00:30Z",
                "updated_timestamp": "2026-10-24T02:00:35Z",
            },
        )
        self.assertEqual(r_pay.status_code, 201)

        r_orphan = self.client.post(
            "/api/v1/payments",
            json={
                "transaction_id": "TXN9004_ORPHAN",
                "provider_charge_id": "CHG_9004",
                "order_id": "DY9999_NONEXISTENT",
                "source_record_id": "PG-9004",
                "amount": "45000.0000",
                "captured_amount": "45000.0000",
                "currency": "INR",
                "status": "captured",
                "source_system": "payment_gateway",
                "event_timestamp": "2026-10-24T02:45:00Z",
                "updated_timestamp": "2026-10-24T02:45:05Z",
            },
        )
        self.assertEqual(r_orphan.status_code, 201)

        r_get_pay = self.client.get("/api/v1/payments/TXN9001")
        self.assertEqual(r_get_pay.status_code, 200)
        self.assertEqual(r_get_pay.json()["provider_charge_id"], "CHG_9001")

        # 3. Create Refund & Inventory Record
        r_ref = self.client.post(
            "/api/v1/refunds",
            json={
                "refund_id": "REF5001",
                "transaction_id": "TXN9001",
                "order_id": "DY1042",
                "source_record_id": "RFM-5001",
                "amount": "10000.0000",
                "currency": "INR",
                "status": "processed",
                "event_timestamp": "2026-10-24T03:00:00Z",
                "updated_timestamp": "2026-10-24T03:01:00Z",
            },
        )
        self.assertEqual(r_ref.status_code, 201)

        r_inv = self.client.post(
            "/api/v1/inventory-records",
            json={
                "inventory_record_id": "INV-1042",
                "sku": "SKU-COUTURE-GOWN-42",
                "order_id": "DY1042",
                "source_record_id": "WMS-1042",
                "delta_quantity": -1,
                "available_quantity": 9,
                "reserved_quantity": 0,
                "warehouse_id": "WH-MUMBAI",
                "status": "deducted",
                "event_timestamp": "2026-10-24T02:01:00Z",
                "updated_timestamp": "2026-10-24T02:01:00Z",
            },
        )
        self.assertEqual(r_inv.status_code, 201)

    def test_sources_validate_and_import_endpoints(self) -> None:
        """Verify POST /api/v1/sources, /validate, and /import with inline CSV and synthetic preset."""
        # 1. Create a CSV source with custom column mapping
        r_src = self.client.post(
            "/api/v1/sources",
            json={
                "name": "inline_csv_payments",
                "source_type": "csv",
                "target_entity": "payments",
                "connection_config": {"delimiter": ","},
                "schema_mapping": {
                    "field_mappings": {
                        "source_record_id": "rec_id",
                        "transaction_id": "txn_id",
                        "provider_charge_id": "charge_id",
                        "order_id": "ord_id",
                        "amount": "amt",
                        "captured_amount": "cap_amt",
                        "currency": "curr",
                        "status": "st",
                        "event_timestamp": "ts",
                        "updated_timestamp": "ts",
                    },
                    "required_fields": ["source_record_id", "transaction_id", "order_id", "amount"],
                },
            },
        )
        self.assertEqual(r_src.status_code, 201)
        source_id = r_src.json()["source_id"]

        csv_payload = (
            "rec_id,txn_id,charge_id,ord_id,amt,cap_amt,curr,st,ts\n"
            "R1,TXN_A1,CHG_A1,DY5001,25000.0000,25000.0000,INR,captured,2026-10-24T05:00:00Z\n"
            "R2,,CHG_A2,DY5002,30000.0000,30000.0000,INR,captured,2026-10-24T05:05:00Z\n"
        )

        # 2. Validate via POST /api/v1/sources/{source_id}/validate
        r_val = self.client.post(
            f"/api/v1/sources/{source_id}/validate",
            json={"csv_content": csv_payload},
        )
        self.assertEqual(r_val.status_code, 200)
        val_body = r_val.json()
        self.assertEqual(val_body["total_rows"], 2)
        self.assertEqual(val_body["valid_rows"], 1)
        self.assertEqual(val_body["invalid_rows"], 1)
        self.assertEqual(val_body["row_errors"][0]["row_number"], 2)
        self.assertEqual(val_body["row_errors"][0]["error_code"], "MISSING_REQUIRED_FIELD")

        # 3. Import via POST /api/v1/sources/{source_id}/import
        r_imp = self.client.post(
            f"/api/v1/sources/{source_id}/import",
            json={"csv_content": csv_payload},
        )
        self.assertEqual(r_imp.status_code, 200)
        imp_body = r_imp.json()
        self.assertEqual(imp_body["imported_rows"], 1)
        self.assertEqual(imp_body["imported_ids"], ["TXN_A1"])

        # Verify TXN_A1 is retrievable via GET /api/v1/payments/TXN_A1
        r_check = self.client.get("/api/v1/payments/TXN_A1")
        self.assertEqual(r_check.status_code, 200)
        self.assertEqual(str(r_check.json()["captured_amount"]), "25000.0000")


if __name__ == "__main__":
    unittest.main()
