"""REST API endpoints for Human-in-the-Loop Resolution Approval, Rejection, and Execution.

Security Controls:
- `approve`, `reject`, and `execute` endpoints enforce Role-Based Access Control
  (`require_roles(["operator", "admin"])`) via the `X-API-Key` header.
- Every state change records the authenticated `actor_id` and `actor_role` in
  both `approvals` and `audit_events`.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.core.security import Principal, require_roles
from app.db.session import DatabaseSession, get_db_session
from app.repositories.audit_repo import AuditRepository
from app.repositories.resolution_repo import ResolutionRepository
from app.schemas.domain import (
    ApprovalRead,
    AuditEventRead,
    ResolutionActionCreate,
    ResolutionActionRead,
    ResolutionDecisionRequest,
    ResolutionExecuteRequest,
)
from app.services.resolution.service import ResolutionService

router = APIRouter(prefix="/api/v1/resolutions", tags=["Resolution"])


@router.post(
    "",
    response_model=ResolutionActionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_resolution_action(
    payload: ResolutionActionCreate,
    principal: Principal = Depends(require_roles(["operator", "admin"])),
    db: DatabaseSession = Depends(get_db_session),
) -> ResolutionActionRead:
    """Create a proposed ResolutionAction requiring human approval."""
    service = ResolutionService(db)
    try:
        return service.create_resolution(
            payload=payload,
            actor_id=principal.actor_id,
            actor_role=principal.role,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/{resolution_id}", response_model=ResolutionActionRead)
def get_resolution_action(
    resolution_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> ResolutionActionRead:
    """Retrieve a ResolutionAction by ID."""
    repo = ResolutionRepository(db)
    res = repo.get_resolution(resolution_id)
    if res is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ResolutionAction '{resolution_id}' not found.",
        )
    return res


@router.post("/{resolution_id}/approve", response_model=ResolutionActionRead)
def approve_resolution_action(
    resolution_id: str,
    payload: Optional[ResolutionDecisionRequest] = Body(default=None),
    principal: Principal = Depends(require_roles(["operator", "admin"])),
    db: DatabaseSession = Depends(get_db_session),
) -> ResolutionActionRead:
    """Approve a proposed ResolutionAction (requires operator or admin role)."""
    req = payload or ResolutionDecisionRequest()
    service = ResolutionService(db)
    try:
        return service.approve_resolution(
            resolution_id=resolution_id,
            actor_id=principal.actor_id,
            actor_role=principal.role,
            comment=req.comment,
        )
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


@router.post("/{resolution_id}/reject", response_model=ResolutionActionRead)
def reject_resolution_action(
    resolution_id: str,
    payload: Optional[ResolutionDecisionRequest] = Body(default=None),
    principal: Principal = Depends(require_roles(["operator", "admin"])),
    db: DatabaseSession = Depends(get_db_session),
) -> ResolutionActionRead:
    """Reject a proposed ResolutionAction (requires operator or admin role)."""
    req = payload or ResolutionDecisionRequest()
    service = ResolutionService(db)
    try:
        return service.reject_resolution(
            resolution_id=resolution_id,
            actor_id=principal.actor_id,
            actor_role=principal.role,
            comment=req.comment,
        )
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


@router.post("/{resolution_id}/execute", response_model=ResolutionActionRead)
def execute_resolution_action(
    resolution_id: str,
    payload: Optional[ResolutionExecuteRequest] = Body(default=None),
    principal: Principal = Depends(require_roles(["operator", "admin"])),
    db: DatabaseSession = Depends(get_db_session),
) -> ResolutionActionRead:
    """Execute an approved ResolutionAction idempotently and run post-resolution verification."""
    req = payload or ResolutionExecuteRequest()
    service = ResolutionService(db)
    try:
        return service.execute_resolution(
            resolution_id=resolution_id,
            actor_id=principal.actor_id,
            actor_role=principal.role,
            simulate_noop=req.simulate_noop,
        )
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


@router.get("/{resolution_id}/approvals", response_model=List[ApprovalRead])
def list_resolution_approvals(
    resolution_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> List[ApprovalRead]:
    """List approval/rejection decisions recorded for a ResolutionAction."""
    repo = ResolutionRepository(db)
    return repo.list_approvals_for_resolution(resolution_id)


@router.get("/{resolution_id}/audit-trail", response_model=List[AuditEventRead])
def list_resolution_audit_trail(
    resolution_id: str,
    db: DatabaseSession = Depends(get_db_session),
) -> List[AuditEventRead]:
    """List immutable audit events recorded for a ResolutionAction."""
    audit_repo = AuditRepository(db)
    return audit_repo.list_events_for_entity("resolution_action", resolution_id)
