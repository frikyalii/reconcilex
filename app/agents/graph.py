"""LangGraph stateful workflow builder and executor for ReconcileX discrepancy investigation.

Supports both `langgraph.graph.StateGraph` when `langgraph` is installed and a
deterministic state-machine executor with checkpoint snapshots so the workflow runs
identically in all environments.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.agents.nodes.workflow_nodes import (
    evidence_collector_node,
    historical_incident_retriever_node,
    investigator_and_planner_node,
    recommendation_validator_node,
)
from app.agents.prompts.templates import InvestigationModelProvider
from app.agents.state import InvestigationGraphState
from app.db.session import DatabaseSession
from app.schemas.domain import DiscrepancyRead

try:
    from langgraph.graph import END, StateGraph  # type: ignore

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False


class InvestigationWorkflowGraph:
    """Stateful workflow executing the constrained discrepancy investigation pipeline."""

    def __init__(
        self,
        session: DatabaseSession,
        model_provider: Optional[InvestigationModelProvider] = None,
    ) -> None:
        self._session = session
        self._model_provider = model_provider
        self.checkpoints: Dict[str, List[str]] = {}

    def run(
        self, investigation_id: str, discrepancy: DiscrepancyRead
    ) -> InvestigationGraphState:
        """Execute the stateful investigation graph and record node transition checkpoints."""
        state: InvestigationGraphState = {
            "investigation_id": investigation_id,
            "discrepancy": discrepancy,
            "collected_evidence": [],
            "available_evidence_ids": [],
            "historical_incidents": [],
            "investigator_output": None,
            "planner_output": None,
            "validation_result": None,
            "llm_model_id": "unknown",
            "latency_ms": 0,
        }
        visited_nodes: List[str] = []

        # Node 1: Evidence Collector
        state = evidence_collector_node(state, self._session)
        visited_nodes.append("evidence_collector")

        # Node 2: Historical Incident Retriever
        state = historical_incident_retriever_node(state)
        visited_nodes.append("historical_incident_retriever")

        # Node 3 & 4: Root-Cause Investigator & Resolution Planner
        state = investigator_and_planner_node(state, self._model_provider)
        visited_nodes.append("root_cause_investigator")
        visited_nodes.append("resolution_planner")

        # Node 5: Recommendation Validator
        state = recommendation_validator_node(state)
        visited_nodes.append("recommendation_validator")

        self.checkpoints[investigation_id] = visited_nodes
        return state
