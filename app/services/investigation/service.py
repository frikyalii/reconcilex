"""Orchestration service for running and retrying LangGraph discrepancy investigations."""

from __future__ import annotations

from typing import Optional, Tuple

from app.agents.graph import InvestigationWorkflowGraph
from app.agents.prompts.templates import (
    InvestigationModelProvider,
    build_configured_model_provider,
)
from app.db.session import DatabaseSession
from app.models.enums import DiscrepancyStatus, InvestigationStatus
from app.observability.metrics import metrics_registry
from app.repositories.audit_repo import AuditRepository
from app.repositories.investigation_repo import InvestigationRepository
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.schemas.domain import InvestigationRead, ResolutionActionRead


class InvestigationService:
    """Manages the lifecycle of discrepancy investigations and validated resolution proposals."""

    def __init__(
        self,
        session: DatabaseSession,
        model_provider: Optional[InvestigationModelProvider] = None,
    ) -> None:
        self._session = session
        self._recon_repo = ReconciliationRepository(session)
        self._inv_repo = InvestigationRepository(session)
        self._audit_repo = AuditRepository(session)
        self._model_provider = model_provider or build_configured_model_provider()

    def run_investigation(
        self,
        discrepancy_id: str,
        actor_id: str = "system",
        actor_role: str = "system",
    ) -> Tuple[InvestigationRead, Optional[ResolutionActionRead]]:
        """Create and execute a LangGraph investigation for a persisted discrepancy."""
        disc = self._recon_repo.get_discrepancy(discrepancy_id)
        if disc is None:
            raise KeyError(f"Discrepancy '{discrepancy_id}' not found.")

        inv = self._inv_repo.create_investigation(
            discrepancy_id=discrepancy_id,
            llm_model_id=self._model_provider.model_id,
        )
        return self._execute_workflow_for_investigation(
            investigation_id=inv.investigation_id,
            actor_id=actor_id,
            actor_role=actor_role,
        )

    def retry_investigation(
        self,
        investigation_id: str,
        actor_id: str = "operator",
        actor_role: str = "operator",
    ) -> Tuple[InvestigationRead, Optional[ResolutionActionRead]]:
        """Retry an existing failed or inconclusive investigation."""
        inv = self._inv_repo.get_investigation(investigation_id)
        if inv is None:
            raise KeyError(f"Investigation '{investigation_id}' not found.")
        return self._execute_workflow_for_investigation(
            investigation_id=investigation_id,
            actor_id=actor_id,
            actor_role=actor_role,
        )

    def _execute_workflow_for_investigation(
        self,
        investigation_id: str,
        actor_id: str,
        actor_role: str,
    ) -> Tuple[InvestigationRead, Optional[ResolutionActionRead]]:
        inv = self._inv_repo.get_investigation(investigation_id)
        assert inv is not None
        disc = self._recon_repo.get_discrepancy(inv.discrepancy_id)
        assert disc is not None

        self._recon_repo.update_discrepancy_status(
            disc.discrepancy_id, DiscrepancyStatus.INVESTIGATING
        )
        self._inv_repo.update_investigation(
            investigation_id=investigation_id,
            status=InvestigationStatus.RUNNING,
        )

        graph = InvestigationWorkflowGraph(
            session=self._session,
            model_provider=self._model_provider,
        )
        final_state = graph.run(investigation_id=investigation_id, discrepancy=disc)

        inv_out = final_state.get("investigator_output")
        plan_out = final_state.get("planner_output")
        val_res = final_state.get("validation_result")
        assert val_res is not None

        proposed_resolution: Optional[ResolutionActionRead] = None

        if inv_out is not None:
            updated_inv = self._inv_repo.update_investigation(
                investigation_id=investigation_id,
                status=val_res.final_status,
                summary=inv_out.summary,
                evidence_references=inv_out.evidence_references,
                candidate_causes=[c.dict() for c in inv_out.candidate_causes],
                confidence_score=inv_out.confidence_score,
                uncertainty_explanation=inv_out.uncertainty_explanation,
                missing_evidence=inv_out.missing_evidence,
                next_diagnostic_step=inv_out.next_diagnostic_step,
                validation_passed=val_res.passed,
                validation_errors=val_res.errors,
                llm_model_id=final_state.get("llm_model_id"),
                latency_ms=final_state.get("latency_ms"),
            )
        else:
            updated_inv = self._inv_repo.update_investigation(
                investigation_id=investigation_id,
                status=InvestigationStatus.FAILED,
                validation_passed=False,
                validation_errors=val_res.errors,
            )

        if (
            val_res.passed
            and val_res.final_status == InvestigationStatus.COMPLETED
            and plan_out is not None
        ):
            proposed_resolution = self._inv_repo.create_proposed_resolution(
                discrepancy_id=disc.discrepancy_id,
                investigation_id=investigation_id,
                action_type=plan_out.action_type,
                rationale=plan_out.rationale,
                action_payload=plan_out.action_payload,
                evidence_references=plan_out.supporting_evidence_ids,
                requires_approval=plan_out.requires_approval,
                proposed_by=f"agent:{final_state.get('llm_model_id', 'grounded')}",
                idempotency_key=f"prop-{investigation_id}",
            )
            self._recon_repo.update_discrepancy_status(
                disc.discrepancy_id, DiscrepancyStatus.PENDING_APPROVAL
            )

        metrics_registry.inc_counter(
            "reconcilex_investigations_total",
            status=updated_inv.status.value,
            validation_passed=str(updated_inv.validation_passed).lower(),
        )
        self._audit_repo.record_event(
            entity_type="investigation",
            entity_id=investigation_id,
            action="investigation_workflow_completed",
            actor_id=actor_id,
            actor_role=actor_role,
            after_state={
                "discrepancy_id": disc.discrepancy_id,
                "status": updated_inv.status.value,
                "validation_passed": updated_inv.validation_passed,
                "proposed_resolution_id": (
                    proposed_resolution.resolution_id if proposed_resolution else None
                ),
            },
        )
        return updated_inv, proposed_resolution
