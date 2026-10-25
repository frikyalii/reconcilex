"""Parameterized repository for DataSource and SchemaMapping persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from app.db.base import utcnow_iso
from app.db.session import DatabaseSession
from app.models.enums import SourceType, TargetEntity
from app.schemas.domain import DataSourceCreate, DataSourceRead, SchemaMappingRead


class DuplicateEntityError(Exception):
    """Raised when an entity violates a unique constraint."""


class SourcesRepository:
    """Data access operations for `data_sources` and `schema_mappings`."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def create_source(self, payload: DataSourceCreate) -> DataSourceRead:
        now = utcnow_iso()
        source_id = str(uuid.uuid4())
        try:
            self._session.execute(
                """
                INSERT INTO data_sources (
                    source_id, name, source_type, target_entity,
                    connection_config_json, is_active, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    payload.name,
                    payload.source_type.value,
                    payload.target_entity.value,
                    json.dumps(payload.connection_config),
                    1 if payload.is_active else 0,
                    1,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"DataSource with name '{payload.name}' already exists."
            ) from exc

        mapping_read: Optional[SchemaMappingRead] = None
        if payload.schema_mapping is not None:
            mapping_id = str(uuid.uuid4())
            self._session.execute(
                """
                INSERT INTO schema_mappings (
                    mapping_id, source_id, version, field_mappings_json,
                    required_fields_json, validation_rules_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mapping_id,
                    source_id,
                    1,
                    json.dumps(payload.schema_mapping.field_mappings),
                    json.dumps(payload.schema_mapping.required_fields),
                    json.dumps(payload.schema_mapping.validation_rules),
                    now,
                    now,
                ),
            )
            mapping_read = SchemaMappingRead(
                mapping_id=mapping_id,
                source_id=source_id,
                version=1,
                field_mappings=payload.schema_mapping.field_mappings,
                required_fields=payload.schema_mapping.required_fields,
                validation_rules=payload.schema_mapping.validation_rules,
                created_at=now,
                updated_at=now,
            )

        return DataSourceRead(
            source_id=source_id,
            name=payload.name,
            source_type=payload.source_type,
            target_entity=payload.target_entity,
            connection_config=payload.connection_config,
            is_active=payload.is_active,
            version=1,
            schema_mapping=mapping_read,
            created_at=now,
            updated_at=now,
        )

    def get_source(self, source_id: str) -> Optional[DataSourceRead]:
        row = self._session.fetchone(
            "SELECT * FROM data_sources WHERE source_id = ?", (source_id,)
        )
        if row is None:
            return None
        return self._row_to_source_read(row)

    def get_source_by_name(self, name: str) -> Optional[DataSourceRead]:
        row = self._session.fetchone(
            "SELECT * FROM data_sources WHERE name = ?", (name,)
        )
        if row is None:
            return None
        return self._row_to_source_read(row)

    def list_sources(self, limit: int = 100, offset: int = 0) -> List[DataSourceRead]:
        rows = self._session.fetchall(
            "SELECT * FROM data_sources ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_source_read(r) for r in rows]

    def get_latest_schema_mapping(self, source_id: str) -> Optional[SchemaMappingRead]:
        row = self._session.fetchone(
            """
            SELECT * FROM schema_mappings
            WHERE source_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (source_id,),
        )
        if row is None:
            return None
        return SchemaMappingRead(
            mapping_id=row["mapping_id"],
            source_id=row["source_id"],
            version=int(row["version"]),
            field_mappings=json.loads(row["field_mappings_json"]),
            required_fields=json.loads(row["required_fields_json"]),
            validation_rules=json.loads(row["validation_rules_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_source_read(self, row: Dict[str, Any]) -> DataSourceRead:
        mapping = self.get_latest_schema_mapping(row["source_id"])
        return DataSourceRead(
            source_id=row["source_id"],
            name=row["name"],
            source_type=SourceType(row["source_type"]),
            target_entity=TargetEntity(row["target_entity"]),
            connection_config=json.loads(row["connection_config_json"]),
            is_active=bool(row["is_active"]),
            version=int(row["version"]),
            schema_mapping=mapping,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
