"""Tests for observability module — tracing, spans, metrics."""
import os
import sys
import time

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import Span, Tracer, get_tracer, reset_tracer


class TestSpan:
    def test_auto_generates_ids(self):
        s = Span(name="test")
        assert s.span_id
        assert s.trace_id
        assert s.start_time > 0

    def test_set_attribute(self):
        s = Span(name="test")
        s.set_attribute("key", "value")
        assert s.attributes["key"] == "value"

    def test_set_error(self):
        s = Span(name="test")
        s.set_error("boom")
        assert s.status == "error"
        assert s.error_message == "boom"

    def test_duration_ms(self):
        s = Span(name="test")
        time.sleep(0.01)
        s.finish()
        assert s.duration_ms > 0

    def test_to_dict(self):
        s = Span(name="op")
        s.set_attribute("x", 1)
        s.finish()
        d = s.to_dict()
        assert d["name"] == "op"
        assert d["status"] == "ok"
        assert d["attributes"]["x"] == 1
        assert d["duration_ms"] >= 0

    def test_finish_sets_end_time(self):
        s = Span(name="test")
        assert s.end_time == 0.0
        s.finish()
        assert s.end_time > 0

    def test_parent_id(self):
        parent = Span(name="parent")
        child = Span(name="child", trace_id=parent.trace_id, parent_id=parent.span_id)
        assert child.parent_id == parent.span_id
        assert child.trace_id == parent.trace_id


class TestTracer:
    def test_span_context_manager(self):
        t = Tracer()
        with t.span("test_op") as s:
            s.set_attribute("k", "v")
        assert len(t._spans) == 1
        assert t._spans[0].status == "ok"

    def test_span_records_error(self):
        t = Tracer()
        with pytest.raises(ValueError):
            with t.span("fail_op") as s:
                raise ValueError("test error")
        assert len(t._spans) == 1
        assert t._spans[0].status == "error"
        assert "test error" in t._spans[0].error_message

    def test_parent_child_spans(self):
        t = Tracer()
        with t.span("parent") as parent:
            with t.span("child", parent=parent) as child:
                pass
        assert len(t._spans) == 2
        # child finishes first (inner context exits first)
        child_span = [s for s in t._spans if s.name == "child"][0]
        assert child_span.parent_id == parent.span_id
        assert child_span.trace_id == parent.trace_id

    def test_metrics_aggregation(self):
        t = Tracer()
        for _ in range(5):
            with t.span("op_a"):
                pass
        with t.span("op_b"):
            pass

        metrics = t.get_metrics()
        assert metrics["op_a"]["count"] == 5
        assert metrics["op_b"]["count"] == 1
        assert metrics["op_a"]["avg_ms"] >= 0

    def test_metrics_error_rate(self):
        t = Tracer()
        with t.span("ok_op"):
            pass
        try:
            with t.span("ok_op"):
                raise RuntimeError("fail")
        except RuntimeError:
            pass

        metrics = t.get_metrics()
        assert metrics["ok_op"]["count"] == 2
        assert metrics["ok_op"]["errors"] == 1
        assert metrics["ok_op"]["error_rate"] == 0.5

    def test_get_recent_spans(self):
        t = Tracer()
        for i in range(10):
            with t.span(f"op_{i % 3}"):
                pass
        recent = t.get_recent_spans(n=5)
        assert len(recent) == 5

    def test_get_recent_spans_filtered(self):
        t = Tracer()
        for _ in range(3):
            with t.span("alpha"):
                pass
        for _ in range(3):
            with t.span("beta"):
                pass
        filtered = t.get_recent_spans(name="alpha")
        assert len(filtered) == 3
        assert all(s["name"] == "alpha" for s in filtered)

    def test_get_trace(self):
        t = Tracer()
        with t.span("root") as root:
            with t.span("child1", parent=root):
                pass
            with t.span("child2", parent=root):
                pass
        trace = t.get_trace(root.trace_id)
        assert len(trace) == 3

    def test_max_spans_pruning(self):
        t = Tracer(max_spans=10)
        for i in range(20):
            with t.span(f"op_{i}"):
                pass
        assert len(t._spans) == 10

    def test_get_stats(self):
        t = Tracer(service_name="test_svc")
        with t.span("op"):
            pass
        stats = t.get_stats()
        assert stats["service"] == "test_svc"
        assert stats["total_spans"] == 1
        assert stats["unique_operations"] == 1

    def test_reset(self):
        t = Tracer()
        with t.span("op"):
            pass
        t.reset()
        assert len(t._spans) == 0
        assert len(t._metrics) == 0


class TestGlobalTracer:
    def test_get_tracer_singleton(self):
        reset_tracer()
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_reset_tracer(self):
        reset_tracer()
        t1 = get_tracer()
        reset_tracer()
        t2 = get_tracer()
        assert t1 is not t2
