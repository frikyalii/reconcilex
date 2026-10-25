"""OpenTelemetry tracing instrumentation and span context manager for ReconcileX.

Uses `opentelemetry-api` / `opentelemetry-sdk` when installed in the environment,
and provides a lightweight, zero-dependency span recorder fallback for local testing
and inspection.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Dict, Generator, List

from app.core.logging import get_correlation_id

try:
    from opentelemetry import trace  # type: ignore

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


@dataclass
class RecordedSpan:
    name: str
    correlation_id: str
    duration_ms: float
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = "OK"


class SpanRecorder:
    """Thread-safe recorder of recent spans for observability diagnostics and tests."""

    def __init__(self, max_spans: int = 500) -> None:
        self._lock = threading.Lock()
        self._max_spans = max_spans
        self._spans: List[RecordedSpan] = []

    def record(self, span: RecordedSpan) -> None:
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self._max_spans:
                self._spans = self._spans[-self._max_spans :]

    def list_spans(self) -> List[RecordedSpan]:
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


span_recorder = SpanRecorder()


@contextmanager
def trace_span(name: str, **attributes: Any) -> Generator[Dict[str, Any], None, None]:
    """Context manager recording an OpenTelemetry / diagnostic span with duration and correlation ID."""
    cid = get_correlation_id()
    span_attrs: Dict[str, Any] = {"correlation_id": cid, **attributes}
    start_ns = time.perf_counter_ns()
    status = "OK"
    try:
        if HAS_OTEL:
            tracer = trace.get_tracer("reconcilex")
            with tracer.start_as_current_span(name) as otel_span:
                for k, v in span_attrs.items():
                    otel_span.set_attribute(str(k), str(v))
                yield span_attrs
        else:
            yield span_attrs
    except Exception as exc:
        status = f"ERROR:{type(exc).__name__}"
        raise
    finally:
        elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
        span_recorder.record(
            RecordedSpan(
                name=name,
                correlation_id=cid,
                duration_ms=elapsed_ms,
                attributes=span_attrs,
                status=status,
            )
        )
