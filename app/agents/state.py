"""Typed state and structured Pydantic outputs for the LangGraph investigation workflow.

Enforces strict separation between deterministic facts and agentic hypotheses:
- Every candidate root cause and proposed resolution MUST cite concrete `evidence_ids`
  that were retrieved by the `EvidenceCollector` or `HistoricalIncidentRetriever`.
- Semantic similarity from runbooks/historical incidents is explicitly tagged with
  `is_advisory_context_only=True` and cannot be used as proof of a transaction match.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, TypedDict

from pydantic import Field, validator

from app.models.enums import InvestigationStatus, ResolutionActionType
from app.schemas.common import DecimalSafeBaseModel, validate_non_float_decimal
from app.schemas.domain import DiscrepancyRead


class EvidenceItem(DecimalSafeBaseModel):
    """A single verifiable piece of evidence collected from a database table or log."""

    evidence_id: str = Field(
        ...,
        description="Canonical evidence identifier (e.g. 'ORDER:DY1042', 'PAYMENT:TXN9001', 'EVENT_LOG:EVT-DY1042-WH-3')",
    )
    source_type: str = Field(
        ...,
        description="Category of evidence ('order', 'payment_gateway', 'finance_ledger', 'refund', 'inventory', 'event_log')",
    )
    summary: str
    data: Dict[str, Any] = Field(default_factory=dict)


class HistoricalIncidentMatch(DecimalSafeBaseModel):
    """Runbook or historical incident retrieved via Qdrant vector search or rule-tag lookup."""

    incident_id: str = Field(..., description="e.g. 'RUNBOOK:RB-WEBHOOK-DLQ-01'")
    title: str
    discrepancy_type: str
    root_cause_pattern: str
    recommended_action_type: ResolutionActionType
    similarity_score: Decimal = Decimal("0.9000")
    is_advisory_context_only: bool = Field(
        default=True,
        description="Advisory context only — semantic similarity is NEVER proof that two transactions are identical",
    )


class CandidateRootCause(DecimalSafeBaseModel):
    """A single root-cause hypothesis backed by explicit evidence citations."""

    cause_code: str = Field(..., min_length=1, max_length=64)
    hypothesis: str = Field(..., min_length=1)
    likelihood: str = Field(default="high", description="'high' | 'medium' | 'low'")
    supporting_evidence_ids: List[str] = Field(default_factory=list)


class InvestigatorStructuredOutput(DecimalSafeBaseModel):
    """Structured output produced by the RootCauseInvestigator node."""

    is_inconclusive: bool = False
    summary: str
    evidence_references: List[str] = Field(default_factory=list)
    candidate_causes: List[CandidateRootCause] = Field(default_factory=list)
    confidence_score: Decimal = Decimal("0.0000")
    uncertainty_explanation: str
    missing_evidence: List[str] = Field(default_factory=list)
    next_diagnostic_step: str

    _validate_conf = validator("confidence_score", pre=True, allow_reuse=True)(
        validate_non_float_decimal
    )


class PlannerStructuredOutput(DecimalSafeBaseModel):
    """Structured output produced by the ResolutionPlanner node."""

    action_type: ResolutionActionType
    requires_approval: bool = True
    rationale: str
    action_payload: Dict[str, Any] = Field(default_factory=dict)
    supporting_evidence_ids: List[str] = Field(default_factory=list)


class ValidationResultOutput(DecimalSafeBaseModel):
    """Result produced by the RecommendationValidator node."""

    passed: bool
    final_status: InvestigationStatus
    errors: List[str] = Field(default_factory=list)


class InvestigationGraphState(TypedDict, total=False):
    """Typed state flowing through the LangGraph discrepancy investigation workflow."""

    investigation_id: str
    discrepancy: DiscrepancyRead
    collected_evidence: List[EvidenceItem]
    available_evidence_ids: List[str]
    historical_incidents: List[HistoricalIncidentMatch]
    investigator_output: Optional[InvestigatorStructuredOutput]
    planner_output: Optional[PlannerStructuredOutput]
    validation_result: Optional[ValidationResultOutput]
    llm_model_id: str
    latency_ms: int
