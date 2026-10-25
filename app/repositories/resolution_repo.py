"""Parameterized repository for ResolutionActions and Approvals."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from app.db.base import utcnow_iso
from app.db.session import DatabaseSession
from app.models.enums import (
    ApprovalDecision,
    ResolutionActionType,
    ResolutionStatus,
    validate_resolution_transition,
)
from app.schemas.domain import (
    ApprovalRead,
    ResolutionActionCreate,
    ResolutionActionRead,
)


class ResolutionRepository:
    """Data access layer for `resolution_actions` and `approvals`."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def create_resolution(self, payload: ResolutionActionCreate) -> ResolutionActionRead:
        if payload.idempotency_key:
            existing = self.get_resolution_by_idempotency_key(payload.idempotency_key)
            if existing is not None:
                return existing

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
                payload.discrepancy_id,
                payload.investigation_id,
                payload.idempotency_key,
                payload.action_type.value,
                ResolutionStatus.PROPOSED.value,
                1 if payload.requires_approval else 0,
                payload.proposed_by,
                payload.rationale,
                json.dumps(payload.action_payload),
                json.dumps(payload.evidence_references),
                json.dumps({}),
                None,
                None,
                None,
                now,
                now,
            ),
        )
        created = self.get_resolution(resolution_id)
        assert created is not None
        return created

    def get_resolution(self, resolution_id: str) -> Optional[ResolutionActionRead]:
        row = self._session.fetchone(
            "SELECT * FROM resolution_actions WHERE resolution_id = ?",
            (resolution_id,),
        )
        if row is None:
            return None
        return self._row_to_resolution(row)

    def get_resolution_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[ResolutionActionRead]:
        row = self._session.fetchone(
            "SELECT * FROM resolution_actions WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        if row is None:
            return None
        return self._row_to_resolution(row)

    def update_resolution_state(
        self,
        resolution_id: str,
        target_status: ResolutionStatus,
        execution_result: Optional[Dict[str, Any]] = None,
        verification_run_id: Optional[str] = None,
        executed_at: Optional[str] = None,
        verified_at: Optional[str] = None,
    ) -> ResolutionActionRead:
        current = self.get_resolution(resolution_id)
        if current is None:
            raise KeyError(f"ResolutionAction '{resolution_id}' not found.")
        if current.status != target_status and not validate_resolution_transition(
            current.status, target_status
        ):
            raise ValueError(
                f"Invalid resolution status transition from '{current.status.value}' to '{target_status.value}'."
            )

        now = utcnow_iso()
        new_exec_res = (
            execution_result
            if execution_result is not None
            else current.execution_result
        )
        new_ver_run = (
            verification_run_id
            if verification_run_id is not None
            else current.verification_run_id
        )
        new_exec_at = executed_at if executed_at is not None else current.executed_at
        new_ver_at = verified_at if verified_at is not None else current.verified_at

        self._session.execute(
            """
            UPDATE resolution_actions
            SET status = ?,
                execution_result_json = ?,
                verification_run_id = ?,
                executed_at = ?,
                verified_at = ?,
                updated_at = ?
            WHERE resolution_id = ?
            """,
            (
                target_status.value,
                json.dumps(new_exec_res),
                new_ver_run,
                new_exec_at,
                new_ver_at,
                now,
                resolution_id,
            ),
        )
        updated = self.get_resolution(resolution_id)
        assert updated is not None
        return updated

    def create_approval(
        self,
        resolution_id: str,
        decision: ApprovalDecision,
        actor_id: str,
        actor_role: str,
        comment: Optional[str] = None,
    ) -> ApprovalRead:
        now = utcnow_iso()
        approval_id = str(uuid.uuid4())
        self._session.execute(
            """
            INSERT INTO approvals (
                approval_id, resolution_id, decision,
                actor_id, actor_role, comment, decided_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                resolution_id,
                decision.value,
                actor_id,
                actor_role,
                comment,
                now,
                now,
            ),
        )
        return ApprovalRead(
            approval_id=approval_id,
            resolution_id=resolution_id,
            decision=decision,
            actor_id=actor_id,
            actor_role=actor_role,
            comment=comment,
            decided_at=now,
            created_at=now,
        )

    def list_approvals_for_resolution(self, resolution_id: str) -> List[ApprovalRead]:
        rows = self._session.fetchall(
            "SELECT * FROM approvals WHERE resolution_id = ? ORDER BY decided_at ASC",
            (resolution_id,),
        )
        return [
            ApprovalRead(
                approval_id=r["approval_id"],
                resolution_id=r["resolution_id"],
                decision=ApprovalDecision(r["decision"]),
                actor_id=r["actor_id"],
                actor_role=r["actor_role"],
                comment=r["comment"],
                decided_at=r["decided_at"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

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
