"""Parameterized repository for immutable AuditEvent records."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from app.core.logging import get_correlation_id
from app.db.base import utcnow_iso
from app.db.session import DatabaseSession
from app.schemas.domain import AuditEventRead


class AuditRepository:
    """Data access layer for `audit_events`."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def record_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        actor_id: str = "system",
        actor_role: str = "system",
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEventRead:
        now = utcnow_iso()
        audit_id = str(uuid.uuid4())
        cid = get_correlation_id()
        before = before_state or {}
        after = after_state or {}
        meta = metadata or {}

        self._session.execute(
            """
            INSERT INTO audit_events (
                audit_id, entity_type, entity_id, action,
                actor_id, actor_role, correlation_id,
                before_state_json, after_state_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                entity_type,
                entity_id,
                action,
                actor_id,
                actor_role,
                cid,
                json.dumps(before, default=str),
                json.dumps(after, default=str),
                json.dumps(meta, default=str),
                now,
            ),
        )
        return AuditEventRead(
            audit_id=audit_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            correlation_id=cid,
            before_state=before,
            after_state=after,
            metadata=meta,
            created_at=now,
        )

    def list_events_for_entity(
        self, entity_type: str, entity_id: str
    ) -> List[AuditEventRead]:
        rows = self._session.fetchall(
            """
            SELECT * FROM audit_events
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY created_at ASC
            """,
            (entity_type, entity_id),
        )
        return [
            AuditEventRead(
                audit_id=r["audit_id"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                action=r["action"],
                actor_id=r["actor_id"],
                actor_role=r["actor_role"],
                correlation_id=r["correlation_id"],
                before_state=json.loads(r["before_state_json"]),
                after_state=json.loads(r["after_state_json"]),
                metadata=json.loads(r["metadata_json"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
