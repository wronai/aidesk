"""Tests for ParallelGroup — concurrent pipeline step execution."""
import asyncio
import os
import sys
import time

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_bus import EventBus
from pipeline import ParallelGroup, PipelineContext


class _SlowStep:
    """Mock step that sleeps for a given duration."""
    def __init__(self, name: str, delay: float = 0.05):
        self.name = name
        self._delay = delay
        self.executed = False

    def can_run(self, ctx):
        return True

    async def execute(self, ctx, bus):
        await asyncio.sleep(self._delay)
        self.executed = True
        return ctx


class _FailingStep:
    """Mock step that always raises."""
    def __init__(self, name: str = "fail"):
        self.name = name

    def can_run(self, ctx):
        return True

    async def execute(self, ctx, bus):
        raise RuntimeError(f"{self.name} exploded")


class _SkippedStep:
    """Mock step that can_run returns False."""
    def __init__(self, name: str = "skip"):
        self.name = name

    def can_run(self, ctx):
        return False

    async def execute(self, ctx, bus):
        raise AssertionError("Should not be called")


class TestParallelGroup:
    def test_runs_all_steps(self):
        s1 = _SlowStep("a", 0.01)
        s2 = _SlowStep("b", 0.01)
        s3 = _SlowStep("c", 0.01)
        group = ParallelGroup([s1, s2, s3])
        ctx = PipelineContext()
        bus = EventBus(enable_store=False)
        asyncio.get_event_loop().run_until_complete(group.execute(ctx, bus))
        assert s1.executed
        assert s2.executed
        assert s3.executed

    def test_parallel_is_faster_than_sequential(self):
        steps = [_SlowStep(f"s{i}", 0.05) for i in range(4)]
        group = ParallelGroup(steps)
        ctx = PipelineContext()
        bus = EventBus(enable_store=False)

        t0 = time.time()
        asyncio.get_event_loop().run_until_complete(group.execute(ctx, bus))
        elapsed = time.time() - t0

        # 4 steps × 50ms each = 200ms sequential, but parallel should be ~50ms
        assert elapsed < 0.15, f"Parallel took {elapsed:.3f}s, expected < 0.15s"

    def test_error_in_one_doesnt_block_others(self):
        s1 = _SlowStep("ok1", 0.01)
        s2 = _FailingStep("bad")
        s3 = _SlowStep("ok2", 0.01)
        group = ParallelGroup([s1, s2, s3])
        ctx = PipelineContext()
        bus = EventBus(enable_store=False)
        asyncio.get_event_loop().run_until_complete(group.execute(ctx, bus))

        assert s1.executed
        assert s3.executed
        assert "ok1" in ctx.steps_executed
        assert "ok2" in ctx.steps_executed
        assert any("bad" in e["step"] for e in ctx.errors)

    def test_skipped_steps_not_executed(self):
        s1 = _SlowStep("run", 0.01)
        s2 = _SkippedStep("skip")
        group = ParallelGroup([s1, s2])
        ctx = PipelineContext()
        bus = EventBus(enable_store=False)
        asyncio.get_event_loop().run_until_complete(group.execute(ctx, bus))

        assert s1.executed
        assert "run" in ctx.steps_executed
        assert "skip" not in ctx.steps_executed

    def test_can_run_true_if_any_step_runnable(self):
        s1 = _SkippedStep("skip")
        s2 = _SlowStep("run", 0.01)
        group = ParallelGroup([s1, s2])
        ctx = PipelineContext()
        assert group.can_run(ctx) is True

    def test_can_run_false_if_all_skipped(self):
        s1 = _SkippedStep("skip1")
        s2 = _SkippedStep("skip2")
        group = ParallelGroup([s1, s2])
        ctx = PipelineContext()
        assert group.can_run(ctx) is False

    def test_name_auto_generated(self):
        s1 = _SlowStep("alpha", 0.01)
        s2 = _SlowStep("beta", 0.01)
        group = ParallelGroup([s1, s2])
        assert group.name == "parallel(alpha,beta)"

    def test_name_custom(self):
        group = ParallelGroup([_SlowStep("a")], name="my_group")
        assert group.name == "my_group"

    def test_empty_runnable_returns_ctx(self):
        group = ParallelGroup([_SkippedStep("s1"), _SkippedStep("s2")])
        ctx = PipelineContext()
        bus = EventBus(enable_store=False)
        result = asyncio.get_event_loop().run_until_complete(group.execute(ctx, bus))
        assert result is ctx
