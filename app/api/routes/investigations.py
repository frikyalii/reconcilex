"""REST API endpoints for LangGraph Discrepancy Investigations."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.session import DatabaseSession, get_db_session
from app.repositories.investigation_repo import InvestigationRepository
from app.schemas.domain import InvestigationRead, ResolutionActionRead
from app.services.investigation.service import InvestigationService

router = APIRouter(prefix="/api/v1", tags=["Investigation"])


@router.post(
    "/discrepancies/{discrepancy_id}/investigations",
    response_model=InvestigationRead,
    status_code=status.HTTP_201_CREATED,
)
def trigger_discrepancy_investigation(
    discrepancy_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> InvestigationRead:
    """Trigger a constrained LangGraph root-cause investigation for a detected discrepancy."""
    service = InvestigationService(db)
    try:
        inv, _ = service.run_investigation(discrepancy_id=discrepancy_id)
        return inv
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationRead,
)
def get_investigation(
    investigation_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> InvestigationRead:
    """Retrieve a specific discrepancy investigation by ID."""
    repo = InvestigationRepository(db)
    inv = repo.get_investigation(investigation_id)
    if inv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation '{investigation_id}' not found.",
        )
    return inv


@router.post(
    "/investigations/{investigation_id}/retry",
    response_model=InvestigationRead,
)
def retry_investigation(
    investigation_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> InvestigationRead:
    """Retry a failed or inconclusive discrepancy investigation."""
    service = InvestigationService(db)
    try:
        inv, _ = service.retry_investigation(investigation_id=investigation_id)
        return inv
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/discrepancies/{discrepancy_id}/resolutions",
    response_model=List[ResolutionActionRead],
)
def list_discrepancy_resolutions(
    discrepancy_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> List[ResolutionActionRead]:
    """List proposed or executed resolution actions associated with a discrepancy."""
    repo = InvestigationRepository(db)
    return repo.list_resolutions_for_discrepancy(discrepancy_id)
