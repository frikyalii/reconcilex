"""Operational endpoints: /health, /ready, and /metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import PlainTextResponse

from app.core.config import Settings, get_settings
from app.core.logging import get_correlation_id
from app.db.session import DatabaseSession, get_db_session
from app.observability.metrics import metrics_registry
from app.schemas.common import HealthResponse, ReadinessCheckItem, ReadinessResponse

router = APIRouter(tags=["Operations"])

REQUIRED_CORE_TABLES = {
    "data_sources",
    "schema_mappings",
    "orders",
    "payments",
    "refunds",
    "inventory_records",
    "reconciliation_rules",
    "reconciliation_runs",
    "discrepancies",
    "investigations",
    "resolution_actions",
    "approvals",
    "audit_events",
}


@router.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def get_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness probe confirming the API process is responsive."""
    metrics_registry.inc_counter("reconcilex_health_requests_total", endpoint="health")
    return HealthResponse(
        status="ok",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        correlation_id=get_correlation_id(),
    )


@router.get("/ready", response_model=ReadinessResponse)
def get_readiness(
    response: Response,
    db: DatabaseSession = Depends(get_db_session),
) -> ReadinessResponse:
    """Readiness probe verifying database connectivity and required core schema tables."""
    checks: list[ReadinessCheckItem] = []
    db_ok = False
    schema_ok = False

    try:
        db_ok = db.ping()
        checks.append(
            ReadinessCheckItem(
                component="database_connectivity",
                ready=db_ok,
                detail="Database SELECT 1 succeeded" if db_ok else "Database ping failed",
            )
        )
        existing_tables = set(db.list_tables())
        missing = sorted(REQUIRED_CORE_TABLES - existing_tables)
        schema_ok = len(missing) == 0
        checks.append(
            ReadinessCheckItem(
                component="database_schema",
                ready=schema_ok,
                detail=(
                    f"All {len(REQUIRED_CORE_TABLES)} core tables verified"
                    if schema_ok
                    else f"Missing tables: {missing}"
                ),
            )
        )
    except Exception as exc:
        checks.append(
            ReadinessCheckItem(
                component="database_connectivity",
                ready=False,
                detail=f"Database check failed: {type(exc).__name__}",
            )
        )

    overall_ready = db_ok and schema_ok
    if not overall_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    metrics_registry.inc_counter(
        "reconcilex_readiness_requests_total",
        status="ready" if overall_ready else "degraded",
    )

    return ReadinessResponse(
        status="ready" if overall_ready else "degraded",
        checks=checks,
        correlation_id=get_correlation_id(),
    )


@router.get("/metrics", response_class=PlainTextResponse)
def get_metrics(settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    """Prometheus text exposition endpoint."""
    if not settings.METRICS_ENABLED:
        return PlainTextResponse(
            content="Metrics collection is disabled.\n",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return PlainTextResponse(
        content=metrics_registry.render_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
