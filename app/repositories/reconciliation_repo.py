"""Parameterized repository for ReconciliationRules, ReconciliationRuns, and Discrepancies."""

from __future__ import annotations

import json
import sqlite3
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.base import format_decimal_db, utcnow_iso
from app.db.session import DatabaseSession
from app.models.enums import (
    DiscrepancyStatus,
    MatchConfidence,
    ReconciliationRunStatus,
    RuleType,
    Severity,
    validate_run_transition,
)
from app.repositories.sources_repo import DuplicateEntityError
from app.schemas.domain import (
    DiscrepancyCreate,
    DiscrepancyRead,
    ReconciliationRuleCreate,
    ReconciliationRuleRead,
    ReconciliationRunRead,
)


class ReconciliationRepository:
    """Data access operations for `reconciliation_rules`, `reconciliation_runs`, and `discrepancies`."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    # -------------------------
    # Reconciliation Rules
    # -------------------------
    def create_rule(self, payload: ReconciliationRuleCreate) -> ReconciliationRuleRead:
        now = utcnow_iso()
        rule_id = str(uuid.uuid4())
        tol_str = format_decimal_db(payload.tolerance_amount)
        try:
            self._session.execute(
                """
                INSERT INTO reconciliation_rules (
                    rule_id, code, name, description, rule_type,
                    severity, tolerance_amount, max_delay_seconds,
                    is_active, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    payload.code,
                    payload.name,
                    payload.description,
                    payload.rule_type.value,
                    payload.severity.value,
                    tol_str,
                    payload.max_delay_seconds,
                    1 if payload.is_active else 0,
                    json.dumps(payload.config),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"ReconciliationRule with code '{payload.code}' already exists."
            ) from exc

        return ReconciliationRuleRead(
            rule_id=rule_id,
            code=payload.code,
            name=payload.name,
            description=payload.description,
            rule_type=payload.rule_type,
            severity=payload.severity,
            tolerance_amount=Decimal(tol_str),
            max_delay_seconds=payload.max_delay_seconds,
            is_active=payload.is_active,
            config=payload.config,
            created_at=now,
            updated_at=now,
        )

    def get_rule_by_code(self, code: str) -> Optional[ReconciliationRuleRead]:
        row = self._session.fetchone(
            "SELECT * FROM reconciliation_rules WHERE code = ?", (code,)
        )
        if row is None:
            return None
        return self._row_to_rule(row)

    def list_rules(
        self, active_only: bool = False, limit: int = 100, offset: int = 0
    ) -> List[ReconciliationRuleRead]:
        if active_only:
            rows = self._session.fetchall(
                "SELECT * FROM reconciliation_rules WHERE is_active = 1 ORDER BY code ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        else:
            rows = self._session.fetchall(
                "SELECT * FROM reconciliation_rules ORDER BY code ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_rule(r) for r in rows]

    @staticmethod
    def _row_to_rule(row: Dict[str, Any]) -> ReconciliationRuleRead:
        return ReconciliationRuleRead(
            rule_id=row["rule_id"],
            code=row["code"],
            name=row["name"],
            description=row["description"],
            rule_type=RuleType(row["rule_type"]),
            severity=Severity(row["severity"]),
            tolerance_amount=Decimal(row["tolerance_amount"]),
            max_delay_seconds=int(row["max_delay_seconds"]),
            is_active=bool(row["is_active"]),
            config=json.loads(row["config_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------
    # Reconciliation Runs
    # -------------------------
    def create_run(
        self,
        triggered_by: str = "system",
        idempotency_key: Optional[str] = None,
        rule_codes: Optional[List[str]] = None,
        source_snapshot: Optional[Dict[str, Any]] = None,
    ) -> ReconciliationRunRead:
        if idempotency_key:
            existing = self.get_run_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        now = utcnow_iso()
        run_id = str(uuid.uuid4())
        codes = rule_codes or []
        snap = source_snapshot or {}

        self._session.execute(
            """
            INSERT INTO reconciliation_runs (
                run_id, idempotency_key, status, triggered_by,
                source_snapshot_json, rule_codes_json, input_counts_json,
                output_counts_json, discrepancy_count, error_summary,
                started_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                idempotency_key,
                ReconciliationRunStatus.PENDING.value,
                triggered_by,
                json.dumps(snap),
                json.dumps(codes),
                json.dumps({}),
                json.dumps({}),
                0,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        return ReconciliationRunRead(
            run_id=run_id,
            idempotency_key=idempotency_key,
            status=ReconciliationRunStatus.PENDING,
            triggered_by=triggered_by,
            source_snapshot=snap,
            rule_codes=codes,
            input_counts={},
            output_counts={},
            discrepancy_count=0,
            error_summary=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )

    def update_run_state(
        self,
        run_id: str,
        target_status: ReconciliationRunStatus,
        source_snapshot: Optional[Dict[str, Any]] = None,
        rule_codes: Optional[List[str]] = None,
        input_counts: Optional[Dict[str, int]] = None,
        output_counts: Optional[Dict[str, int]] = None,
        discrepancy_count: Optional[int] = None,
        error_summary: Optional[str] = None,
        clear_error_summary: bool = False,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> ReconciliationRunRead:
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(f"ReconciliationRun '{run_id}' not found.")
        if current.status != target_status and not validate_run_transition(
            current.status, target_status
        ):
            raise ValueError(
                f"Invalid run status transition from '{current.status.value}' to '{target_status.value}'."
            )

        now = utcnow_iso()
        new_snap = source_snapshot if source_snapshot is not None else current.source_snapshot
        new_codes = rule_codes if rule_codes is not None else current.rule_codes
        new_in = input_counts if input_counts is not None else current.input_counts
        new_out = output_counts if output_counts is not None else current.output_counts
        new_disc = (
            discrepancy_count if discrepancy_count is not None else current.discrepancy_count
        )
        if clear_error_summary:
            new_err = None
        else:
            new_err = error_summary if error_summary is not None else current.error_summary
        new_start = started_at if started_at is not None else current.started_at
        new_comp = completed_at if completed_at is not None else current.completed_at

        self._session.execute(
            """
            UPDATE reconciliation_runs
            SET status = ?,
                source_snapshot_json = ?,
                rule_codes_json = ?,
                input_counts_json = ?,
                output_counts_json = ?,
                discrepancy_count = ?,
                error_summary = ?,
                started_at = ?,
                completed_at = ?,
                updated_at = ?
            WHERE run_id = ?
            """,
            (
                target_status.value,
                json.dumps(new_snap),
                json.dumps(new_codes),
                json.dumps(new_in),
                json.dumps(new_out),
                new_disc,
                new_err,
                new_start,
                new_comp,
                now,
                run_id,
            ),
        )
        updated = self.get_run(run_id)
        assert updated is not None
        return updated

    def clear_discrepancies_for_run(self, run_id: str) -> int:
        """Delete any partial discrepancies recorded for a failed run before retrying."""
        cur = self._session.execute(
            "DELETE FROM discrepancies WHERE run_id = ?", (run_id,)
        )
        deleted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        cur.close()
        return deleted

    def get_run(self, run_id: str) -> Optional[ReconciliationRunRead]:
        row = self._session.fetchone(
            "SELECT * FROM reconciliation_runs WHERE run_id = ?", (run_id,)
        )
        if row is None:
            return None
        return self._row_to_run(row)

    def get_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[ReconciliationRunRead]:
        row = self._session.fetchone(
            "SELECT * FROM reconciliation_runs WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        if row is None:
            return None
        return self._row_to_run(row)

    def list_runs(
        self,
        status_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ReconciliationRunRead]:
        if status_filter:
            rows = self._session.fetchall(
                "SELECT * FROM reconciliation_runs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status_filter, limit, offset),
            )
        else:
            rows = self._session.fetchall(
                "SELECT * FROM reconciliation_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_run(r) for r in rows]

    @staticmethod
    def _row_to_run(row: Dict[str, Any]) -> ReconciliationRunRead:
        return ReconciliationRunRead(
            run_id=row["run_id"],
            idempotency_key=row["idempotency_key"],
            status=ReconciliationRunStatus(row["status"]),
            triggered_by=row["triggered_by"],
            source_snapshot=json.loads(row["source_snapshot_json"]),
            rule_codes=json.loads(row["rule_codes_json"]),
            input_counts=json.loads(row["input_counts_json"]),
            output_counts=json.loads(row["output_counts_json"]),
            discrepancy_count=int(row["discrepancy_count"]),
            error_summary=row["error_summary"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------
    # Discrepancies
    # -------------------------
    def create_discrepancy(self, payload: DiscrepancyCreate) -> DiscrepancyRead:
        now = utcnow_iso()
        discrepancy_id = str(uuid.uuid4())
        self._session.execute(
            """
            INSERT INTO discrepancies (
                discrepancy_id, run_id, rule_id, rule_code, discrepancy_type,
                severity, match_confidence, status, order_id, transaction_id,
                refund_id, sku, expected_value, actual_value, currency,
                description, record_references_json, evidence_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                discrepancy_id,
                payload.run_id,
                payload.rule_id,
                payload.rule_code,
                payload.discrepancy_type,
                payload.severity.value,
                payload.match_confidence.value,
                payload.status.value,
                payload.order_id,
                payload.transaction_id,
                payload.refund_id,
                payload.sku,
                payload.expected_value,
                payload.actual_value,
                payload.currency,
                payload.description,
                json.dumps(payload.record_references),
                json.dumps(payload.evidence),
                now,
                now,
            ),
        )
        return DiscrepancyRead(
            discrepancy_id=discrepancy_id,
            run_id=payload.run_id,
            rule_id=payload.rule_id,
            rule_code=payload.rule_code,
            discrepancy_type=payload.discrepancy_type,
            severity=payload.severity,
            match_confidence=payload.match_confidence,
            status=payload.status,
            order_id=payload.order_id,
            transaction_id=payload.transaction_id,
            refund_id=payload.refund_id,
            sku=payload.sku,
            expected_value=payload.expected_value,
            actual_value=payload.actual_value,
            currency=payload.currency,
            description=payload.description,
            record_references=payload.record_references,
            evidence=payload.evidence,
            created_at=now,
            updated_at=now,
        )

    def update_discrepancy_status(
        self, discrepancy_id: str, target_status: DiscrepancyStatus
    ) -> DiscrepancyRead:
        now = utcnow_iso()
        self._session.execute(
            "UPDATE discrepancies SET status = ?, updated_at = ? WHERE discrepancy_id = ?",
            (target_status.value, now, discrepancy_id),
        )
        updated = self.get_discrepancy(discrepancy_id)
        if updated is None:
            raise KeyError(f"Discrepancy '{discrepancy_id}' not found.")
        return updated

    def get_discrepancy(self, discrepancy_id: str) -> Optional[DiscrepancyRead]:
        row = self._session.fetchone(
            "SELECT * FROM discrepancies WHERE discrepancy_id = ?", (discrepancy_id,)
        )
        if row is None:
            return None
        return self._row_to_discrepancy(row)

    def list_discrepancies_for_run(
        self,
        run_id: str,
        severity: Optional[str] = None,
        match_confidence: Optional[str] = None,
        rule_code: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[DiscrepancyRead]:
        clauses = ["run_id = ?"]
        params: List[Any] = [run_id]
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if match_confidence:
            clauses.append("match_confidence = ?")
            params.append(match_confidence)
        if rule_code:
            clauses.append("rule_code = ?")
            params.append(rule_code)
        where_sql = " AND ".join(clauses)
        params.extend([limit, offset])
        rows = self._session.fetchall(
            f"""
            SELECT * FROM discrepancies
            WHERE {where_sql}
            ORDER BY rule_code ASC, COALESCE(order_id, '') ASC, COALESCE(transaction_id, '') ASC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        return [self._row_to_discrepancy(r) for r in rows]

    @staticmethod
    def _row_to_discrepancy(row: Dict[str, Any]) -> DiscrepancyRead:
        return DiscrepancyRead(
            discrepancy_id=row["discrepancy_id"],
            run_id=row["run_id"],
            rule_id=row["rule_id"],
            rule_code=row["rule_code"],
            discrepancy_type=row["discrepancy_type"],
            severity=Severity(row["severity"]),
            match_confidence=MatchConfidence(row["match_confidence"]),
            status=DiscrepancyStatus(row["status"]),
            order_id=row["order_id"],
            transaction_id=row["transaction_id"],
            refund_id=row["refund_id"],
            sku=row["sku"],
            expected_value=row["expected_value"],
            actual_value=row["actual_value"],
            currency=row["currency"],
            description=row["description"],
            record_references=json.loads(row["record_references_json"]),
            evidence=json.loads(row["evidence_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
