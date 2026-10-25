"""Workflow tests for Phase 5: LangGraph Discrepancy Investigation, Evidence Citations, Hallucination Rejection, and Inconclusive Handling."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple
import unittest

from app.agents.state import (
    CandidateRootCause,
    EvidenceItem,
    HistoricalIncidentMatch,
    InvestigatorStructuredOutput,
    PlannerStructuredOutput,
)
from app.core.config import Settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.models.enums import (
    DiscrepancyStatus,
    InvestigationStatus,
    OrderStatus,
    PaymentSourceSystem,
    PaymentStatus,
    ResolutionActionType,
    ResolutionStatus,
)
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.repositories.records_repo import RecordsRepository
from app.schemas.domain import DiscrepancyRead, OrderCreate, PaymentCreate
from app.services.investigation.service import InvestigationService
from app.services.reconciliation.engine import ReconciliationEngine
from scripts.seed_synthetic import seed_all_synthetic_data


class _HallucinatingModelProvider:
    """Mock LLM provider that cites nonexistent evidence IDs and attempts an unauthorized SQL payload."""

    model_id = "mock-hallucinating-llm"

    def investigate_and_plan(
        self,
        discrepancy: DiscrepancyRead,
        evidence: List[EvidenceItem],
        incidents: List[HistoricalIncidentMatch],
    ) -> Tuple[InvestigatorStructuredOutput, Optional[PlannerStructuredOutput]]:
        return (
            InvestigatorStructuredOutput(
                is_inconclusive=False,
                summary="Invented root cause citing fabricated evidence and runbook as transaction proof.",
                evidence_references=["PAYMENT:TXN_DOES_NOT_EXIST_999", "RUNBOOK:RB-WEBHOOK-DLQ-01"],
                candidate_causes=[
                    CandidateRootCause(
                        cause_code="HALLUCINATED_CAUSE",
                        hypothesis="Database corruption invented by LLM.",
                        likelihood="high",
                        supporting_evidence_ids=["PAYMENT:TXN_DOES_NOT_EXIST_999"],
                    )
                ],
                confidence_score=Decimal("0.9900"),
                uncertainty_explanation="None",
                missing_evidence=[],
                next_diagnostic_step="Run raw SQL drop.",
            ),
            PlannerStructuredOutput(
                action_type=ResolutionActionType.RETRY_WEBHOOK_SYNC,
                requires_approval=False,  # Unauthorized attempt to bypass human approval
                rationale="Execute raw SQL without operator approval.",
                action_payload={"raw_sql": "DELETE FROM payments;"},
                supporting_evidence_ids=["PAYMENT:TXN_DOES_NOT_EXIST_999"],
            ),
        )


class TestInvestigationWorkflow(unittest.TestCase):
    """Verify LangGraph evidence collection, grounded hypothesis acceptance, hallucination rejection, and inconclusive fallback."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.settings = Settings(DATABASE_URL="sqlite:///:memory:")
        self.session = create_session_for_settings(self.settings)
        self.records_repo = RecordsRepository(self.session)
        self.recon_repo = ReconciliationRepository(self.session)
        self.engine = ReconciliationEngine(self.session)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_dy1042_evidence_supported_hypothesis_accepted_and_proposes_resolution(self) -> None:
        """Primary Scenario DY1042: LangGraph collects order, gateway payment TXN9001, and 3 webhook failure logs, validates citations, and proposes RETRY_WEBHOOK_SYNC."""
        seed_all_synthetic_data(self.session)
        run, findings = self.engine.execute_run(triggered_by="workflow_test")

        dy1042_disc = next(
            f
            for f in findings
            if f.order_id == "DY1042"
            and f.rule_code == "RULE_MISSING_FINANCE_SETTLEMENT"
        )

        inv_service = InvestigationService(self.session)
        investigation, proposed_action = inv_service.run_investigation(
            dy1042_disc.discrepancy_id
        )

        self.assertEqual(investigation.status, InvestigationStatus.COMPLETED)
        self.assertTrue(investigation.validation_passed)
        self.assertEqual(len(investigation.validation_errors), 0)

        # Must cite actual collected evidence IDs for DY1042, TXN9001, and the 3 failed webhook logs
        self.assertIn("ORDER:DY1042", investigation.evidence_references)
        self.assertIn("PAYMENT:TXN9001", investigation.evidence_references)
        self.assertIn("EVENT_LOG:EVT-DY1042-WH-1", investigation.evidence_references)
        self.assertIn("EVENT_LOG:EVT-DY1042-WH-2", investigation.evidence_references)
        self.assertIn("EVENT_LOG:EVT-DY1042-WH-3", investigation.evidence_references)

        self.assertEqual(len(investigation.candidate_causes), 1)
        self.assertEqual(
            investigation.candidate_causes[0]["cause_code"],
            "WEBHOOK_DELIVERY_FAILURE_DLQ",
        )

        # Verify proposed resolution action was created in `proposed` status requiring human approval
        self.assertIsNotNone(proposed_action)
        assert proposed_action is not None
        self.assertEqual(
            proposed_action.action_type, ResolutionActionType.RETRY_WEBHOOK_SYNC
        )
        self.assertEqual(proposed_action.status, ResolutionStatus.PROPOSED)
        self.assertTrue(proposed_action.requires_approval)

        # Verify discrepancy transitioned to `pending_approval`
        updated_disc = self.recon_repo.get_discrepancy(dy1042_disc.discrepancy_id)
        assert updated_disc is not None
        self.assertEqual(updated_disc.status, DiscrepancyStatus.PENDING_APPROVAL)

    def test_unsupported_claims_and_unauthorized_payloads_are_rejected(self) -> None:
        """RecommendationValidator must reject hallucinated evidence citations, runbook-as-proof citations, approval bypasses, and raw_sql payloads."""
        seed_all_synthetic_data(self.session)
        _, findings = self.engine.execute_run(triggered_by="validator_test")
        dy1042_disc = next(f for f in findings if f.order_id == "DY1042")

        adversarial_service = InvestigationService(
            self.session, model_provider=_HallucinatingModelProvider()
        )
        investigation, proposed_action = adversarial_service.run_investigation(
            dy1042_disc.discrepancy_id
        )

        self.assertEqual(investigation.status, InvestigationStatus.FAILED)
        self.assertFalse(investigation.validation_passed)
        self.assertIsNone(proposed_action)

        joined_errors = " | ".join(investigation.validation_errors)
        self.assertIn("UNSUPPORTED_EVIDENCE_CITATION", joined_errors)
        self.assertIn("SEMANTIC_SIMILARITY_NOT_PROOF", joined_errors)
        self.assertIn("HALLUCINATED_EVIDENCE_ID", joined_errors)
        self.assertIn("APPROVAL_BYPASS_REJECTED", joined_errors)
        self.assertIn("UNAUTHORIZED_OPERATION_PAYLOAD", joined_errors)

    def test_missing_webhook_evidence_produces_explicit_inconclusive_result(self) -> None:
        """When a missing finance settlement has no webhook failure logs in `event_logs`, the workflow returns `inconclusive` and lists `missing_evidence` instead of inventing a root cause."""
        self.records_repo.create_order(
            OrderCreate(
                order_id="DY_NO_LOGS",
                source_record_id="OMS-NO-LOGS",
                expected_amount=Decimal("70000.0000"),
                currency="INR",
                status=OrderStatus.CONFIRMED,
                event_timestamp="2026-10-24T01:00:00Z",
                updated_timestamp="2026-10-24T01:00:00Z",
            )
        )
        self.records_repo.create_payment(
            PaymentCreate(
                transaction_id="TXN_NO_LOGS",
                provider_charge_id="CHG_NO_LOGS",
                order_id="DY_NO_LOGS",
                source_record_id="PG-NO-LOGS",
                source_system=PaymentSourceSystem.PAYMENT_GATEWAY,
                amount=Decimal("70000.0000"),
                captured_amount=Decimal("70000.0000"),
                currency="INR",
                status=PaymentStatus.CAPTURED,
                event_timestamp="2026-10-24T01:01:00Z",
                updated_timestamp="2026-10-24T01:01:00Z",
            )
        )
        # Note: No finance_ledger record AND no event_logs for DY_NO_LOGS
        _, findings = self.engine.execute_run(triggered_by="inconclusive_test")
        disc = next(f for f in findings if f.order_id == "DY_NO_LOGS")

        inv_service = InvestigationService(self.session)
        investigation, proposed_action = inv_service.run_investigation(
            disc.discrepancy_id
        )

        self.assertEqual(investigation.status, InvestigationStatus.INCONCLUSIVE)
        self.assertTrue(investigation.validation_passed)
        self.assertIsNone(proposed_action)
        self.assertIn("webhook_delivery_event_logs", investigation.missing_evidence)


if __name__ == "__main__":
    unittest.main()
