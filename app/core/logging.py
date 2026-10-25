"""Structured logging with correlation ID propagation and credential redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

# Context variable holding the current request or job correlation ID
_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

SENSITIVE_FIELD_PATTERNS = re.compile(
    r"(password|secret|token|api_key|apikey|authorization|credential|card_number|cvv)",
    re.IGNORECASE,
)

BEARER_VALUE_PATTERN = re.compile(
    r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*",
    re.IGNORECASE,
)


def get_correlation_id() -> str:
    """Return the active correlation ID or generate a fallback ID."""
    cid = _correlation_id_ctx.get()
    if not cid:
        cid = str(uuid.uuid4())
        _correlation_id_ctx.set(cid)
    return cid


def set_correlation_id(correlation_id: str | None = None) -> str:
    """Set the correlation ID in the current execution context."""
    cid = correlation_id or str(uuid.uuid4())
    _correlation_id_ctx.set(cid)
    return cid


def redact_sensitive_value(key: str, value: Any) -> Any:
    """Recursively redact sensitive keys and Bearer tokens from structured data."""
    if SENSITIVE_FIELD_PATTERNS.search(str(key)):
        return "***REDACTED***"
    if isinstance(value, Mapping):
        return {k: redact_sensitive_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_value(key, item) for item in value]
    if isinstance(value, str):
        return BEARER_VALUE_PATTERN.sub(r"\1***REDACTED***", value)
    return value


def redact_log_message(message: str) -> str:
    """Redact inline secret patterns in plain log messages."""
    msg = BEARER_VALUE_PATTERN.sub(r"\1***REDACTED***", message)
    msg = re.sub(
        r"((?:password|secret|token|api_key|apikey)\s*[=:]\s*)([^\s,;\"']+)",
        r"\1***REDACTED***",
        msg,
        flags=re.IGNORECASE,
    )
    return msg


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON with correlation IDs and redaction."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": get_correlation_id(),
            "message": redact_log_message(record.getMessage()),
        }

        # Include structured extra fields if passed via `extra={"context": ...}`
        extra_context = getattr(record, "context", None)
        if isinstance(extra_context, Mapping):
            payload["context"] = {
                str(k): redact_sensitive_value(str(k), v) for k, v in extra_context.items()
            }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure application-wide root logger."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers on repeated initialization
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s (cid=%(correlation_id)s): %(message)s"
            )
        )
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)
