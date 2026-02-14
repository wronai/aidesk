"""
Observability — lightweight tracing, metrics, and span tracking.

Provides structured tracing for pipeline steps and API calls without
requiring OpenTelemetry as a dependency. Compatible with OTel concepts
(traces, spans, attributes) for easy migration later.

Usage:
    from observability import Tracer, get_tracer

    tracer = get_tracer()

    # Trace a pipeline run
    with tracer.span("pipeline.run", attributes={"run_id": "abc"}) as span:
        span.set_attribute("steps", 8)
        with tracer.span("analyze", parent=span) as child:
            child.set_attribute("tokens", 150)

    # Get metrics
    tracer.get_metrics()  # → aggregated span stats
"""
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import nfo
import structlog

logger = structlog.get_logger()


@dataclass
class Span:
    """
    A single traced operation (OTel-compatible concept).

    Tracks name, duration, attributes, status, and parent-child relationships.
    """
    name: str
    trace_id: str = ""
    span_id: str = ""
    parent_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # "ok", "error"
    error_message: str = ""

    def __post_init__(self):
        if not self.span_id:
            self.span_id = str(uuid.uuid4())[:12]
        if not self.trace_id:
            self.trace_id = str(uuid.uuid4())[:16]
        if self.start_time == 0.0:
            self.start_time = time.time()

    @property
    def duration_ms(self) -> float:
        if self.end_time > 0:
            return round((self.end_time - self.start_time) * 1000, 2)
        return round((time.time() - self.start_time) * 1000, 2)

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def set_error(self, error: str):
        self.status = "error"
        self.error_message = error

    def finish(self):
        if self.end_time == 0.0:
            self.end_time = time.time()

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "error": self.error_message or None,
        }


class Tracer:
    """
    Lightweight tracer that collects spans and computes metrics.

    Thread-safe for single-writer (pipeline loop) usage.
    Keeps a rolling buffer of recent spans for debugging.
    """

    def __init__(self, service_name: str = "proxeen", max_spans: int = 500):
        self.service_name = service_name
        self._max_spans = max_spans
        self._spans: List[Span] = []
        self._metrics: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_ms": 0.0, "errors": 0, "min_ms": float("inf"), "max_ms": 0.0}
        )

    @contextmanager
    def span(self, name: str, parent: Optional[Span] = None, attributes: Optional[Dict] = None):
        """
        Context manager that creates, yields, and finishes a Span.

        Usage:
            with tracer.span("step_name") as s:
                s.set_attribute("key", "value")
                # ... do work ...
        """
        s = Span(
            name=name,
            trace_id=parent.trace_id if parent else "",
            parent_id=parent.span_id if parent else "",
            attributes=attributes or {},
        )
        try:
            yield s
        except Exception as e:
            s.set_error(str(e))
            raise
        finally:
            s.finish()
            self._record(s)

    def _record(self, span: Span):
        """Record a finished span into buffer and metrics."""
        self._spans.append(span)
        if len(self._spans) > self._max_spans:
            self._spans = self._spans[-self._max_spans:]

        m = self._metrics[span.name]
        m["count"] += 1
        m["total_ms"] += span.duration_ms
        if span.status == "error":
            m["errors"] += 1
        m["min_ms"] = min(m["min_ms"], span.duration_ms)
        m["max_ms"] = max(m["max_ms"], span.duration_ms)

    def get_metrics(self) -> Dict[str, Dict]:
        """Get aggregated metrics per span name."""
        result = {}
        for name, m in self._metrics.items():
            result[name] = {
                "count": m["count"],
                "total_ms": round(m["total_ms"], 2),
                "avg_ms": round(m["total_ms"] / m["count"], 2) if m["count"] > 0 else 0,
                "min_ms": round(m["min_ms"], 2) if m["min_ms"] != float("inf") else 0,
                "max_ms": round(m["max_ms"], 2),
                "errors": m["errors"],
                "error_rate": round(m["errors"] / m["count"], 4) if m["count"] > 0 else 0,
            }
        return result

    def get_recent_spans(self, n: int = 20, name: Optional[str] = None) -> List[Dict]:
        """Get recent spans, optionally filtered by name."""
        spans = self._spans
        if name:
            spans = [s for s in spans if s.name == name]
        return [s.to_dict() for s in spans[-n:]]

    def get_trace(self, trace_id: str) -> List[Dict]:
        """Get all spans belonging to a trace."""
        return [s.to_dict() for s in self._spans if s.trace_id == trace_id]

    def get_stats(self) -> Dict:
        """Get tracer statistics."""
        return {
            "service": self.service_name,
            "total_spans": len(self._spans),
            "unique_operations": len(self._metrics),
            "metrics": self.get_metrics(),
        }

    def reset(self):
        """Clear all spans and metrics."""
        self._spans.clear()
        self._metrics.clear()


# ===== Global tracer singleton =====

_tracer: Optional[Tracer] = None


def get_tracer(service_name: str = "proxeen") -> Tracer:
    """Get or create the global tracer instance."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name=service_name)
    return _tracer


def reset_tracer():
    """Reset the global tracer (for testing)."""
    global _tracer
    _tracer = None
