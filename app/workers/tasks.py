"""Background worker tasks for long-running reconciliation jobs and investigations.

Key Reliability Guarantees:
1. PostgreSQL/SQLite is ALWAYS the durable source of truth for job state (`reconciliation_runs`).
   Redis is used strictly as the message broker and transient cache.
2. Tasks accept a durable `run_id` that was already persisted in `pending` state
   before dispatch, transition it to `running`, and persist all findings atomically.
3. Execution is idempotent: if a worker receives a duplicate message for a run that
   is already `completed` or `completed_with_discrepancies`, it safely returns the
   persisted summary without duplicating discrepancies.
4. Correlation IDs are propagated from the HTTP request context into the worker log stream.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.config import Settings, get_settings
from app.core.logging import get_correlation_id, get_logger, set_correlation_id
from app.db.session import session_scope
from app.observability.metrics import metrics_registry
from app.services.reconciliation.engine import ReconciliationEngine
from app.workers.celery_app import HAS_CELERY, celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="reconcilex.tasks.run_reconciliation_job",
    max_retries=3,
)
def run_reconciliation_job_task(
    run_id: str,
    reference_timestamp: Optional[str] = None,
    correlation_id: Optional[str] = None,
    database_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a persisted reconciliation run inside a background worker."""
    cid = set_correlation_id(correlation_id)
    base_settings = get_settings()
    settings = (
        Settings(DATABASE_URL=database_url)
        if database_url
        else base_settings
    )

    logger.info(
        "Starting background reconciliation job",
        extra={"context": {"run_id": run_id, "correlation_id": cid}},
    )

    with session_scope(settings) as session:
        engine = ReconciliationEngine(session)
        run, discrepancies = engine.execute_existing_run(
            run_id=run_id,
            reference_timestamp=reference_timestamp,
        )

    metrics_registry.inc_counter(
        "reconcilex_worker_jobs_completed_total",
        status=run.status.value,
    )
    logger.info(
        "Completed background reconciliation job",
        extra={
            "context": {
                "run_id": run.run_id,
                "status": run.status.value,
                "discrepancy_count": len(discrepancies),
            }
        },
    )
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "discrepancy_count": len(discrepancies),
        "snapshot_sha256": run.source_snapshot.get("snapshot_sha256"),
    }


def dispatch_reconciliation_job(
    run_id: str,
    settings: Optional[Settings] = None,
    reference_timestamp: Optional[str] = None,
    execute_immediately_in_eager_mode: bool = True,
) -> str:
    """Dispatch a durable `pending` reconciliation run to Celery/Redis or eager local worker."""
    cfg = settings or get_settings()
    cid = get_correlation_id()

    if HAS_CELERY and not cfg.CELERY_TASK_ALWAYS_EAGER:
        try:
            async_res = run_reconciliation_job_task.apply_async(
                kwargs={
                    "run_id": run_id,
                    "reference_timestamp": reference_timestamp,
                    "correlation_id": cid,
                    "database_url": cfg.DATABASE_URL,
                },
                task_id=f"recon-run-{run_id}",
            )
            return str(async_res.id)
        except Exception as exc:
            logger.warning(
                "Broker dispatch failed; falling back to local execution",
                extra={"context": {"run_id": run_id, "error": str(exc)}},
            )

    if execute_immediately_in_eager_mode:
        res = run_reconciliation_job_task.apply_async(
            kwargs={
                "run_id": run_id,
                "reference_timestamp": reference_timestamp,
                "correlation_id": cid,
                "database_url": cfg.DATABASE_URL,
            },
            task_id=f"recon-run-{run_id}",
        )
        return str(res.id)

    return f"queued-deferred-{run_id}"
