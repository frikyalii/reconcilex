"""API integration tests for Phase 5: Discrepancy Investigations and Retry endpoints."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.main import create_app
from scripts.seed_synthetic import seed_all_synthetic_data


class TestInvestigationsAPI(unittest.TestCase):
    """End-to-end API tests for /api/v1/discrepancies/{id}/investigations and /api/v1/investigations/{id}."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.test_settings = Settings(
            APP_NAME="ReconcileX-Phase5-Test",
            APP_VERSION="0.5.0",
            ENVIRONMENT="testing",
            DATABASE_URL="sqlite:///:memory:",
        )
        self.session = create_session_for_settings(self.test_settings)
        seed_all_synthetic_data(self.session)

        self.app = create_app(self.test_settings)
        self.app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_trigger_get_and_retry_investigation_api(self) -> None:
        """Verify triggering an investigation for DY1042 via REST API, inspecting evidence citations, and listing proposed resolutions."""
        # 1. Run reconciliation first
        r_run = self.client.post(
            "/api/v1/reconciliations", json={"triggered_by": "api_test"}
        )
        self.assertEqual(r_run.status_code, 201)
        run_id = r_run.json()["run_id"]

        # 2. Fetch the DY1042 missing finance settlement discrepancy
        r_disc = self.client.get(
            f"/api/v1/reconciliations/{run_id}/discrepancies",
            params={"rule_code": "RULE_MISSING_FINANCE_SETTLEMENT"},
        )
        self.assertEqual(r_disc.status_code, 200)
        discrepancy_id = r_disc.json()[0]["discrepancy_id"]

        # 3. Trigger investigation: POST /api/v1/discrepancies/{discrepancy_id}/investigations
        r_inv = self.client.post(
            f"/api/v1/discrepancies/{discrepancy_id}/investigations"
        )
        self.assertEqual(r_inv.status_code, 201)
        inv_body = r_inv.json()
        investigation_id = inv_body["investigation_id"]
        self.assertEqual(inv_body["status"], "completed")
        self.assertTrue(inv_body["validation_passed"])
        self.assertIn("ORDER:DY1042", inv_body["evidence_references"])
        self.assertIn("PAYMENT:TXN9001", inv_body["evidence_references"])
        self.assertEqual(str(inv_body["confidence_score"]), "0.9500")

        # 4. Retrieve investigation: GET /api/v1/investigations/{investigation_id}
        r_get = self.client.get(f"/api/v1/investigations/{investigation_id}")
        self.assertEqual(r_get.status_code, 200)
        self.assertEqual(r_get.json()["investigation_id"], investigation_id)

        # 5. Retry investigation: POST /api/v1/investigations/{investigation_id}/retry
        r_retry = self.client.post(f"/api/v1/investigations/{investigation_id}/retry")
        self.assertEqual(r_retry.status_code, 200)
        self.assertEqual(r_retry.json()["status"], "completed")

        # 6. Check proposed resolution created for the discrepancy
        r_res = self.client.get(
            f"/api/v1/discrepancies/{discrepancy_id}/resolutions"
        )
        self.assertEqual(r_res.status_code, 200)
        resolutions = r_res.json()
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(resolutions[0]["action_type"], "retry_webhook_sync")
        self.assertEqual(resolutions[0]["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
