"""REST API endpoints for DataSource management, validation, and data ingestion."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.db.session import DatabaseSession, get_db_session
from app.observability.metrics import metrics_registry
from app.repositories.sources_repo import DuplicateEntityError, SourcesRepository
from app.schemas.domain import (
    DataSourceCreate,
    DataSourceRead,
    SourceImportReport,
    SourceImportRequest,
    SourceValidationReport,
)
from app.services.ingestion.adapters import IngestionService

router = APIRouter(prefix="/api/v1/sources", tags=["Sources"])


@router.post("", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: DataSourceCreate,
    db: DatabaseSession = Depends(get_db_session),
) -> DataSourceRead:
    """Register a new DataSource and optional SchemaMapping."""
    repo = SourcesRepository(db)
    try:
        created = repo.create_source(payload)
        metrics_registry.inc_counter(
            "reconcilex_sources_created_total",
            source_type=created.source_type.value,
            target_entity=created.target_entity.value,
        )
        return created
    except DuplicateEntityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("", response_model=List[DataSourceRead])
def list_sources(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: DatabaseSession = Depends(get_db_session),
) -> List[DataSourceRead]:
    """List registered data sources with pagination."""
    repo = SourcesRepository(db)
    return repo.list_sources(limit=limit, offset=offset)


@router.get("/{source_id}", response_model=DataSourceRead)
def get_source(
    source_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> DataSourceRead:
    """Retrieve a single DataSource and its active SchemaMapping."""
    repo = SourcesRepository(db)
    source = repo.get_source(source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataSource '{source_id}' not found.",
        )
    return source


@router.post("/{source_id}/validate", response_model=SourceValidationReport)
def validate_source(
    source_id: str,
    request: Optional[SourceImportRequest] = Body(default=None),
    db: DatabaseSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SourceValidationReport:
    """Validate source records against the source's SchemaMapping without persisting."""
    service = IngestionService(db, max_upload_bytes=settings.MAX_UPLOAD_BYTES)
    try:
        return service.validate_source_data(source_id, request=request)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{source_id}/import", response_model=SourceImportReport)
def import_source(
    source_id: str,
    request: Optional[SourceImportRequest] = Body(default=None),
    db: DatabaseSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SourceImportReport:
    """Validate and import valid records from the configured source, returning a row-level report."""
    service = IngestionService(db, max_upload_bytes=settings.MAX_UPLOAD_BYTES)
    try:
        report = service.import_source_data(source_id, request=request)
        metrics_registry.inc_counter(
            "reconcilex_records_imported_total",
            value=float(report.imported_rows),
            target_entity=report.target_entity.value,
        )
        return report
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
