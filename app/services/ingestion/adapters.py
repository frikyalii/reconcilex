"""Source adapters, configurable schema mapping, and row-level validation/import engine.

Implements:
1. CSVSourceAdapter — parses CSV files or inline CSV strings with configurable column mappings.
2. PostgresTableAdapter — reads from a configured database table using parameterized SQL.
3. SyntheticDatasetAdapter — generates deterministic synthetic rows for testing and demos.
4. IngestionService — applies schema mapping, validates every row without silently discarding
   invalid records, produces structured `SourceValidationReport` / `SourceImportReport`
   with 1-based row numbers and error codes, and persists valid rows idempotently.
"""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError

from app.db.session import DatabaseSession
from app.models.enums import PaymentSourceSystem, SourceType, TargetEntity
from app.repositories.audit_repo import AuditRepository
from app.repositories.records_repo import RecordsRepository
from app.repositories.sources_repo import DuplicateEntityError, SourcesRepository
from app.schemas.domain import (
    DataSourceRead,
    EventLogCreate,
    InventoryRecordCreate,
    OrderCreate,
    PaymentCreate,
    RefundCreate,
    RowValidationError,
    SchemaMappingRead,
    SourceImportReport,
    SourceImportRequest,
    SourceValidationReport,
)
from app.services.ingestion.synthetic_data import (
    get_synthetic_event_logs,
    get_synthetic_finance_ledger_payments,
    get_synthetic_gateway_payments,
    get_synthetic_inventory_records,
    get_synthetic_orders,
    get_synthetic_refunds,
)


class CSVSourceAdapter:
    """Adapter for extracting rows from CSV files or inline CSV text."""

    def __init__(self, max_bytes: int = 10 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes

    def extract_rows(
        self,
        connection_config: Dict[str, Any],
        inline_csv: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if inline_csv is not None:
            raw_bytes = len(inline_csv.encode("utf-8"))
            if raw_bytes > self._max_bytes:
                raise ValueError(
                    f"CSV payload size ({raw_bytes} bytes) exceeds limit of {self._max_bytes} bytes."
                )
            text_stream = io.StringIO(inline_csv)
        else:
            file_path = connection_config.get("file_path")
            if not file_path:
                raise ValueError(
                    "CSV source requires either 'connection_config.file_path' or inline 'csv_content'."
                )
            path_obj = Path(str(file_path))
            if not path_obj.exists():
                raise FileNotFoundError(f"CSV source file not found: {file_path}")
            size = path_obj.stat().st_size
            if size > self._max_bytes:
                raise ValueError(
                    f"CSV file size ({size} bytes) exceeds limit of {self._max_bytes} bytes."
                )
            text_stream = io.StringIO(path_obj.read_text(encoding="utf-8"))

        delimiter = str(connection_config.get("delimiter", ","))
        reader = csv.DictReader(text_stream, delimiter=delimiter)
        rows: List[Dict[str, Any]] = []
        for row in reader:
            cleaned = {
                str(k).strip(): (v.strip() if isinstance(v, str) else v)
                for k, v in row.items()
                if k is not None
            }
            rows.append(cleaned)
        return rows


class PostgresTableAdapter:
    """Adapter for reading rows from an existing relational staging table."""

    ALLOWED_STAGING_TABLES = {
        "staging_orders",
        "staging_payments",
        "staging_refunds",
        "staging_inventory",
        "orders",
        "payments",
        "refunds",
        "inventory_records",
    }

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def extract_rows(self, connection_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        table_name = str(connection_config.get("table_name", "")).strip()
        if table_name not in self.ALLOWED_STAGING_TABLES:
            raise ValueError(
                f"Table '{table_name}' is not in the allowed staging table whitelist."
            )
        limit = int(connection_config.get("batch_size", 1000))
        return self._session.fetchall(f"SELECT * FROM {table_name} LIMIT ?", (limit,))


class SyntheticDatasetAdapter:
    """Adapter for generating synthetic records in-memory for tests and scenarios."""

    def extract_rows(
        self,
        target_entity: TargetEntity,
        connection_config: Dict[str, Any],
        inline_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        if inline_rows is not None:
            return list(inline_rows)

        if target_entity == TargetEntity.ORDERS:
            return get_synthetic_orders()
        if target_entity == TargetEntity.PAYMENTS:
            return get_synthetic_gateway_payments()
        if target_entity == TargetEntity.FINANCE_LEDGER:
            return get_synthetic_finance_ledger_payments()
        if target_entity == TargetEntity.REFUNDS:
            return get_synthetic_refunds()
        if target_entity == TargetEntity.INVENTORY:
            return get_synthetic_inventory_records()
        if target_entity == TargetEntity.EVENT_LOGS:
            return get_synthetic_event_logs()
        return []


class IngestionService:
    """Validates, normalizes, and imports source rows using configurable schema mappings."""

    def __init__(self, session: DatabaseSession, max_upload_bytes: int = 10 * 1024 * 1024) -> None:
        self._session = session
        self._sources_repo = SourcesRepository(session)
        self._records_repo = RecordsRepository(session)
        self._audit_repo = AuditRepository(session)
        self._csv_adapter = CSVSourceAdapter(max_bytes=max_upload_bytes)
        self._pg_adapter = PostgresTableAdapter(session)
        self._synthetic_adapter = SyntheticDatasetAdapter()

    def validate_source_data(
        self,
        source_id: str,
        request: Optional[SourceImportRequest] = None,
    ) -> SourceValidationReport:
        source = self._get_source_or_raise(source_id)
        raw_rows = self._extract_raw_rows(source, request)
        valid_models, row_errors = self._validate_rows(source, raw_rows)

        return SourceValidationReport(
            source_id=source.source_id,
            source_name=source.name,
            source_type=source.source_type,
            target_entity=source.target_entity,
            total_rows=len(raw_rows),
            valid_rows=len(valid_models),
            invalid_rows=len(row_errors),
            is_valid=(len(row_errors) == 0),
            row_errors=row_errors,
        )

    def import_source_data(
        self,
        source_id: str,
        request: Optional[SourceImportRequest] = None,
        actor_id: str = "system",
        actor_role: str = "system",
    ) -> SourceImportReport:
        source = self._get_source_or_raise(source_id)
        raw_rows = self._extract_raw_rows(source, request)
        valid_models, row_errors = self._validate_rows(source, raw_rows)

        imported_ids: List[str] = []
        skipped_duplicates = 0

        for row_num, raw_row, model_instance in valid_models:
            try:
                if isinstance(model_instance, OrderCreate):
                    created = self._records_repo.create_order(model_instance)
                    imported_ids.append(created.order_id)
                elif isinstance(model_instance, PaymentCreate):
                    created = self._records_repo.create_payment(model_instance)
                    imported_ids.append(created.transaction_id)
                elif isinstance(model_instance, RefundCreate):
                    created = self._records_repo.create_refund(model_instance)
                    imported_ids.append(created.refund_id)
                elif isinstance(model_instance, InventoryRecordCreate):
                    created = self._records_repo.create_inventory_record(model_instance)
                    imported_ids.append(created.inventory_record_id)
                elif isinstance(model_instance, EventLogCreate):
                    created = self._records_repo.create_event_log(model_instance)
                    imported_ids.append(created.event_id)
            except DuplicateEntityError as dup_exc:
                skipped_duplicates += 1
                row_errors.append(
                    RowValidationError(
                        row_number=row_num,
                        source_record_id=str(
                            getattr(model_instance, "source_record_id", None) or ""
                        ),
                        field="primary_identifier",
                        error_code="DUPLICATE_ENTITY",
                        message=str(dup_exc),
                        raw_row=raw_row,
                    )
                )

        self._audit_repo.record_event(
            entity_type="data_source",
            entity_id=source.source_id,
            action="source_import_completed",
            actor_id=actor_id,
            actor_role=actor_role,
            after_state={
                "total_rows": len(raw_rows),
                "imported_rows": len(imported_ids),
                "skipped_duplicate_rows": skipped_duplicates,
                "invalid_rows": len(row_errors),
            },
        )

        return SourceImportReport(
            source_id=source.source_id,
            source_name=source.name,
            source_type=source.source_type,
            target_entity=source.target_entity,
            total_rows=len(raw_rows),
            imported_rows=len(imported_ids),
            skipped_duplicate_rows=skipped_duplicates,
            invalid_rows=len(row_errors),
            row_errors=row_errors,
            imported_ids=imported_ids,
        )

    def _get_source_or_raise(self, source_id: str) -> DataSourceRead:
        source = self._sources_repo.get_source(source_id)
        if source is None:
            raise KeyError(f"DataSource '{source_id}' not found.")
        return source

    def _extract_raw_rows(
        self, source: DataSourceRead, request: Optional[SourceImportRequest]
    ) -> List[Dict[str, Any]]:
        req = request or SourceImportRequest()
        if req.rows is not None:
            return list(req.rows)

        if source.source_type == SourceType.CSV:
            return self._csv_adapter.extract_rows(
                source.connection_config, inline_csv=req.csv_content
            )
        if source.source_type == SourceType.POSTGRES:
            return self._pg_adapter.extract_rows(source.connection_config)
        if source.source_type == SourceType.SYNTHETIC:
            return self._synthetic_adapter.extract_rows(
                source.target_entity, source.connection_config, inline_rows=req.rows
            )
        raise ValueError(f"Unsupported source_type: {source.source_type}")

    def _validate_rows(
        self, source: DataSourceRead, raw_rows: List[Dict[str, Any]]
    ) -> Tuple[List[Tuple[int, Dict[str, Any], Any]], List[RowValidationError]]:
        valid_models: List[Tuple[int, Dict[str, Any], Any]] = []
        row_errors: List[RowValidationError] = []
        mapping = source.schema_mapping

        for idx, raw_row in enumerate(raw_rows, start=1):
            mapped_row, missing_req = self._apply_schema_mapping(raw_row, mapping)
            src_rec_id = str(mapped_row.get("source_record_id") or "").strip() or None

            if missing_req:
                row_errors.append(
                    RowValidationError(
                        row_number=idx,
                        source_record_id=src_rec_id,
                        field=missing_req[0],
                        error_code="MISSING_REQUIRED_FIELD",
                        message=f"Missing required mapped fields: {missing_req}",
                        raw_row=raw_row,
                    )
                )
                continue

            # Check custom validation rules (such as allowed_currencies)
            if mapping and mapping.validation_rules:
                allowed_currencies = mapping.validation_rules.get("allowed_currencies")
                if allowed_currencies and "currency" in mapped_row:
                    curr_val = str(mapped_row.get("currency") or "").strip().upper()
                    if curr_val not in [c.upper() for c in allowed_currencies]:
                        row_errors.append(
                            RowValidationError(
                                row_number=idx,
                                source_record_id=src_rec_id,
                                field="currency",
                                error_code="DISALLOWED_CURRENCY",
                                message=f"Currency '{curr_val}' is not in allowed_currencies {allowed_currencies}.",
                                raw_row=raw_row,
                            )
                        )
                        continue

            mapped_row["source_id"] = source.source_id
            try:
                model_obj = self._build_target_model(source.target_entity, mapped_row)
                valid_models.append((idx, raw_row, model_obj))
            except (ValidationError, ValueError, TypeError, InvalidOperation) as exc:
                field_name = None
                if isinstance(exc, ValidationError) and exc.errors():
                    first_loc = exc.errors()[0].get("loc", ())
                    field_name = str(first_loc[0]) if first_loc else None
                row_errors.append(
                    RowValidationError(
                        row_number=idx,
                        source_record_id=src_rec_id,
                        field=field_name,
                        error_code="SCHEMA_VALIDATION_FAILED",
                        message=str(exc),
                        raw_row=raw_row,
                    )
                )

        return valid_models, row_errors

    @staticmethod
    def _apply_schema_mapping(
        raw_row: Dict[str, Any], mapping: Optional[SchemaMappingRead]
    ) -> Tuple[Dict[str, Any], List[str]]:
        if mapping is None or not mapping.field_mappings:
            mapped = dict(raw_row)
            required = mapping.required_fields if mapping else []
        else:
            mapped = {}
            used_source_keys = set()
            for canonical_field, source_column in mapping.field_mappings.items():
                if source_column in raw_row:
                    mapped[canonical_field] = raw_row[source_column]
                    used_source_keys.add(source_column)
            # Preserve any unmapped columns in `metadata` or pass through if matching canonical name
            extra_meta: Dict[str, Any] = {}
            for k, v in raw_row.items():
                if k not in used_source_keys:
                    if k not in mapped:
                        extra_meta[k] = v
            if extra_meta:
                existing_meta = mapped.get("metadata")
                if isinstance(existing_meta, dict):
                    existing_meta.update(extra_meta)
                else:
                    mapped["metadata"] = extra_meta
            required = mapping.required_fields

        # Normalize JSON string columns if coming from CSV
        for json_field in ("line_items", "metadata", "payload"):
            val = mapped.get(json_field)
            if isinstance(val, str):
                val_str = val.strip()
                if val_str.startswith(("{", "[")):
                    try:
                        mapped[json_field] = json.loads(val_str)
                    except json.JSONDecodeError:
                        pass
                elif not val_str:
                    mapped.pop(json_field, None)

        missing: List[str] = []
        for req_field in required:
            val = mapped.get(req_field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing.append(req_field)

        return mapped, missing

    @staticmethod
    def _build_target_model(target_entity: TargetEntity, mapped_row: Dict[str, Any]) -> Any:
        if target_entity == TargetEntity.ORDERS:
            return OrderCreate(**mapped_row)
        if target_entity == TargetEntity.PAYMENTS:
            mapped_row.setdefault("source_system", PaymentSourceSystem.PAYMENT_GATEWAY.value)
            return PaymentCreate(**mapped_row)
        if target_entity == TargetEntity.FINANCE_LEDGER:
            mapped_row["source_system"] = PaymentSourceSystem.FINANCE_LEDGER.value
            return PaymentCreate(**mapped_row)
        if target_entity == TargetEntity.REFUNDS:
            return RefundCreate(**mapped_row)
        if target_entity == TargetEntity.INVENTORY:
            return InventoryRecordCreate(**mapped_row)
        if target_entity == TargetEntity.EVENT_LOGS:
            return EventLogCreate(**mapped_row)
        raise ValueError(f"Unsupported target_entity: {target_entity}")
