"""Parameterized repository for Orders, Payments, Refunds, InventoryRecords, and EventLogs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.base import format_decimal_db, utcnow_iso
from app.db.session import DatabaseSession
from app.models.enums import (
    InventoryStatus,
    OrderStatus,
    PaymentSourceSystem,
    PaymentStatus,
    RefundStatus,
)
from app.repositories.sources_repo import DuplicateEntityError
from app.schemas.domain import (
    EventLogCreate,
    EventLogRead,
    InventoryRecordCreate,
    InventoryRecordRead,
    OrderCreate,
    OrderRead,
    PaymentCreate,
    PaymentRead,
    RefundCreate,
    RefundRead,
)


class RecordsRepository:
    """Data access layer for financial, order, inventory, and event log records."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    # -------------------------
    # Orders
    # -------------------------
    def create_order(self, payload: OrderCreate) -> OrderRead:
        now = utcnow_iso()
        record_id = str(uuid.uuid4())
        expected_str = format_decimal_db(payload.expected_amount)
        try:
            self._session.execute(
                """
                INSERT INTO orders (
                    id, order_id, source_record_id, source_id, customer_id,
                    expected_amount, currency, status, line_items_json,
                    event_timestamp, updated_timestamp, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    payload.order_id,
                    payload.source_record_id,
                    payload.source_id,
                    payload.customer_id,
                    expected_str,
                    payload.currency,
                    payload.status.value,
                    json.dumps(payload.line_items),
                    payload.event_timestamp,
                    payload.updated_timestamp,
                    json.dumps(payload.metadata),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"Order with order_id '{payload.order_id}' already exists."
            ) from exc

        return OrderRead(
            id=record_id,
            order_id=payload.order_id,
            source_record_id=payload.source_record_id,
            source_id=payload.source_id,
            customer_id=payload.customer_id,
            expected_amount=Decimal(expected_str),
            currency=payload.currency,
            status=payload.status,
            line_items=payload.line_items,
            event_timestamp=payload.event_timestamp,
            updated_timestamp=payload.updated_timestamp,
            metadata=payload.metadata,
            created_at=now,
            updated_at=now,
        )

    def get_order(self, order_id: str) -> Optional[OrderRead]:
        row = self._session.fetchone(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        )
        if row is None:
            return None
        return self._row_to_order(row)

    def list_orders(self, limit: int = 100, offset: int = 0) -> List[OrderRead]:
        rows = self._session.fetchall(
            "SELECT * FROM orders ORDER BY event_timestamp ASC, order_id ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_order(r) for r in rows]

    @staticmethod
    def _row_to_order(row: Dict[str, Any]) -> OrderRead:
        return OrderRead(
            id=row["id"],
            order_id=row["order_id"],
            source_record_id=row["source_record_id"],
            source_id=row["source_id"],
            customer_id=row["customer_id"],
            expected_amount=Decimal(row["expected_amount"]),
            currency=row["currency"],
            status=OrderStatus(row["status"]),
            line_items=json.loads(row["line_items_json"]),
            event_timestamp=row["event_timestamp"],
            updated_timestamp=row["updated_timestamp"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------
    # Payments (Gateway & Finance Ledger)
    # -------------------------
    def create_payment(self, payload: PaymentCreate) -> PaymentRead:
        now = utcnow_iso()
        record_id = str(uuid.uuid4())
        amount_str = format_decimal_db(payload.amount)
        captured_str = format_decimal_db(payload.captured_amount)
        try:
            self._session.execute(
                """
                INSERT INTO payments (
                    id, transaction_id, provider_charge_id, order_id, source_record_id,
                    source_system, source_id, amount, captured_amount, currency, status,
                    event_timestamp, updated_timestamp, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    payload.transaction_id,
                    payload.provider_charge_id,
                    payload.order_id,
                    payload.source_record_id,
                    payload.source_system.value,
                    payload.source_id,
                    amount_str,
                    captured_str,
                    payload.currency,
                    payload.status.value,
                    payload.event_timestamp,
                    payload.updated_timestamp,
                    json.dumps(payload.metadata),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"Payment with transaction_id '{payload.transaction_id}' already exists."
            ) from exc

        return PaymentRead(
            id=record_id,
            transaction_id=payload.transaction_id,
            provider_charge_id=payload.provider_charge_id,
            order_id=payload.order_id,
            source_record_id=payload.source_record_id,
            source_system=payload.source_system,
            source_id=payload.source_id,
            amount=Decimal(amount_str),
            captured_amount=Decimal(captured_str),
            currency=payload.currency,
            status=payload.status,
            event_timestamp=payload.event_timestamp,
            updated_timestamp=payload.updated_timestamp,
            metadata=payload.metadata,
            created_at=now,
            updated_at=now,
        )

    def get_payment(self, transaction_id: str) -> Optional[PaymentRead]:
        row = self._session.fetchone(
            "SELECT * FROM payments WHERE transaction_id = ?", (transaction_id,)
        )
        if row is None:
            return None
        return self._row_to_payment(row)

    def list_payments(
        self,
        order_id: Optional[str] = None,
        source_system: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PaymentRead]:
        clauses: list[str] = []
        params: list[Any] = []
        if order_id:
            clauses.append("order_id = ?")
            params.append(order_id)
        if source_system:
            clauses.append("source_system = ?")
            params.append(source_system)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._session.fetchall(
            f"SELECT * FROM payments {where_sql} ORDER BY event_timestamp ASC, transaction_id ASC LIMIT ? OFFSET ?",
            params,
        )
        return [self._row_to_payment(r) for r in rows]

    @staticmethod
    def _row_to_payment(row: Dict[str, Any]) -> PaymentRead:
        return PaymentRead(
            id=row["id"],
            transaction_id=row["transaction_id"],
            provider_charge_id=row["provider_charge_id"],
            order_id=row["order_id"],
            source_record_id=row["source_record_id"],
            source_system=PaymentSourceSystem(row["source_system"]),
            source_id=row["source_id"],
            amount=Decimal(row["amount"]),
            captured_amount=Decimal(row["captured_amount"]),
            currency=row["currency"],
            status=PaymentStatus(row["status"]),
            event_timestamp=row["event_timestamp"],
            updated_timestamp=row["updated_timestamp"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------
    # Refunds
    # -------------------------
    def create_refund(self, payload: RefundCreate) -> RefundRead:
        now = utcnow_iso()
        record_id = str(uuid.uuid4())
        amount_str = format_decimal_db(payload.amount)
        try:
            self._session.execute(
                """
                INSERT INTO refunds (
                    id, refund_id, transaction_id, order_id, provider_refund_id,
                    source_record_id, source_id, amount, currency, status,
                    reason, event_timestamp, updated_timestamp, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    payload.refund_id,
                    payload.transaction_id,
                    payload.order_id,
                    payload.provider_refund_id,
                    payload.source_record_id,
                    payload.source_id,
                    amount_str,
                    payload.currency,
                    payload.status.value,
                    payload.reason,
                    payload.event_timestamp,
                    payload.updated_timestamp,
                    json.dumps(payload.metadata),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"Refund with refund_id '{payload.refund_id}' already exists."
            ) from exc

        return RefundRead(
            id=record_id,
            refund_id=payload.refund_id,
            transaction_id=payload.transaction_id,
            order_id=payload.order_id,
            provider_refund_id=payload.provider_refund_id,
            source_record_id=payload.source_record_id,
            source_id=payload.source_id,
            amount=Decimal(amount_str),
            currency=payload.currency,
            status=payload.status,
            reason=payload.reason,
            event_timestamp=payload.event_timestamp,
            updated_timestamp=payload.updated_timestamp,
            metadata=payload.metadata,
            created_at=now,
            updated_at=now,
        )

    def get_refund(self, refund_id: str) -> Optional[RefundRead]:
        row = self._session.fetchone(
            "SELECT * FROM refunds WHERE refund_id = ?", (refund_id,)
        )
        if row is None:
            return None
        return self._row_to_refund(row)

    def list_refunds(
        self, order_id: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> List[RefundRead]:
        if order_id:
            rows = self._session.fetchall(
                "SELECT * FROM refunds WHERE order_id = ? ORDER BY event_timestamp ASC LIMIT ? OFFSET ?",
                (order_id, limit, offset),
            )
        else:
            rows = self._session.fetchall(
                "SELECT * FROM refunds ORDER BY event_timestamp ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_refund(r) for r in rows]

    @staticmethod
    def _row_to_refund(row: Dict[str, Any]) -> RefundRead:
        return RefundRead(
            id=row["id"],
            refund_id=row["refund_id"],
            transaction_id=row["transaction_id"],
            order_id=row["order_id"],
            provider_refund_id=row["provider_refund_id"],
            source_record_id=row["source_record_id"],
            source_id=row["source_id"],
            amount=Decimal(row["amount"]),
            currency=row["currency"],
            status=RefundStatus(row["status"]),
            reason=row["reason"],
            event_timestamp=row["event_timestamp"],
            updated_timestamp=row["updated_timestamp"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------
    # Inventory Records
    # -------------------------
    def create_inventory_record(self, payload: InventoryRecordCreate) -> InventoryRecordRead:
        now = utcnow_iso()
        record_id = str(uuid.uuid4())
        try:
            self._session.execute(
                """
                INSERT INTO inventory_records (
                    id, inventory_record_id, sku, order_id, source_record_id,
                    source_id, delta_quantity, available_quantity, reserved_quantity,
                    warehouse_id, status, event_timestamp, updated_timestamp,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    payload.inventory_record_id,
                    payload.sku,
                    payload.order_id,
                    payload.source_record_id,
                    payload.source_id,
                    payload.delta_quantity,
                    payload.available_quantity,
                    payload.reserved_quantity,
                    payload.warehouse_id,
                    payload.status.value,
                    payload.event_timestamp,
                    payload.updated_timestamp,
                    json.dumps(payload.metadata),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"InventoryRecord with inventory_record_id '{payload.inventory_record_id}' already exists."
            ) from exc

        return InventoryRecordRead(
            id=record_id,
            inventory_record_id=payload.inventory_record_id,
            sku=payload.sku,
            order_id=payload.order_id,
            source_record_id=payload.source_record_id,
            source_id=payload.source_id,
            delta_quantity=payload.delta_quantity,
            available_quantity=payload.available_quantity,
            reserved_quantity=payload.reserved_quantity,
            warehouse_id=payload.warehouse_id,
            status=payload.status,
            event_timestamp=payload.event_timestamp,
            updated_timestamp=payload.updated_timestamp,
            metadata=payload.metadata,
            created_at=now,
            updated_at=now,
        )

    def get_inventory_record(self, inventory_record_id: str) -> Optional[InventoryRecordRead]:
        row = self._session.fetchone(
            "SELECT * FROM inventory_records WHERE inventory_record_id = ?",
            (inventory_record_id,),
        )
        if row is None:
            return None
        return self._row_to_inventory(row)

    def list_inventory_records(
        self,
        sku: Optional[str] = None,
        order_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[InventoryRecordRead]:
        clauses: list[str] = []
        params: list[Any] = []
        if sku:
            clauses.append("sku = ?")
            params.append(sku)
        if order_id:
            clauses.append("order_id = ?")
            params.append(order_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._session.fetchall(
            f"SELECT * FROM inventory_records {where_sql} ORDER BY event_timestamp ASC LIMIT ? OFFSET ?",
            params,
        )
        return [self._row_to_inventory(r) for r in rows]

    @staticmethod
    def _row_to_inventory(row: Dict[str, Any]) -> InventoryRecordRead:
        return InventoryRecordRead(
            id=row["id"],
            inventory_record_id=row["inventory_record_id"],
            sku=row["sku"],
            order_id=row["order_id"],
            source_record_id=row["source_record_id"],
            source_id=row["source_id"],
            delta_quantity=int(row["delta_quantity"]),
            available_quantity=int(row["available_quantity"]),
            reserved_quantity=int(row["reserved_quantity"]),
            warehouse_id=row["warehouse_id"],
            status=InventoryStatus(row["status"]),
            event_timestamp=row["event_timestamp"],
            updated_timestamp=row["updated_timestamp"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------
    # Event Logs (Webhooks & Operational Evidence)
    # -------------------------
    def create_event_log(self, payload: EventLogCreate) -> EventLogRead:
        now = utcnow_iso()
        event_id = payload.event_id or str(uuid.uuid4())
        try:
            self._session.execute(
                """
                INSERT INTO event_logs (
                    event_id, source_record_id, source_id, event_type,
                    order_id, transaction_id, refund_id, sku,
                    delivery_status, attempt_count, error_code, message,
                    payload_json, event_timestamp, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    payload.source_record_id,
                    payload.source_id,
                    payload.event_type,
                    payload.order_id,
                    payload.transaction_id,
                    payload.refund_id,
                    payload.sku,
                    payload.delivery_status,
                    payload.attempt_count,
                    payload.error_code,
                    payload.message,
                    json.dumps(payload.payload),
                    payload.event_timestamp,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntityError(
                f"EventLog with event_id '{event_id}' already exists."
            ) from exc

        return EventLogRead(
            event_id=event_id,
            source_record_id=payload.source_record_id,
            source_id=payload.source_id,
            event_type=payload.event_type,
            order_id=payload.order_id,
            transaction_id=payload.transaction_id,
            refund_id=payload.refund_id,
            sku=payload.sku,
            delivery_status=payload.delivery_status,
            attempt_count=payload.attempt_count,
            error_code=payload.error_code,
            message=payload.message,
            payload=payload.payload,
            event_timestamp=payload.event_timestamp,
            created_at=now,
        )

    def list_event_logs(
        self,
        order_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[EventLogRead]:
        clauses: list[str] = []
        params: list[Any] = []
        if order_id:
            clauses.append("order_id = ?")
            params.append(order_id)
        if transaction_id:
            clauses.append("transaction_id = ?")
            params.append(transaction_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._session.fetchall(
            f"SELECT * FROM event_logs {where_sql} ORDER BY event_timestamp ASC LIMIT ?",
            params,
        )
        return [
            EventLogRead(
                event_id=r["event_id"],
                source_record_id=r["source_record_id"],
                source_id=r["source_id"],
                event_type=r["event_type"],
                order_id=r["order_id"],
                transaction_id=r["transaction_id"],
                refund_id=r["refund_id"],
                sku=r["sku"],
                delivery_status=r["delivery_status"],
                attempt_count=int(r["attempt_count"]),
                error_code=r["error_code"],
                message=r["message"],
                payload=json.loads(r["payload_json"]),
                event_timestamp=r["event_timestamp"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
