"""Parameterized repository for Investigations and proposed ResolutionActions."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.base import format_decimal_db, utcnow_iso
from app.db.session import DatabaseSession
from app.models.enums import (
    InvestigationStatus,
    ResolutionActionType,
    ResolutionStatus,
)
from app.schemas.domain import InvestigationRead, ResolutionActionRead


class InvestigationRepository:
    """Data access layer for `investigations` and proposed `resolution_actions`."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def create_investigation(
        self,
        discrepancy_id: str,
        llm_model_id: Optional[str] = None,
    ) -> InvestigationRead:
        now = utcnow_iso()
        investigation_id = str(uuid.uuid4())
        self._session.execute(
            """
            INSERT INTO investigations (
                investigation_id, discrepancy_id, status, summary,
                evidence_references_json, candidate_causes_json,
                confidence_score, uncertainty_explanation,
                missing_evidence_json, next_diagnostic_step,
                validation_passed, validation_errors_json,
                llm_model_id, latency_ms, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                investigation_id,
                discrepancy_id,
                InvestigationStatus.PENDING.value,
                None,
                json.dumps([]),
                json.dumps([]),
                None,
                None,
                json.dumps([]),
                None,
                0,
                json.dumps([]),
                llm_model_id,
                None,
                now,
                now,
            ),
        )
        created = self.get_investigation(investigation_id)
        assert created is not None
        return created

    def update_investigation(
        self,
        investigation_id: str,
        status: InvestigationStatus,
        summary: Optional[str] = None,
        evidence_references: Optional[List[str]] = None,
        candidate_causes: Optional[List[Dict[str, Any]]] = None,
        confidence_score: Optional[Decimal] = None,
        uncertainty_explanation: Optional[str] = None,
        missing_evidence: Optional[List[str]] = None,
        next_diagnostic_step: Optional[str] = None,
        validation_passed: bool = False,
        validation_errors: Optional[List[str]] = None,
        llm_model_id: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> InvestigationRead:
        current = self.get_investigation(investigation_id)
        if current is None:
            raise KeyError(f"Investigation '{investigation_id}' not found.")

        now = utcnow_iso()
        conf_str = (
            format_decimal_db(confidence_score)
            if confidence_score is not None
            else (
                format_decimal_db(current.confidence_score)
                if current.confidence_score is not None
                else None
            )
        )
        ev_refs = (
            evidence_references
            if evidence_references is not None
            else current.evidence_references
        )
        causes = (
            candidate_causes
            if candidate_causes is not None
            else current.candidate_causes
        )
        missing_ev = (
            missing_evidence
            if missing_evidence is not None
            else current.missing_evidence
        )
        val_errs = (
            validation_errors
            if validation_errors is not None
            else current.validation_errors
        )

        self._session.execute(
            """
            UPDATE investigations
            SET status = ?,
                summary = ?,
                evidence_references_json = ?,
                candidate_causes_json = ?,
                confidence_score = ?,
                uncertainty_explanation = ?,
                missing_evidence_json = ?,
                next_diagnostic_step = ?,
                validation_passed = ?,
                validation_errors_json = ?,
                llm_model_id = ?,
                latency_ms = ?,
                updated_at = ?
            WHERE investigation_id = ?
            """,
            (
                status.value,
                summary if summary is not None else current.summary,
                json.dumps(ev_refs),
                json.dumps(causes),
                conf_str,
                (
                    uncertainty_explanation
                    if uncertainty_explanation is not None
                    else current.uncertainty_explanation
                ),
                json.dumps(missing_ev),
                (
                    next_diagnostic_step
                    if next_diagnostic_step is not None
                    else current.next_diagnostic_step
                ),
                1 if validation_passed else 0,
                json.dumps(val_errs),
                llm_model_id if llm_model_id is not None else current.llm_model_id,
                latency_ms if latency_ms is not None else current.latency_ms,
                now,
                investigation_id,
            ),
        )
        updated = self.get_investigation(investigation_id)
        assert updated is not None
        return updated

    def get_investigation(self, investigation_id: str) -> Optional[InvestigationRead]:
        row = self._session.fetchone(
            "SELECT * FROM investigations WHERE investigation_id = ?",
            (investigation_id,),
        )
        if row is None:
            return None
        return self._row_to_investigation(row)

    def list_investigations_for_discrepancy(
        self, discrepancy_id: str
    ) -> List[InvestigationRead]:
        rows = self._session.fetchall(
            """
            SELECT * FROM investigations
            WHERE discrepancy_id = ?
            ORDER BY created_at DESC
            """,
            (discrepancy_id,),
        )
        return [self._row_to_investigation(r) for r in rows]

    @staticmethod
    def _row_to_investigation(row: Dict[str, Any]) -> InvestigationRead:
        conf_val = row["confidence_score"]
        return InvestigationRead(
            investigation_id=row["investigation_id"],
            discrepancy_id=row["discrepancy_id"],
            status=InvestigationStatus(row["status"]),
            summary=row["summary"],
            evidence_references=json.loads(row["evidence_references_json"]),
            candidate_causes=json.loads(row["candidate_causes_json"]),
            confidence_score=Decimal(conf_val) if conf_val is not None else None,
            uncertainty_explanation=row["uncertainty_explanation"],
            missing_evidence=json.loads(row["missing_evidence_json"]),
            next_diagnostic_step=row["next_diagnostic_step"],
            validation_passed=bool(row["validation_passed"]),
            validation_errors=json.loads(row["validation_errors_json"]),
            llm_model_id=row["llm_model_id"],
            latency_ms=int(row["latency_ms"]) if row["latency_ms"] is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_proposed_resolution(
        self,
        discrepancy_id: str,
        investigation_id: Optional[str],
        action_type: ResolutionActionType,
        rationale: str,
        action_payload: Dict[str, Any],
        evidence_references: List[str],
        requires_approval: bool = True,
        proposed_by: str = "agent",
        idempotency_key: Optional[str] = None,
    ) -> ResolutionActionRead:
        if idempotency_key:
            existing_row = self._session.fetchone(
                "SELECT * FROM resolution_actions WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            if existing_row is not None:
                return self._row_to_resolution(existing_row)

        now = utcnow_iso()
        resolution_id = str(uuid.uuid4())
        self._session.execute(
            """
            INSERT INTO resolution_actions (
                resolution_id, discrepancy_id, investigation_id, idempotency_key,
                action_type, status, requires_approval, proposed_by, rationale,
                action_payload_json, evidence_references_json, execution_result_json,
                verification_run_id, executed_at, verified_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution_id,
                discrepancy_id,
                investigation_id,
                idempotency_key,
                action_type.value,
                ResolutionStatus.PROPOSED.value,
                1 if requires_approval else 0,
                proposed_by,
                rationale,
                json.dumps(action_payload),
                json.dumps(evidence_references),
                json.dumps({}),
                None,
                None,
                None,
                now,
                now,
            ),
        )
        row = self._session.fetchone(
            "SELECT * FROM resolution_actions WHERE resolution_id = ?",
            (resolution_id,),
        )
        assert row is not None
        return self._row_to_resolution(row)

    def list_resolutions_for_discrepancy(
        self, discrepancy_id: str
    ) -> List[ResolutionActionRead]:
        rows = self._session.fetchall(
            "SELECT * FROM resolution_actions WHERE discrepancy_id = ? ORDER BY created_at DESC",
            (discrepancy_id,),
        )
        return [self._row_to_resolution(r) for r in rows]

    @staticmethod
    def _row_to_resolution(row: Dict[str, Any]) -> ResolutionActionRead:
        return ResolutionActionRead(
            resolution_id=row["resolution_id"],
            discrepancy_id=row["discrepancy_id"],
            investigation_id=row["investigation_id"],
            idempotency_key=row["idempotency_key"],
            action_type=ResolutionActionType(row["action_type"]),
            status=ResolutionStatus(row["status"]),
            requires_approval=bool(row["requires_approval"]),
            proposed_by=row["proposed_by"],
            rationale=row["rationale"],
            action_payload=json.loads(row["action_payload_json"]),
            evidence_references=json.loads(row["evidence_references_json"]),
            execution_result=json.loads(row["execution_result_json"]),
            verification_run_id=row["verification_run_id"],
            executed_at=row["executed_at"],
            verified_at=row["verified_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
