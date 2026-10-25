"""FastAPI application factory, middleware, and safe error handling for ReconcileX."""

from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes.investigations import router as investigations_router
from app.api.routes.operations import router as operations_router
from app.api.routes.reconciliation import router as reconciliation_router
from app.api.routes.records import router as records_router
from app.api.routes.resolutions import router as resolutions_router
from app.api.routes.sources import router as sources_router
from app.core.config import Settings, get_settings
from app.core.logging import (
    configure_logging,
    get_correlation_id,
    get_logger,
    set_correlation_id,
)
from app.observability.metrics import metrics_registry
from app.schemas.common import ErrorDetail

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the ReconcileX FastAPI application."""
    cfg = settings or get_settings()
    configure_logging(level=cfg.LOG_LEVEL, json_output=cfg.LOG_JSON)

    app = FastAPI(
        title=cfg.APP_NAME,
        version=cfg.APP_VERSION,
        description=(
            "Production-oriented, agent-assisted e-commerce data reconciliation platform "
            "across orders, payments, refunds, inventory, and financial ledgers."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.middleware("http")
    async def correlation_and_metrics_middleware(
        request: Request, call_next: Callable
    ):
        inbound_cid = request.headers.get("X-Correlation-ID")
        cid = set_correlation_id(inbound_cid)
        start_time = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Correlation-ID"] = cid
        metrics_registry.inc_counter(
            "reconcilex_http_requests_total",
            method=request.method,
            path=request.url.path,
            status_code=str(response.status_code),
        )
        logger.info(
            "HTTP request processed",
            extra={
                "context": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": elapsed_ms,
                }
            },
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        detail_msg = exc.detail if isinstance(exc.detail, str) else "Request error"
        payload = ErrorDetail(
            error_code=f"HTTP_{exc.status_code}",
            message=detail_msg,
            correlation_id=get_correlation_id(),
            details=None if isinstance(exc.detail, str) else exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.dict(),
            headers={"X-Correlation-ID": get_correlation_id()},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        payload = ErrorDetail(
            error_code="VALIDATION_ERROR",
            message="Request payload validation failed.",
            correlation_id=get_correlation_id(),
            details=exc.errors(),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=payload.dict(),
            headers={"X-Correlation-ID": get_correlation_id()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error(
            "Unhandled server exception",
            exc_info=exc,
            extra={"context": {"path": request.url.path, "method": request.method}},
        )
        payload = ErrorDetail(
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal error occurred.",
            correlation_id=get_correlation_id(),
            details=None,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=payload.dict(),
            headers={"X-Correlation-ID": get_correlation_id()},
        )

    # Register routers
    app.include_router(operations_router)
    app.include_router(sources_router)
    app.include_router(records_router)
    app.include_router(reconciliation_router)
    app.include_router(investigations_router)
    app.include_router(resolutions_router)

    return app


app = create_app()
