"""
Circuit Breaker + Retry with Exponential Backoff for Pipeline resilience.

Provides:
- CircuitBreaker: prevents repeated calls to failing services (API outages)
- RetryWithBackoff: retries transient errors (HTTP 429, timeouts) with exponential delay
- StepGuard: wraps a PipelineStep with circuit breaker + retry logic

States:
  CLOSED  → normal operation, failures counted
  OPEN    → step disabled (too many failures), fast-fail for reset_timeout seconds
  HALF_OPEN → single probe allowed to test recovery

Integrates with:
- PipelineOrchestrator (pipeline.py) — wraps AnalyzeStep and other API-calling steps
- EventBus (event_bus.py) — emits circuit_breaker.opened / .closed events
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set

import structlog

logger = structlog.get_logger()


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Circuit breaker that disables a component after repeated failures.

    Prevents cascading failures and unnecessary API costs during outages.
    """
    name: str = "default"
    failure_threshold: int = 5
    reset_timeout: float = 60.0
    half_open_max_calls: int = 1

    # Internal state
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = 0.0
    total_opens: int = 0
    total_half_open_probes: int = 0
    _half_open_calls: int = 0

    def __post_init__(self):
        self.last_state_change = time.time()

    def can_execute(self) -> bool:
        """Check if the guarded operation should be attempted."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if reset timeout has elapsed → transition to half-open
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self._transition(CircuitState.HALF_OPEN)
                self._half_open_calls = 0
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow limited probes
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                self.total_half_open_probes += 1
                return True
            return False

        return False

    def record_success(self):
        """Record a successful operation."""
        self.success_count += 1
        if self.state == CircuitState.HALF_OPEN:
            # Recovery confirmed → close circuit
            self._transition(CircuitState.CLOSED)
            self.failure_count = 0
            logger.info("Circuit breaker closed (recovered)", name=self.name)
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0

    def record_failure(self):
        """Record a failed operation."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Probe failed → back to open
            self._transition(CircuitState.OPEN)
            logger.warning("Circuit breaker re-opened (probe failed)", name=self.name)

        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self._transition(CircuitState.OPEN)
                self.total_opens += 1
                logger.warning(
                    "Circuit breaker opened",
                    name=self.name,
                    failures=self.failure_count,
                    threshold=self.failure_threshold,
                    reset_timeout=self.reset_timeout,
                )

    def _transition(self, new_state: CircuitState):
        self.state = new_state
        self.last_state_change = time.time()

    def get_stats(self) -> Dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout,
            "total_opens": self.total_opens,
            "total_half_open_probes": self.total_half_open_probes,
            "time_in_state_s": round(time.time() - self.last_state_change, 1),
        }


# ===== Transient error detection =====

_TRANSIENT_ERROR_SUBSTRINGS = {
    "429", "rate limit", "too many requests",
    "timeout", "timed out",
    "connection refused", "connection reset",
    "502", "503", "504",
    "temporarily unavailable",
    "retry",
}


def is_transient_error(error: Exception) -> bool:
    """Check if an error is likely transient and worth retrying."""
    msg = str(error).lower()
    return any(s in msg for s in _TRANSIENT_ERROR_SUBSTRINGS)


# ===== Retry with backoff =====

async def retry_with_backoff(
    fn,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retry_on: Optional[Callable] = None,
):
    """
    Retry an async function with exponential backoff.

    Args:
        fn: Async callable to retry
        max_retries: Maximum retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap
        backoff_factor: Multiplier for each retry
        retry_on: Predicate to decide if error is retryable (default: is_transient_error)

    Returns:
        Result of fn()

    Raises:
        Last exception if all retries exhausted
    """
    should_retry = retry_on or is_transient_error
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_error = e
            if attempt >= max_retries or not should_retry(e):
                raise
            delay = min(base_delay * (backoff_factor ** attempt), max_delay)
            logger.debug(
                "Retrying after transient error",
                attempt=attempt + 1,
                max_retries=max_retries,
                delay=round(delay, 1),
                error=str(e)[:100],
            )
            await asyncio.sleep(delay)

    raise last_error


# ===== StepGuard: wraps PipelineStep with circuit breaker + retry =====

class StepGuard:
    """
    Wraps a PipelineStep with circuit breaker and retry logic.

    Usage in pipeline:
        step = StepGuard(AnalyzeStep(analyzer), failure_threshold=5, max_retries=2)
        pipeline.add_step(step)
    """

    def __init__(
        self,
        step,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        max_retries: int = 2,
        base_delay: float = 1.0,
    ):
        self._step = step
        self._breaker = CircuitBreaker(
            name=step.name,
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
        )
        self.max_retries = max_retries
        self.base_delay = base_delay

    @property
    def name(self) -> str:
        return self._step.name

    def can_run(self, ctx) -> bool:
        """Gate: original can_run AND circuit breaker allows."""
        if not self._breaker.can_execute():
            return False
        return self._step.can_run(ctx)

    async def execute(self, ctx, bus):
        """Execute with retry + circuit breaker."""
        async def _attempt():
            return await self._step.execute(ctx, bus)

        try:
            result = await retry_with_backoff(
                _attempt,
                max_retries=self.max_retries,
                base_delay=self.base_delay,
            )
            self._breaker.record_success()
            return result
        except Exception as e:
            self._breaker.record_failure()
            raise

    def get_breaker_stats(self) -> Dict:
        """Get circuit breaker statistics."""
        return self._breaker.get_stats()


def wrap_step_with_guard(
    step,
    failure_threshold: int = 5,
    reset_timeout: float = 60.0,
    max_retries: int = 2,
) -> StepGuard:
    """Convenience factory to wrap a pipeline step with circuit breaker + retry."""
    return StepGuard(
        step,
        failure_threshold=failure_threshold,
        reset_timeout=reset_timeout,
        max_retries=max_retries,
    )
