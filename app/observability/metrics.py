"""Prometheus-compatible metrics registry and exporter for ReconcileX."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, Tuple


class MetricsRegistry:
    """Thread-safe Prometheus text-format metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = defaultdict(float)

    def inc_counter(self, name: str, value: float = 1.0, **labels: str) -> None:
        label_key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        with self._lock:
            self._counters[(name, label_key)] += value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        label_key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        with self._lock:
            self._gauges[(name, label_key)] = value

    def render_prometheus_text(self) -> str:
        """Render registered counters and gauges in Prometheus exposition format."""
        lines: list[str] = [
            "# HELP reconcilex_up Whether the ReconcileX backend service is healthy.",
            "# TYPE reconcilex_up gauge",
            "reconcilex_up 1",
        ]
        with self._lock:
            for (name, label_key), val in sorted(self._counters.items()):
                label_str = self._format_labels(label_key)
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{label_str} {val}")
            for (name, label_key), val in sorted(self._gauges.items()):
                label_str = self._format_labels(label_key)
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{label_str} {val}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_labels(label_key: Tuple[Tuple[str, str], ...]) -> str:
        if not label_key:
            return ""
        inner = ",".join(f'{k}="{v}"' for k, v in label_key)
        return f"{{{inner}}}"


metrics_registry = MetricsRegistry()
