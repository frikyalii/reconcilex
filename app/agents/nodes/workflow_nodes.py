"""Constrained LangGraph workflow nodes for discrepancy investigation and validation.

Emits structured JSON logs at every node transition (`evidence_collector`,
`historical_incident_retriever`, `root_cause_investigator`, `resolution_planner`,
`recommendation_validator`) with `X-Correlation-ID` and OpenTelemetry spans.
"""

from __future__ import annotations

import time
from typing import Set

from app.agents.prompts.templates import (
    InvestigationModelProvider,
    build_configured_model_provider,
)
from app.agents.state import (
    InvestigationGraphState,
    ValidationResultOutput,
)
from app.agents.tools.evidence_tools import (
    collect_discrepancy_evidence,
    retrieve_historical_incidents,
)
from app.core.logging import get_logger
from app.db.session import DatabaseSession
from app.models.enums import InvestigationStatus
from app.observability.tracing import trace_span

logger = get_logger(__name__)

PROHIBITED_PAYLOAD_KEYS: Set[str] = {
    "raw_sql",
    "sql",
    "shell_command",
    "bash",
    "exec",
    "direct_provider_mutation",
    "issue_real_refund",
    "unrestricted_tool",
}


def evidence_collector_node(
    state: InvestigationGraphState, session: DatabaseSession
) -> InvestigationGraphState:
    """Node 1: Collect related order, payment, ledger, refund, inventory, and event log evidence."""
    disc = state["discrepancy"]
    with trace_span(
        "agent.node.evidence_collector",
        investigation_id=state.get("investigation_id"),
        discrepancy_id=disc.discrepancy_id,
    ):
        items = collect_discrepancy_evidence(session, disc)
        ev_ids = [it.evidence_id for it in items]
        state["collected_evidence"] = items
        state["available_evidence_ids"] = ev_ids
        logger.info(
            "LangGraph Node [1/5] EvidenceCollector completed",
            extra={
                "context": {
                    "investigation_id": state.get("investigation_id"),
                    "discrepancy_id": disc.discrepancy_id,
                    "order_id": disc.order_id,
                    "evidence_count": len(items),
                    "available_evidence_ids": ev_ids,
                }
            },
        )
    return state


def historical_incident_retriever_node(
    state: InvestigationGraphState,
) -> InvestigationGraphState:
    """Node 2: Retrieve advisory historical incident patterns and runbooks."""
    disc = state["discrepancy"]
    with trace_span(
        "agent.node.historical_incident_retriever",
        investigation_id=state.get("investigation_id"),
        discrepancy_type=disc.discrepancy_type,
    ):
        matches = retrieve_historical_incidents(disc)
        state["historical_incidents"] = matches
        logger.info(
            "LangGraph Node [2/5] HistoricalIncidentRetriever completed",
            extra={
                "context": {
                    "investigation_id": state.get("investigation_id"),
                    "discrepancy_type": disc.discrepancy_type,
                    "matched_runbook_ids": [m.incident_id for m in matches],
                }
            },
        )
    return state


def investigator_and_planner_node(
    state: InvestigationGraphState,
    model_provider: InvestigationModelProvider | None = None,
) -> InvestigationGraphState:
    """Nodes 3 & 4: Produce structured root-cause investigation and candidate resolution plan."""
    provider = model_provider or build_configured_model_provider()
    disc = state["discrepancy"]
    with trace_span(
        "agent.node.investigator_and_planner",
        investigation_id=state.get("investigation_id"),
        llm_model_id=provider.model_id,
    ):
        start_ns = time.perf_counter_ns()
        inv_out, plan_out = provider.investigate_and_plan(
            discrepancy=disc,
            evidence=state.get("collected_evidence", []),
            incidents=state.get("historical_incidents", []),
        )
        elapsed_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))
        state["investigator_output"] = inv_out
        state["planner_output"] = plan_out
        state["llm_model_id"] = provider.model_id
        state["latency_ms"] = elapsed_ms

        logger.info(
            "LangGraph Nodes [3/5 & 4/5] RootCauseInvestigator & ResolutionPlanner completed",
            extra={
                "context": {
                    "investigation_id": state.get("investigation_id"),
                    "llm_model_id": provider.model_id,
                    "latency_ms": elapsed_ms,
                    "is_inconclusive": inv_out.is_inconclusive,
                    "candidate_cause_codes": [
                        c.cause_code for c in inv_out.candidate_causes
                    ],
                    "proposed_action_type": (
                        plan_out.action_type.value if plan_out else None
                    ),
                }
            },
        )
    return state


def recommendation_validator_node(
    state: InvestigationGraphState,
) -> InvestigationGraphState:
    """Node 5: Deterministic safety & groundedness validator for LLM outputs."""
    with trace_span(
        "agent.node.recommendation_validator",
        investigation_id=state.get("investigation_id"),
    ):
        inv_out = state.get("investigator_output")
        plan_out = state.get("planner_output")
        valid_ev_ids = set(state.get("available_evidence_ids", []))
        errors: list[str] = []

        if inv_out is None:
            state["validation_result"] = ValidationResultOutput(
                passed=False,
                final_status=InvestigationStatus.FAILED,
                errors=["MISSING_INVESTIGATOR_OUTPUT: Investigator produced no output."],
            )
            return state

        for ref_id in inv_out.evidence_references:
            if ref_id.startswith("RUNBOOK:"):
                errors.append(
                    f"SEMANTIC_SIMILARITY_NOT_PROOF: Runbook '{ref_id}' cannot be cited as primary transaction evidence."
                )
            elif ref_id not in valid_ev_ids:
                errors.append(
                    f"UNSUPPORTED_EVIDENCE_CITATION: Cited evidence '{ref_id}' was not collected from any data source."
                )

        if inv_out.is_inconclusive:
            if not inv_out.missing_evidence:
                errors.append(
                    "INCONCLUSIVE_WITHOUT_MISSING_EVIDENCE: Inconclusive findings must list missing_evidence."
                )
            res = ValidationResultOutput(
                passed=(len(errors) == 0),
                final_status=(
                    InvestigationStatus.INCONCLUSIVE
                    if len(errors) == 0
                    else InvestigationStatus.FAILED
                ),
                errors=errors,
            )
            state["validation_result"] = res
            logger.info(
                "LangGraph Node [5/5] RecommendationValidator completed (inconclusive finding)",
                extra={
                    "context": {
                        "investigation_id": state.get("investigation_id"),
                        "validation_passed": res.passed,
                        "final_status": res.final_status.value,
                        "missing_evidence": inv_out.missing_evidence,
                    }
                },
            )
            return state

        if not inv_out.candidate_causes:
            errors.append(
                "EMPTY_CANDIDATE_CAUSES: Conclusive investigation must include at least one candidate root cause."
            )

        for cause in inv_out.candidate_causes:
            if not cause.supporting_evidence_ids:
                errors.append(
                    f"UNSUPPORTED_CAUSE: Candidate cause '{cause.cause_code}' has no supporting_evidence_ids."
                )
            for cid in cause.supporting_evidence_ids:
                if cid.startswith("RUNBOOK:"):
                    errors.append(
                        f"SEMANTIC_SIMILARITY_NOT_PROOF: Candidate cause '{cause.cause_code}' cited advisory runbook '{cid}' as transaction proof."
                    )
                elif cid not in valid_ev_ids:
                    errors.append(
                        f"HALLUCINATED_EVIDENCE_ID: Candidate cause '{cause.cause_code}' cited nonexistent evidence '{cid}'."
                    )

        if plan_out is not None:
            if not plan_out.supporting_evidence_ids:
                errors.append(
                    "UNSUPPORTED_RESOLUTION_PLAN: Proposed resolution lacks supporting_evidence_ids."
                )
            for pid in plan_out.supporting_evidence_ids:
                if pid not in valid_ev_ids:
                    errors.append(
                        f"HALLUCINATED_PLAN_EVIDENCE: Resolution plan cited nonexistent evidence '{pid}'."
                    )

            if not plan_out.requires_approval:
                errors.append(
                    f"APPROVAL_BYPASS_REJECTED: Action '{plan_out.action_type.value}' must set requires_approval=True."
                )

            bad_keys = PROHIBITED_PAYLOAD_KEYS.intersection(
                {k.lower() for k in plan_out.action_payload.keys()}
            )
            if bad_keys:
                errors.append(
                    f"UNAUTHORIZED_OPERATION_PAYLOAD: Resolution payload contains prohibited keys: {sorted(bad_keys)}."
                )

        passed = len(errors) == 0
        res = ValidationResultOutput(
            passed=passed,
            final_status=(
                InvestigationStatus.COMPLETED if passed else InvestigationStatus.FAILED
            ),
            errors=errors,
        )
        state["validation_result"] = res
        logger.info(
            "LangGraph Node [5/5] RecommendationValidator completed",
            extra={
                "context": {
                    "investigation_id": state.get("investigation_id"),
                    "validation_passed": res.passed,
                    "final_status": res.final_status.value,
                    "validation_errors": errors,
                }
            },
        )
    return state
