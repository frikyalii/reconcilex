"""API integration tests for Phase 3 & Phase 4: ReconciliationRules, ReconciliationRuns, Async 202 Dispatch, Retry, Cancel, and Discrepancies."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.main import create_app
from app.models.enums import ReconciliationRunStatus
from app.repositories.reconciliation_repo import ReconciliationRepository
from scripts.seed_synthetic import seed_all_synthetic_data


class TestReconciliationAPI(unittest.TestCase):
    """End-to-end API tests for /api/v1/reconciliation-rules and /api/v1/reconciliations."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.test_settings = Settings(
            APP_NAME="ReconcileX-Phase3-4-Test",
            APP_VERSION="0.4.0",
            ENVIRONMENT="testing",
            DATABASE_URL="sqlite:///:memory:",
            CELERY_TASK_ALWAYS_EAGER=False,
        )
        self.session = create_session_for_settings(self.test_settings)
        seed_all_synthetic_data(self.session)

        self.app = create_app(self.test_settings)
        self.app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_rules_and_reconciliation_run_lifecycle_api(self) -> None:
        """Verify listing default rules, triggering an idempotent reconciliation run, and querying discrepancies."""
        # 1. GET /api/v1/reconciliation-rules seeds and returns the 11 default rules
        r_rules = self.client.get("/api/v1/reconciliation-rules")
        self.assertEqual(r_rules.status_code, 200)
        rules = r_rules.json()
        self.assertEqual(len(rules), 11)

        # 2. Trigger reconciliation run via POST /api/v1/reconciliations with idempotency_key
        r_run = self.client.post(
            "/api/v1/reconciliations",
            json={
                "idempotency_key": "idem-run-dy1042-001",
                "triggered_by": "finance_operator",
            },
        )
        self.assertEqual(r_run.status_code, 201)
        run_body = r_run.json()
        run_id = run_body["run_id"]
        self.assertEqual(run_body["status"], "completed_with_discrepancies")
        self.assertGreaterEqual(run_body["discrepancy_count"], 7)
        self.assertEqual(run_body["input_counts"]["orders"], 8)

        # 3. Calling POST /api/v1/reconciliations with the same idempotency_key returns the exact same run_id
        r_idem = self.client.post(
            "/api/v1/reconciliations",
            json={
                "idempotency_key": "idem-run-dy1042-001",
                "triggered_by": "finance_operator",
            },
        )
        self.assertEqual(r_idem.status_code, 201)
        self.assertEqual(r_idem.json()["run_id"], run_id)

        # 4. GET /api/v1/reconciliations/{run_id}
        r_get_run = self.client.get(f"/api/v1/reconciliations/{run_id}")
        self.assertEqual(r_get_run.status_code, 200)
        self.assertEqual(r_get_run.json()["run_id"], run_id)

        # 5. GET /api/v1/reconciliations/{run_id}/discrepancies filtered by rule_code
        r_disc = self.client.get(
            f"/api/v1/reconciliations/{run_id}/discrepancies",
            params={"rule_code": "RULE_MISSING_FINANCE_SETTLEMENT"},
        )
        self.assertEqual(r_disc.status_code, 200)
        discs = r_disc.json()
        self.assertEqual(len(discs), 1)
        self.assertEqual(discs[0]["order_id"], "DY1042")
        self.assertEqual(discs[0]["transaction_id"], "TXN9001")
        self.assertEqual(discs[0]["severity"], "critical")

    def test_async_202_dispatch_retry_and_cancel_api(self) -> None:
        """Verify HTTP 202 Accepted on async_execution=True, cancelling a pending job, and retrying a failed job."""
        # 1. Trigger with async_execution=True -> returns 202 Accepted and persists pending run
        r_async = self.client.post(
            "/api/v1/reconciliations",
            json={
                "idempotency_key": "idem-async-202-001",
                "triggered_by": "async_caller",
                "async_execution": True,
            },
        )
        self.assertEqual(r_async.status_code, 202)
        run_id = r_async.json()["run_id"]
        self.assertEqual(r_async.json()["status"], "pending")

        # 2. Retry/execute the pending job synchronously via POST /api/v1/reconciliations/{run_id}/retry
        r_retry = self.client.post(f"/api/v1/reconciliations/{run_id}/retry")
        self.assertEqual(r_retry.status_code, 200)
        self.assertEqual(r_retry.json()["status"], "completed_with_discrepancies")

        # Attempting to retry an already completed run returns 400 Bad Request
        r_bad_retry = self.client.post(f"/api/v1/reconciliations/{run_id}/retry")
        self.assertEqual(r_bad_retry.status_code, 400)

        # 3. Create another pending run and cancel it via POST /api/v1/reconciliations/{run_id}/cancel
        r_async2 = self.client.post(
            "/api/v1/reconciliations",
            json={"triggered_by": "cancel_caller", "async_execution": True},
        )
        self.assertEqual(r_async2.status_code, 202)
        run2_id = r_async2.json()["run_id"]

        r_cancel = self.client.post(f"/api/v1/reconciliations/{run2_id}/cancel")
        self.assertEqual(r_cancel.status_code, 200)
        self.assertEqual(r_cancel.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
