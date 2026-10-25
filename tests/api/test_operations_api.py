"""API tests for Phase 1 operational endpoints, correlation headers, and RBAC security."""

from __future__ import annotations

import unittest

from fastapi import Depends
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import Principal, require_roles
from app.db.session import reset_in_memory_databases
from app.main import create_app


class TestOperationsAndSecurityAPI(unittest.TestCase):
    """Verify /health, /ready, /metrics, correlation ID propagation, and RBAC."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.test_settings = Settings(
            APP_NAME="ReconcileX-Test",
            APP_VERSION="0.1.0",
            ENVIRONMENT="testing",
            DATABASE_URL="sqlite:///:memory:",
            METRICS_ENABLED=True,
            OPERATOR_API_KEYS="op-key-1:operator,admin-key-1:admin,view-key-1:viewer",
        )
        self.app = create_app(self.test_settings)
        self.app.dependency_overrides[get_settings] = lambda: self.test_settings

        # Register a protected test route to verify RBAC enforcement in Phase 1
        @self.app.post("/api/v1/_test/operator-only")
        def _operator_only_endpoint(
            principal: Principal = Depends(require_roles(["operator", "admin"])),
        ) -> dict[str, str]:
            return {"actor_id": principal.actor_id, "role": principal.role}

        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_health_endpoint_and_correlation_id(self) -> None:
        """GET /health returns 200 OK and echoes custom X-Correlation-ID header."""
        response = self.client.get(
            "/health", headers={"X-Correlation-ID": "corr-dy1042-health"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Correlation-ID"), "corr-dy1042-health")
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["app_name"], "ReconcileX-Test")
        self.assertEqual(body["environment"], "testing")
        self.assertEqual(body["correlation_id"], "corr-dy1042-health")

    def test_ready_endpoint_verifies_all_13_tables(self) -> None:
        """GET /ready verifies database connectivity and all 13 core schema tables."""
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        checks = {item["component"]: item for item in body["checks"]}
        self.assertTrue(checks["database_connectivity"]["ready"])
        self.assertTrue(checks["database_schema"]["ready"])
        self.assertIn("All 13 core tables verified", checks["database_schema"]["detail"])

    def test_metrics_endpoint_exposes_prometheus_text(self) -> None:
        """GET /metrics returns Prometheus exposition format metrics."""
        self.client.get("/health")
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("reconcilex_up 1", response.text)
        self.assertIn("reconcilex_health_requests_total", response.text)

    def test_rbac_authentication_and_authorization(self) -> None:
        """Verify missing key -> 401, viewer role -> 403, operator role -> 200."""
        # 1. Missing API key
        r_unauth = self.client.post("/api/v1/_test/operator-only")
        self.assertEqual(r_unauth.status_code, 401)
        self.assertEqual(r_unauth.json()["error_code"], "HTTP_401")

        # 2. Insufficient role (viewer attempting operator action)
        r_forbidden = self.client.post(
            "/api/v1/_test/operator-only",
            headers={"X-API-Key": "view-key-1", "X-Actor-Id": "auditor-1"},
        )
        self.assertEqual(r_forbidden.status_code, 403)
        self.assertEqual(r_forbidden.json()["error_code"], "HTTP_403")

        # 3. Authorized operator role
        r_ok = self.client.post(
            "/api/v1/_test/operator-only",
            headers={"X-API-Key": "op-key-1", "X-Actor-Id": "finance-op-42"},
        )
        self.assertEqual(r_ok.status_code, 200)
        self.assertEqual(
            r_ok.json(), {"actor_id": "finance-op-42", "role": "operator"}
        )


if __name__ == "__main__":
    unittest.main()
