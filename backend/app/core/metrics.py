"""Minimal in-process metrics registry with Prometheus text exposition.

Scope: one Uvicorn worker, in-memory values. Counters reset on restart,
which matches the single-node deployment contract; swapping this module
for ``prometheus-client`` later does not change any call site.

Deliberately label-bounded: HTTP paths are normalized (UUIDs and digit
runs collapsed) before becoming label values so the label set cannot
grow without bound.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_DIGIT_RUN_PATTERN = re.compile(r"/\d+(?=/|$|\.)")


def normalize_path(path: str) -> str:
    """Collapse volatile path segments (UUIDs, numeric ids) into stable labels."""

    normalized = _UUID_PATTERN.sub("{uuid}", path)
    return _DIGIT_RUN_PATTERN.sub("/{id}", normalized)


def make_labels(**pairs: str) -> tuple[str, ...]:
    """Build an ordered ``key="value"`` label tuple from keyword arguments."""

    return tuple(f'{key}="{_escape(value)}"' for key, value in pairs.items())


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass
class Counter:
    values: dict[tuple[str, ...], int] = field(default_factory=dict)

    def inc(self, labels: tuple[str, ...] = (), amount: int = 1) -> None:
        self.values[labels] = self.values.get(labels, 0) + amount


@dataclass
class Gauge:
    values: dict[tuple[str, ...], float] = field(default_factory=dict)

    def set(self, labels: tuple[str, ...] = (), value: float = 0.0) -> None:
        self.values[labels] = value

    def inc(self, labels: tuple[str, ...] = (), amount: float = 1.0) -> None:
        self.values[labels] = self.values.get(labels, 0.0) + amount

    def dec(self, labels: tuple[str, ...] = (), amount: float = 1.0) -> None:
        self.values[labels] = self.values.get(labels, 0.0) - amount


@dataclass
class Summary:
    # Prometheus summary approximation: cumulative count + sum only.
    count: dict[tuple[str, ...], int] = field(default_factory=dict)
    total: dict[tuple[str, ...], float] = field(default_factory=dict)

    def observe(self, labels: tuple[str, ...] = (), value: float = 0.0) -> None:
        self.count[labels] = self.count.get(labels, 0) + 1
        self.total[labels] = self.total.get(labels, 0.0) + value


class MetricsRegistry:
    """Thread-safe registry of labeled counters, gauges, and summaries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._summaries: dict[str, Summary] = {}

    def counter(self, name: str) -> Counter:
        with self._lock:
            return self._counters.setdefault(name, Counter())

    def gauge(self, name: str) -> Gauge:
        with self._lock:
            return self._gauges.setdefault(name, Gauge())

    def summary(self, name: str) -> Summary:
        with self._lock:
            return self._summaries.setdefault(name, Summary())

    def render(self) -> str:
        """Render the registry in Prometheus text exposition format 0.0.4."""

        with self._lock:
            lines: list[str] = []
            for name, counter in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                for labels in sorted(counter.values):
                    lines.append(f"{name}{_render_labels(labels)} {counter.values[labels]}")
            for name, gauge in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                for labels in sorted(gauge.values):
                    lines.append(f"{name}{_render_labels(labels)} {gauge.values[labels]}")
            for name, summary in sorted(self._summaries.items()):
                lines.append(f"# TYPE {name} summary")
                for labels in sorted(summary.count):
                    lines.append(f"{name}_count{_render_labels(labels)} {summary.count[labels]}")
                    lines.append(
                        f"{name}_sum{_render_labels(labels)} {summary.total[labels]:.6f}"
                    )
            return "\n".join(lines) + "\n" if lines else ""


def _render_labels(labels: tuple[str, ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(labels) + "}"


registry = MetricsRegistry()

HTTP_REQUESTS = registry.counter("http_requests_total")
HTTP_REQUEST_DURATION = registry.summary("http_request_duration_seconds")
HTTP_IN_FLIGHT = registry.gauge("http_requests_in_flight")
RATE_LIMIT_REJECTIONS = registry.counter("http_rate_limit_rejections_total")
AGENT_REPAIR_PATH = registry.counter("agent_repair_path_total")
AGENT_UNKNOWN_RULE = registry.counter("agent_unknown_rule_total")


def render_metrics() -> str:
    return registry.render()
