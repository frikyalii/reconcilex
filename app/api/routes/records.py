"""REST API endpoints for Orders, Payments, Refunds, InventoryRecords, and EventLogs."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.session import DatabaseSession, get_db_session
from app.repositories.records_repo import RecordsRepository
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

router = APIRouter(prefix="/api/v1", tags=["Records"])


# -------------------------
# Orders
# -------------------------
@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> OrderRead:
    repo = RecordsRepository(db)
    try:
        return repo.create_order(payload)
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/orders", response_model=List[OrderRead])
def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[OrderRead]:
    repo = RecordsRepository(db)
    return repo.list_orders(limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(
    order_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> OrderRead:
    repo = RecordsRepository(db)
    order = repo.get_order(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found.",
        )
    return order


# -------------------------
# Payments
# -------------------------
@router.post("/payments", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
def create_payment(
    payload: PaymentCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> PaymentRead:
    repo = RecordsRepository(db)
    try:
        return repo.create_payment(payload)
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/payments", response_model=List[PaymentRead])
def list_payments(
    order_id: Optional[str] = Query(default=None),
    source_system: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[PaymentRead]:
    repo = RecordsRepository(db)
    return repo.list_payments(
        order_id=order_id, source_system=source_system, limit=limit, offset=offset
    )


@router.get("/payments/{transaction_id}", response_model=PaymentRead)
def get_payment(
    transaction_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> PaymentRead:
    repo = RecordsRepository(db)
    payment = repo.get_payment(transaction_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment '{transaction_id}' not found.",
        )
    return payment


# -------------------------
# Refunds
# -------------------------
@router.post("/refunds", response_model=RefundRead, status_code=status.HTTP_201_CREATED)
def create_refund(
    payload: RefundCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> RefundRead:
    repo = RecordsRepository(db)
    try:
        return repo.create_refund(payload)
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/refunds", response_model=List[RefundRead])
def list_refunds(
    order_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[RefundRead]:
    repo = RecordsRepository(db)
    return repo.list_refunds(order_id=order_id, limit=limit, offset=offset)


# -------------------------
# Inventory Records
# -------------------------
@router.post(
    "/inventory-records",
    response_model=InventoryRecordRead,
    status_code=status.HTTP_201_CREATED,
)
def create_inventory_record(
    payload: InventoryRecordCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> InventoryRecordRead:
    repo = RecordsRepository(db)
    try:
        return repo.create_inventory_record(payload)
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/inventory-records", response_model=List[InventoryRecordRead])
def list_inventory_records(
    sku: Optional[str] = Query(default=None),
    order_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[InventoryRecordRead]:
    repo = RecordsRepository(db)
    return repo.list_inventory_records(sku=sku, order_id=order_id, limit=limit, offset=offset)


# -------------------------
# Event Logs (Operational & Webhook Evidence)
# -------------------------
@router.post(
    "/event-logs",
    response_model=EventLogRead,
    status_code=status.HTTP_201_CREATED,
)
def create_event_log(
    payload: EventLogCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> EventLogRead:
    repo = RecordsRepository(db)
    try:
        return repo.create_event_log(payload)
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/event-logs", response_model=List[EventLogRead])
def list_event_logs(
    order_id: Optional[str] = Query(default=None),
    transaction_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: DatabaseSession = Depends(get_db_session),
) -> List[EventLogRead]:
    repo = RecordsRepository(db)
    return repo.list_event_logs(order_id=order_id, transaction_id=transaction_id, limit=limit)
