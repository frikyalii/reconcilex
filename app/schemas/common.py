"""Common Pydantic schemas for operational health, readiness, pagination, and error envelopes.

Compatible with both Pydantic v1 (`1.10+`) and Pydantic v2 (`2.0+`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.db.base import quantize_amount

try:
    from pydantic import ConfigDict, field_serializer  # type: ignore

    _IS_PYDANTIC_V2 = True
except ImportError:
    _IS_PYDANTIC_V2 = False


class DecimalSafeBaseModel(BaseModel):
    """Base Pydantic model that serializes Decimal values as exact fixed-scale strings (not floats)
    and provides `.dict()` compatibility across Pydantic v1 and v2.
    """

    if _IS_PYDANTIC_V2:
        model_config = ConfigDict(
            populate_by_name=True,
            ser_json_timedelta="iso8601",
        )

        def dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
            data = super().model_dump(*args, **kwargs)
            return _convert_decimals_recursive(data)

        def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
            data = super().model_dump(*args, **kwargs)
            return _convert_decimals_recursive(data)

    else:

        class Config:
            json_encoders = {
                Decimal: lambda v: str(quantize_amount(v)),
            }


def _convert_decimals_recursive(obj: Any) -> Any:
    """Ensure any Decimal values inside dicts/lists are formatted as 4-decimal strings in Pydantic v2."""
    if isinstance(obj, Decimal):
        return str(quantize_amount(obj))
    if isinstance(obj, dict):
        return {k: _convert_decimals_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_decimals_recursive(item) for item in obj]
    return obj


class ErrorDetail(DecimalSafeBaseModel):
    """Structured error envelope returned on validation or operational errors."""

    error_code: str = Field(..., description="Machine-readable error identifier")
    message: str = Field(..., description="Human-readable safe error message")
    correlation_id: str = Field(..., description="Request correlation ID for log tracing")
    details: Optional[Any] = Field(default=None, description="Optional structured validation details")


class HealthResponse(DecimalSafeBaseModel):
    """Liveness probe response."""

    status: str = Field(default="ok", description="Service liveness status")
    app_name: str
    version: str
    environment: str
    correlation_id: str


class ReadinessCheckItem(DecimalSafeBaseModel):
    """Status of an individual backing dependency."""

    component: str
    ready: bool
    detail: Optional[str] = None


class ReadinessResponse(DecimalSafeBaseModel):
    """Readiness probe response verifying database and schema state."""

    status: str = Field(..., description="'ready' or 'degraded'")
    checks: List[ReadinessCheckItem]
    correlation_id: str


def validate_non_float_decimal(val: Any) -> Decimal:
    """Reject binary float values and quantize financial amounts to 4 decimal places."""
    if isinstance(val, float):
        raise ValueError(
            "Binary floating-point values are not permitted for monetary fields. "
            "Provide a string, integer, or Decimal."
        )
    return quantize_amount(val)
