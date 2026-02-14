"""Tests for circuit breaker, retry with backoff, and StepGuard."""
import asyncio
import os
import sys
import time

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    StepGuard,
    is_transient_error,
    retry_with_backoff,
    wrap_step_with_guard,
)
from event_bus import EventBus
from pipeline import PipelineContext


# ===== CircuitBreaker =====

class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute()

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute()

    def test_opens_at_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.can_execute()
        assert cb.total_opens == 1

    def test_open_blocks_execution(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=60.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.can_execute()

    def test_open_to_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.01)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.02)
        assert cb.can_execute()  # transitions to half-open
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # → half-open
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # → half-open
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_get_stats(self):
        cb = CircuitBreaker(name="test_stats")
        cb.record_failure()
        stats = cb.get_stats()
        assert stats["name"] == "test_stats"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 1


# ===== is_transient_error =====

class TestTransientErrorDetection:
    def test_429_is_transient(self):
        assert is_transient_error(Exception("HTTP 429 Too Many Requests"))

    def test_timeout_is_transient(self):
        assert is_transient_error(Exception("Connection timed out"))

    def test_503_is_transient(self):
        assert is_transient_error(Exception("503 Service Temporarily Unavailable"))

    def test_rate_limit_is_transient(self):
        assert is_transient_error(Exception("Rate limit exceeded"))

    def test_syntax_error_not_transient(self):
        assert not is_transient_error(Exception("SyntaxError: invalid syntax"))

    def test_auth_error_not_transient(self):
        assert not is_transient_error(Exception("401 Unauthorized"))


# ===== retry_with_backoff =====

class TestRetryWithBackoff:
    def test_succeeds_first_try(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = asyncio.get_event_loop().run_until_complete(
            retry_with_backoff(fn, max_retries=3)
        )
        assert result == "ok"
        assert call_count == 1

    def test_retries_transient_then_succeeds(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("HTTP 429 rate limit")
            return "ok"

        result = asyncio.get_event_loop().run_until_complete(
            retry_with_backoff(fn, max_retries=3, base_delay=0.01)
        )
        assert result == "ok"
        assert call_count == 3

    def test_raises_after_max_retries(self):
        async def fn():
            raise Exception("HTTP 429 rate limit")

        with pytest.raises(Exception, match="429"):
            asyncio.get_event_loop().run_until_complete(
                retry_with_backoff(fn, max_retries=2, base_delay=0.01)
            )

    def test_no_retry_for_non_transient(self):
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            raise Exception("Invalid API key")

        with pytest.raises(Exception, match="Invalid"):
            asyncio.get_event_loop().run_until_complete(
                retry_with_backoff(fn, max_retries=3, base_delay=0.01)
            )
        assert call_count == 1  # no retries


# ===== StepGuard =====

class _MockStep:
    """Mock pipeline step for testing."""
    name = "mock_step"
    call_count = 0
    should_fail = False
    fail_message = "mock error"

    def can_run(self, ctx):
        return True

    async def execute(self, ctx, bus):
        self.call_count += 1
        if self.should_fail:
            raise Exception(self.fail_message)
        return ctx


class TestStepGuard:
    def test_delegates_name(self):
        step = _MockStep()
        guard = StepGuard(step)
        assert guard.name == "mock_step"

    def test_delegates_can_run(self):
        step = _MockStep()
        guard = StepGuard(step)
        ctx = PipelineContext()
        assert guard.can_run(ctx)

    def test_success_passes_through(self):
        step = _MockStep()
        guard = StepGuard(step)
        ctx = PipelineContext()
        bus = EventBus()
        result = asyncio.get_event_loop().run_until_complete(guard.execute(ctx, bus))
        assert result == ctx
        assert step.call_count == 1

    def test_circuit_opens_after_threshold(self):
        step = _MockStep()
        step.should_fail = True
        step.fail_message = "API error"
        guard = StepGuard(step, failure_threshold=3, max_retries=0)
        ctx = PipelineContext()
        bus = EventBus()

        for _ in range(3):
            try:
                asyncio.get_event_loop().run_until_complete(guard.execute(ctx, bus))
            except Exception:
                pass

        # Circuit should be open now
        assert not guard.can_run(ctx)

    def test_breaker_stats(self):
        step = _MockStep()
        guard = StepGuard(step)
        stats = guard.get_breaker_stats()
        assert stats["name"] == "mock_step"
        assert stats["state"] == "closed"

    def test_wrap_step_with_guard_factory(self):
        step = _MockStep()
        guard = wrap_step_with_guard(step, failure_threshold=10)
        assert guard.name == "mock_step"
        assert guard._breaker.failure_threshold == 10
