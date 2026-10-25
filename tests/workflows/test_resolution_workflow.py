"""Workflow tests for Phase 6: Safe Resolution, Human Approval Gate, Rejection, Idempotency, and Post-Resolution Verification."""

from __future__ import annotations

import unittest

from app.core.config import Settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.models.enums import (
    DiscrepancyStatus,
    ResolutionStatus,
)
from app.repositories.audit_repo import AuditRepository
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.repositories.records_repo import RecordsRepository
from app.repositories.resolution_repo import ResolutionRepository
from app.services.investigation.service import InvestigationService
from app.services.reconciliation.engine import ReconciliationEngine
from app.services.resolution.service import ResolutionService
from scripts.seed_synthetic import seed_all_synthetic_data


class TestResolutionAndVerificationWorkflow(unittest.TestCase):
    """Verify all mandatory Section 8 resolution workflow test cases."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.settings = Settings(DATABASE_URL="sqlite:///:memory:")
        self.session = create_session_for_settings(self.settings)
        seed_all_synthetic_data(self.session)
        self.recon_repo = ReconciliationRepository(self.session)
        self.records_repo = RecordsRepository(self.session)
        self.res_repo = ResolutionRepository(self.session)
        self.audit_repo = AuditRepository(self.session)
        self.engine = ReconciliationEngine(self.session)
        self.inv_service = InvestigationService(self.session)
        self.res_service = ResolutionService(self.session)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_dy1042_full_approval_execution_idempotency_and_verification(self) -> None:
        """End-to-end Scenario DY1042:
        1. Cannot execute proposed action before approval.
        2. Operator approves -> status='approved'.
        3. Execute runs simulated webhook replay (creating FIN-TXN9001 in finance_ledger),
           triggers a fresh verification reconciliation run, and transitions resolution to
           `verified_resolved` and discrepancy to `resolved`.
        4. Repeated execution requests are idempotent and do not repeat the simulated action
           or create duplicate verification runs.
        """
        _, findings = self.engine.execute_run(triggered_by="phase6_test")
        dy1042_disc = next(
            f
            for f in findings
            if f.order_id == "DY1042"
            and f.rule_code == "RULE_MISSING_FINANCE_SETTLEMENT"
        )

        _, proposed = self.inv_service.run_investigation(dy1042_disc.discrepancy_id)
        assert proposed is not None
        res_id = proposed.resolution_id

        # 1. Attempting to execute before approval raises ValueError
        with self.assertRaises(ValueError):
            self.res_service.execute_resolution(
                res_id, actor_id="op-1", actor_role="operator"
            )

        # 2. Operator approves the resolution
        approved = self.res_service.approve_resolution(
            res_id,
            actor_id="finance-lead-01",
            actor_role="operator",
            comment="Confirmed webhook DLQ timeout in logs; approved replay.",
        )
        self.assertEqual(approved.status, ResolutionStatus.APPROVED)

        # 3. Execute the approved resolution
        executed = self.res_service.execute_resolution(
            res_id,
            actor_id="finance-lead-01",
            actor_role="operator",
        )
        self.assertEqual(executed.status, ResolutionStatus.VERIFIED_RESOLVED)
        self.assertIsNotNone(executed.verification_run_id)
        self.assertEqual(
            executed.execution_result["created_finance_ledger_transaction_id"],
            "FIN-TXN9001",
        )

        # Confirm FIN-TXN9001 now exists in finance_ledger
        fin_entry = self.records_repo.get_payment("FIN-TXN9001")
        self.assertIsNotNone(fin_entry)

        # Confirm discrepancy status is now RESOLVED
        resolved_disc = self.recon_repo.get_discrepancy(dy1042_disc.discrepancy_id)
        assert resolved_disc is not None
        self.assertEqual(resolved_disc.status, DiscrepancyStatus.RESOLVED)

        # 4. Repeated execution request is idempotent (same verification_run_id, no extra audit events)
        first_ver_run_id = executed.verification_run_id
        audits_before = len(
            self.audit_repo.list_events_for_entity("resolution_action", res_id)
        )
        repeated = self.res_service.execute_resolution(
            res_id,
            actor_id="finance-lead-01",
            actor_role="operator",
        )
        self.assertEqual(repeated.status, ResolutionStatus.VERIFIED_RESOLVED)
        self.assertEqual(repeated.verification_run_id, first_ver_run_id)
        audits_after = len(
            self.audit_repo.list_events_for_entity("resolution_action", res_id)
        )
        self.assertEqual(audits_before, audits_after)

    def test_rejected_action_cannot_execute(self) -> None:
        """Rejected resolution actions transition to `rejected` and cannot be executed."""
        _, findings = self.engine.execute_run(triggered_by="reject_test")
        dy1047_disc = next(
            f for f in findings if f.order_id == "DY1047"
        )
        _, proposed = self.inv_service.run_investigation(dy1047_disc.discrepancy_id)
        assert proposed is not None

        rejected = self.res_service.reject_resolution(
            proposed.resolution_id,
            actor_id="auditor-op",
            actor_role="operator",
            comment="Warehouse physical count pending; do not auto-sync yet.",
        )
        self.assertEqual(rejected.status, ResolutionStatus.REJECTED)

        with self.assertRaises(ValueError):
            self.res_service.execute_resolution(
                proposed.resolution_id,
                actor_id="auditor-op",
                actor_role="operator",
            )

    def test_post_resolution_verification_detects_unresolved_discrepancy(self) -> None:
        """If a simulated action does not fix the underlying discrepancy, post-resolution verification marks it `verification_failed`."""
        _, findings = self.engine.execute_run(triggered_by="ver_fail_test")
        dy1042_disc = next(
            f
            for f in findings
            if f.order_id == "DY1042"
            and f.rule_code == "RULE_MISSING_FINANCE_SETTLEMENT"
        )
        _, proposed = self.inv_service.run_investigation(dy1042_disc.discrepancy_id)
        assert proposed is not None

        self.res_service.approve_resolution(
            proposed.resolution_id,
            actor_id="op-1",
            actor_role="operator",
        )

        # Execute with simulate_noop=True so the finance ledger remains missing
        failed_ver = self.res_service.execute_resolution(
            proposed.resolution_id,
            actor_id="op-1",
            actor_role="operator",
            simulate_noop=True,
        )
        self.assertEqual(failed_ver.status, ResolutionStatus.VERIFICATION_FAILED)
        self.assertEqual(
            failed_ver.execution_result["verification_status"], "still_unresolved"
        )

        # Discrepancy must NOT be marked resolved
        disc_after = self.recon_repo.get_discrepancy(dy1042_disc.discrepancy_id)
        assert disc_after is not None
        self.assertNotEqual(disc_after.status, DiscrepancyStatus.RESOLVED)


if __name__ == "__main__":
    unittest.main()
