"""Unit tests for Phase 4: Celery background tasks, durable job lifecycle, retry of failed jobs, cancellation, and idempotency."""

from __future__ import annotations

import unittest

from app.core.config import Settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.models.enums import ReconciliationRunStatus
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.services.reconciliation.engine import ReconciliationEngine
from app.workers.tasks import dispatch_reconciliation_job, run_reconciliation_job_task
from scripts.seed_synthetic import seed_all_synthetic_data


class TestAsyncWorkersAndJobLifecycle(unittest.TestCase):
    """Verify durable job state transitions, worker execution, safe retries, and cancellation."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.db_url = "sqlite:///:memory:"
        self.settings = Settings(DATABASE_URL=self.db_url, CELERY_TASK_ALWAYS_EAGER=True)
        self.session = create_session_for_settings(self.settings)
        seed_all_synthetic_data(self.session)
        self.recon_repo = ReconciliationRepository(self.session)
        self.engine = ReconciliationEngine(self.session)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_enqueue_and_celery_worker_execution_with_idempotency(self) -> None:
        """Enqueueing creates a durable `pending` run in DB; worker executes it to completion and repeated worker delivery is idempotent."""
        pending_run = self.engine.enqueue_run(
            triggered_by="celery_scheduler",
            idempotency_key="idem-worker-job-001",
        )
        self.session.commit()
        self.assertEqual(pending_run.status, ReconciliationRunStatus.PENDING)

        # Execute via Celery worker task
        result = run_reconciliation_job_task.apply_async(
            kwargs={
                "run_id": pending_run.run_id,
                "correlation_id": "cid-worker-test-1",
                "database_url": self.db_url,
            }
        )
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(
            result.result["status"],
            ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES.value,
        )
        disc_count = result.result["discrepancy_count"]
        self.assertGreaterEqual(disc_count, 7)

        # Verify durable state in DB
        completed_run = self.recon_repo.get_run(pending_run.run_id)
        assert completed_run is not None
        self.assertEqual(
            completed_run.status,
            ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES,
        )

        # Duplicate worker delivery for the same run_id must be idempotent (no duplicate findings)
        dup_result = run_reconciliation_job_task.apply_async(
            kwargs={
                "run_id": pending_run.run_id,
                "correlation_id": "cid-worker-test-dup",
                "database_url": self.db_url,
            }
        )
        self.assertEqual(dup_result.result["discrepancy_count"], disc_count)
        persisted_findings = self.recon_repo.list_discrepancies_for_run(
            pending_run.run_id
        )
        self.assertEqual(len(persisted_findings), disc_count)

    def test_retry_failed_run_clears_error_and_completes_cleanly(self) -> None:
        """A failed job transitions `failed -> running -> completed_with_discrepancies` on retry and clears error_summary."""
        run = self.engine.enqueue_run(triggered_by="retry_test")
        # Simulate a transient failure during execution
        self.recon_repo.update_run_state(
            run.run_id,
            target_status=ReconciliationRunStatus.FAILED,
            error_summary="OperationalError: transient database connection reset",
        )
        self.session.commit()

        failed_run = self.recon_repo.get_run(run.run_id)
        assert failed_run is not None
        self.assertEqual(failed_run.status, ReconciliationRunStatus.FAILED)
        self.assertIsNotNone(failed_run.error_summary)

        # Retry the failed run via dispatch_reconciliation_job
        dispatch_reconciliation_job(
            run_id=run.run_id,
            settings=self.settings,
            execute_immediately_in_eager_mode=True,
        )

        retried_run = self.recon_repo.get_run(run.run_id)
        assert retried_run is not None
        self.assertEqual(
            retried_run.status,
            ReconciliationRunStatus.COMPLETED_WITH_DISCREPANCIES,
        )
        self.assertIsNone(retried_run.error_summary)
        self.assertGreaterEqual(retried_run.discrepancy_count, 7)

    def test_cancel_pending_run_prevents_execution(self) -> None:
        """Cancelling a pending run transitions it to `cancelled` and blocks subsequent execution."""
        run = self.engine.enqueue_run(triggered_by="cancel_test")
        cancelled = self.engine.cancel_run(run.run_id, actor_id="op-99")
        self.assertEqual(cancelled.status, ReconciliationRunStatus.CANCELLED)

        with self.assertRaises(ValueError):
            self.engine.execute_existing_run(run.run_id)


if __name__ == "__main__":
    unittest.main()
