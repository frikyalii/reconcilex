"""Phase 7 integration tests: evaluation benchmark metrics, OpenTelemetry tracing spans, and rate limiting."""

from __future__ import annotations

import unittest

from fastapi import Depends
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.session import reset_in_memory_databases
from app.main import create_app
from app.observability.evaluation import run_evaluation_benchmark
from app.observability.tracing import span_recorder, trace_span


class TestPhase7EvaluationAndHardening(unittest.TestCase):
    """Verify empirical evaluation metrics, span tracing, and sliding-window rate limiting."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        span_recorder.clear()

    def tearDown(self) -> None:
        reset_in_memory_databases()
        span_recorder.clear()

    def test_evaluation_benchmark_suite_measures_precision_recall_and_validator_safety(self) -> None:
        """Run the empirical evaluation suite and verify 100% precision/recall on synthetic ground truth and 100% rejection of adversarial claims."""
        report = run_evaluation_benchmark()
        self.assertEqual(report.expected_discrepancies_count, 9)
        self.assertEqual(report.detected_discrepancies_count, 9)
        self.assertEqual(report.true_positives, 9)
        self.assertEqual(report.false_positives, 0)
        self.assertEqual(report.false_negatives, 0)
        self.assertEqual(report.detection_precision, 1.0)
        self.assertEqual(report.detection_recall, 1.0)
        self.assertEqual(report.detection_f1, 1.0)

        self.assertEqual(report.evaluated_investigations_count, 8)
        self.assertEqual(report.root_cause_hypothesis_accuracy, 1.0)
        self.assertEqual(report.evidence_citation_correctness_rate, 1.0)
        self.assertEqual(report.grounded_unsupported_claim_rate, 0.0)
        self.assertEqual(report.grounded_validation_failure_rate, 0.0)
        self.assertGreater(report.mean_investigation_latency_ms, 0.0)

        # Verify adversarial hallucinated outputs were 100% rejected
        self.assertEqual(report.adversarial_trials_count, 3)
        self.assertEqual(report.adversarial_rejection_rate, 1.0)

    def test_opentelemetry_span_recorder_captures_duration_and_attributes(self) -> None:
        """Verify trace_span records span name, correlation_id, duration_ms, and custom attributes."""
        with trace_span("reconciliation.evaluate_rule", rule_code="RULE_MISSING_FINANCE_SETTLEMENT"):
            pass

        spans = span_recorder.list_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "reconciliation.evaluate_rule")
        self.assertEqual(
            spans[0].attributes["rule_code"], "RULE_MISSING_FINANCE_SETTLEMENT"
        )
        self.assertEqual(spans[0].status, "OK")
        self.assertGreaterEqual(spans[0].duration_ms, 0.0)

    def test_sliding_window_rate_limiter_enforces_429_too_many_requests(self) -> None:
        """Verify SlidingWindowRateLimiter blocks requests exceeding max_requests with HTTP 429."""
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)
        settings = Settings(DATABASE_URL="sqlite:///:memory:")
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings

        @app.post("/api/v1/_test/expensive-op")
        def _expensive_op() -> dict[str, str]:
            limiter.check_or_raise("expensive:test-caller")
            return {"status": "allowed"}

        client = TestClient(app, raise_server_exceptions=False)
        self.assertEqual(client.post("/api/v1/_test/expensive-op").status_code, 200)
        self.assertEqual(client.post("/api/v1/_test/expensive-op").status_code, 200)
        r_blocked = client.post("/api/v1/_test/expensive-op")
        self.assertEqual(r_blocked.status_code, 429)
        self.assertEqual(r_blocked.json()["error_code"], "HTTP_429")


if __name__ == "__main__":
    unittest.main()
