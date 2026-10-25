"""Authentication and role-based authorization dependencies for ReconcileX.

Sensitive operations (approving resolutions, executing recovery actions, modifying
reconciliation rules) require explicit operator or admin roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings

RoleType = str  # "viewer" | "operator" | "admin"


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity and role."""

    actor_id: str
    role: RoleType


def get_current_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Authenticate caller using the X-API-Key header and return Principal."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header.",
        )
    key_map = settings.parse_api_keys()
    role = key_map.get(x_api_key)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    actor_id = (x_actor_id or f"{role}-user").strip()
    return Principal(actor_id=actor_id, role=role)


def require_roles(allowed_roles: Sequence[RoleType]) -> Callable[[Principal], Principal]:
    """Dependency factory enforcing that the authenticated caller has one of `allowed_roles`."""

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' is not authorized. Required one of: {list(allowed_roles)}.",
            )
        return principal

    return _dependency
