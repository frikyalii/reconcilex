"""API integration tests for Phase 6: RBAC-protected Resolution Approval, Rejection, Execution, and Audit Trail."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.main import create_app
from scripts.seed_synthetic import seed_all_synthetic_data


class TestResolutionsAPI(unittest.TestCase):
    """End-to-end API tests for /api/v1/resolutions endpoints and RBAC enforcement."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.test_settings = Settings(
            APP_NAME="ReconcileX-Phase6-Test",
            APP_VERSION="0.6.0",
            ENVIRONMENT="testing",
            DATABASE_URL="sqlite:///:memory:",
            OPERATOR_API_KEYS="op-key-1:operator,admin-key-1:admin,view-key-1:viewer",
        )
        self.session = create_session_for_settings(self.test_settings)
        seed_all_synthetic_data(self.session)

        self.app = create_app(self.test_settings)
        self.app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_end_to_end_dy1042_reconciliation_investigation_approval_execution_api(self) -> None:
        """Complete end-to-end API flow for Scenario DY1042:
        1. Run reconciliation -> detects DY1042 missing finance settlement.
        2. Run investigation -> proposes retry_webhook_sync resolution.
        3. Viewer cannot approve (403 Forbidden); unauthenticated cannot approve (401).
        4. Operator approves -> status='approved'.
        5. Operator executes -> simulated webhook replay creates FIN-TXN9001, runs post-resolution
           verification reconciliation, and marks resolution `verified_resolved`.
        6. Inspect `/approvals` and `/audit-trail`.
        """
        # 1. Run reconciliation
        r_run = self.client.post(
            "/api/v1/reconciliations", json={"triggered_by": "e2e_api_test"}
        )
        self.assertEqual(r_run.status_code, 201)
        run_id = r_run.json()["run_id"]

        r_disc = self.client.get(
            f"/api/v1/reconciliations/{run_id}/discrepancies",
            params={"rule_code": "RULE_MISSING_FINANCE_SETTLEMENT"},
        )
        discrepancy_id = r_disc.json()[0]["discrepancy_id"]

        # 2. Run investigation
        r_inv = self.client.post(
            f"/api/v1/discrepancies/{discrepancy_id}/investigations"
        )
        self.assertEqual(r_inv.status_code, 201)

        r_res_list = self.client.get(
            f"/api/v1/discrepancies/{discrepancy_id}/resolutions"
        )
        self.assertEqual(r_res_list.status_code, 200)
        resolution_id = r_res_list.json()[0]["resolution_id"]

        # 3. Verify RBAC protection on approve endpoint
        r_unauth = self.client.post(f"/api/v1/resolutions/{resolution_id}/approve")
        self.assertEqual(r_unauth.status_code, 401)

        r_viewer = self.client.post(
            f"/api/v1/resolutions/{resolution_id}/approve",
            headers={"X-API-Key": "view-key-1", "X-Actor-Id": "readonly-user"},
        )
        self.assertEqual(r_viewer.status_code, 403)

        # Attempting to execute before approval returns 400 Bad Request
        r_early_exec = self.client.post(
            f"/api/v1/resolutions/{resolution_id}/execute",
            headers={"X-API-Key": "op-key-1", "X-Actor-Id": "finance-op-1"},
        )
        self.assertEqual(r_early_exec.status_code, 400)

        # 4. Operator approves the resolution
        r_approve = self.client.post(
            f"/api/v1/resolutions/{resolution_id}/approve",
            headers={"X-API-Key": "op-key-1", "X-Actor-Id": "finance-op-1"},
            json={"comment": "Approved replay of TXN9001 webhook to finance ledger."},
        )
        self.assertEqual(r_approve.status_code, 200)
        self.assertEqual(r_approve.json()["status"], "approved")

        # 5. Operator executes the resolution -> verified_resolved
        r_exec = self.client.post(
            f"/api/v1/resolutions/{resolution_id}/execute",
            headers={"X-API-Key": "op-key-1", "X-Actor-Id": "finance-op-1"},
        )
        self.assertEqual(r_exec.status_code, 200)
        exec_body = r_exec.json()
        self.assertEqual(exec_body["status"], "verified_resolved")
        self.assertIsNotNone(exec_body["verification_run_id"])

        # 6. Verify approvals and audit trail
        r_approvals = self.client.get(f"/api/v1/resolutions/{resolution_id}/approvals")
        self.assertEqual(r_approvals.status_code, 200)
        self.assertEqual(len(r_approvals.json()), 1)
        self.assertEqual(r_approvals.json()[0]["actor_id"], "finance-op-1")
        self.assertEqual(r_approvals.json()[0]["decision"], "approved")

        r_audits = self.client.get(f"/api/v1/resolutions/{resolution_id}/audit-trail")
        self.assertEqual(r_audits.status_code, 200)
        audit_actions = [a["action"] for a in r_audits.json()]
        self.assertIn("resolution_approved", audit_actions)
        self.assertIn("resolution_executed_simulated", audit_actions)
        self.assertIn("resolution_post_verification_completed", audit_actions)


if __name__ == "__main__":
    unittest.main()
