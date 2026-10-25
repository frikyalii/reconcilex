"""REST API endpoints for ReconciliationRules, ReconciliationRuns, Job Lifecycle, and Discrepancies."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app.core.config import Settings, get_settings
from app.db.session import DatabaseSession, get_db_session
from app.models.enums import ReconciliationRunStatus
from app.observability.metrics import metrics_registry
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.repositories.sources_repo import DuplicateEntityError
from app.schemas.domain import (
    DiscrepancyRead,
    ReconciliationRuleCreate,
    ReconciliationRuleRead,
    ReconciliationRunCreate,
    ReconciliationRunRead,
)
from app.services.reconciliation.engine import ReconciliationEngine
from app.workers.tasks import dispatch_reconciliation_job

router = APIRouter(prefix="/api/v1", tags=["Reconciliation"])


@router.post(
    "/reconciliation-rules",
    response_model=ReconciliationRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_reconciliation_rule(
    payload: ReconciliationRuleCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> ReconciliationRuleRead:
    """Create a custom deterministic reconciliation rule."""
    repo = ReconciliationRepository(db)
    try:
        return repo.create_rule(payload)
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/reconciliation-rules", response_model=List[ReconciliationRuleRead])
def list_reconciliation_rules(
    active_only: bool = Query(default=False),
    seed_defaults: bool = Query(
        default=True,
        description="Automatically ensure the 11 standard rules are registered before listing",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[ReconciliationRuleRead]:
    """List configured reconciliation rules."""
    if seed_defaults:
        engine = ReconciliationEngine(db)
        engine.ensure_default_rules()
    repo = ReconciliationRepository(db)
    return repo.list_rules(active_only=active_only, limit=limit, offset=offset)


@router.post(
    "/reconciliations",
    response_model=ReconciliationRunRead,
    status_code=status.HTTP_201_CREATED,
)
def trigger_reconciliation_run(
    response: Response,
    payload: Optional[ReconciliationRunCreate] = Body(default=None),
    db: DatabaseSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ReconciliationRunRead:
    """Trigger a deterministic reconciliation run (synchronously or via Celery worker)."""
    req = payload or ReconciliationRunCreate()
    engine = ReconciliationEngine(db)

    if req.async_execution:
        # Persist the job in `pending` state in PostgreSQL/SQLite first, commit, then dispatch to Celery
        pending_run = engine.enqueue_run(
            triggered_by=req.triggered_by,
            idempotency_key=req.idempotency_key,
            rule_codes=req.rule_codes,
            reference_timestamp=req.reference_timestamp,
        )
        db.commit()
        dispatch_reconciliation_job(
            run_id=pending_run.run_id,
            settings=settings,
            reference_timestamp=req.reference_timestamp,
            execute_immediately_in_eager_mode=settings.CELERY_TASK_ALWAYS_EAGER,
        )
        refreshed = ReconciliationRepository(db).get_run(pending_run.run_id)
        assert refreshed is not None
        response.status_code = status.HTTP_202_ACCEPTED
        return refreshed

    run, discrepancies = engine.execute_run(
        triggered_by=req.triggered_by,
        idempotency_key=req.idempotency_key,
        rule_codes=req.rule_codes,
        reference_timestamp=req.reference_timestamp,
    )
    metrics_registry.inc_counter(
        "reconcilex_reconciliation_runs_total",
        status=run.status.value,
    )
    metrics_registry.inc_counter(
        "reconcilex_discrepancies_detected_total",
        value=float(len(discrepancies)),
    )
    return run


@router.post(
    "/reconciliations/{run_id}/retry",
    response_model=ReconciliationRunRead,
)
def retry_reconciliation_run(
    run_id: str,
    response: Response,
    async_execution: bool = Query(default=False),
    db: DatabaseSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ReconciliationRunRead:
    """Safely retry a `failed` or `pending` reconciliation run idempotently."""
    repo = ReconciliationRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ReconciliationRun '{run_id}' not found.",
        )
    if run.status not in (
        ReconciliationRunStatus.FAILED,
        ReconciliationRunStatus.PENDING,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot retry run '{run_id}' with status '{run.status.value}'. "
                "Only 'failed' or 'pending' runs can be retried."
            ),
        )

    if async_execution:
        dispatch_reconciliation_job(
            run_id=run_id,
            settings=settings,
            execute_immediately_in_eager_mode=settings.CELERY_TASK_ALWAYS_EAGER,
        )
        refreshed = repo.get_run(run_id)
        assert refreshed is not None
        response.status_code = status.HTTP_202_ACCEPTED
        return refreshed

    engine = ReconciliationEngine(db)
    retried_run, _ = engine.execute_existing_run(run_id=run_id)
    return retried_run


@router.post(
    "/reconciliations/{run_id}/cancel",
    response_model=ReconciliationRunRead,
)
def cancel_reconciliation_run(
    run_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> ReconciliationRunRead:
    """Cancel a `pending` or `running` reconciliation run."""
    engine = ReconciliationEngine(db)
    try:
        return engine.cancel_run(run_id=run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/reconciliations", response_model=List[ReconciliationRunRead])
def list_reconciliation_runs(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[ReconciliationRunRead]:
    """List historical reconciliation runs with pagination and optional status filter."""
    repo = ReconciliationRepository(db)
    return repo.list_runs(status_filter=status_filter, limit=limit, offset=offset)


@router.get("/reconciliations/{run_id}", response_model=ReconciliationRunRead)
def get_reconciliation_run(
    run_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> ReconciliationRunRead:
    """Retrieve a specific reconciliation run by ID."""
    repo = ReconciliationRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ReconciliationRun '{run_id}' not found.",
        )
    return run


@router.get(
    "/reconciliations/{run_id}/discrepancies",
    response_model=List[DiscrepancyRead],
)
def list_run_discrepancies(
    run_id: str,
    severity: Optional[str] = Query(default=None),
    match_confidence: Optional[str] = Query(default=None),
    rule_code: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[DiscrepancyRead]:
    """List persisted discrepancies detected during a specific reconciliation run."""
    repo = ReconciliationRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ReconciliationRun '{run_id}' not found.",
        )
    return repo.list_discrepancies_for_run(
        run_id=run_id,
        severity=severity,
        match_confidence=match_confidence,
        rule_code=rule_code,
        limit=limit,
        offset=offset,
    )
