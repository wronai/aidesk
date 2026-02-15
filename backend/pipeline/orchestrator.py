"""Pipeline orchestrator, parallel group, and factory functions."""
import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import nfo
from nfo.models import LogEntry
import structlog

from event_bus import Event, EventBus, EventType
from .context import PipelineContext, PipelineProfile, ProfileSelector
from .steps_core import (
    ScanWindowsStep, DetectActiveWindowStep, CaptureScreenStep,
    CropWindowsStep, BuildContextStep, AnalyzeStep,
    SuggestActionsStep, BuildBroadcastStep,
)
from .steps_tier1 import (
    MultiMonitorStep, SemanticMemoryStep, ActionTemplateStep,
    OCRPostProcessStep, PredictiveStep, ClipboardStep, ClipboardRelationStep,
)

logger = structlog.get_logger()


# ===== Parallel Group (concurrent step execution) =====

class ParallelGroup:
    """
    Runs multiple pipeline steps concurrently via asyncio.gather.

    Acts as a single PipelineStep from the orchestrator's perspective,
    but internally fans out to N sub-steps in parallel.
    """

    def __init__(self, steps: List, name: Optional[str] = None):
        self._steps = steps
        self.name = name or f"parallel({','.join(s.name for s in steps)})"

    def can_run(self, ctx: PipelineContext) -> bool:
        return any(s.can_run(ctx) for s in self._steps)

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        runnable = [(s, s.can_run(ctx)) for s in self._steps]

        async def _run_one(step):
            t0 = time.time()
            try:
                await step.execute(ctx, bus)
                elapsed = time.time() - t0
                ctx.steps_executed.append(step.name)
                ctx.step_timings[step.name] = round(elapsed * 1000, 1)
            except Exception as e:
                elapsed = time.time() - t0
                ctx.errors.append({
                    "step": step.name,
                    "error": str(e),
                    "elapsed_ms": round(elapsed * 1000, 1),
                })
                logger.error("Parallel step failed", step=step.name, error=str(e))

        tasks = [_run_one(s) for s, can in runnable if can]
        if tasks:
            await asyncio.gather(*tasks)
        return ctx


# ===== Pipeline Orchestrator =====

class PipelineOrchestrator:
    """
    Executes a sequence of PipelineSteps, respecting can_run() gates.

    Open/Closed: add steps via add_step() without modifying orchestrator.
    Single Responsibility: orchestrator only manages execution order and timing.
    """

    def __init__(self, bus: EventBus, steps: Optional[List] = None,
                 optimization_strategy=None):
        self.bus = bus
        self.steps: List = steps or []
        self.total_runs = 0
        self.total_errors = 0
        self._strategy = optimization_strategy

    def add_step(self, step) -> "PipelineOrchestrator":
        """Add a step to the pipeline (builder pattern)."""
        self.steps.append(step)
        return self

    def remove_step(self, name: str) -> "PipelineOrchestrator":
        """Remove a step by name."""
        self.steps = [s for s in self.steps if s.name != name]
        return self

    def insert_before(self, before_name: str, step) -> "PipelineOrchestrator":
        """Insert a step before another step by name."""
        for i, s in enumerate(self.steps):
            if s.name == before_name:
                self.steps.insert(i, step)
                return self
        self.steps.append(step)
        return self

    def insert_after(self, after_name: str, step) -> "PipelineOrchestrator":
        """Insert a step after another step by name."""
        for i, s in enumerate(self.steps):
            if s.name == after_name:
                self.steps.insert(i + 1, step)
                return self
        self.steps.append(step)
        return self

    async def run(self, ctx: Optional[PipelineContext] = None) -> PipelineContext:
        """
        Execute all pipeline steps in order.

        Steps that fail can_run() are skipped.
        Steps that throw are logged and skipped (pipeline continues).
        Each step emits an nfo LogEntry with pipeline_run_id for PipelineSink grouping.
        """
        if ctx is None:
            ctx = PipelineContext()

        self.total_runs += 1
        run_id = ctx.run_id
        pipeline_t0 = time.monotonic()
        total_cost = 0.0

        for step in self.steps:
            step_name = step.name

            # Gate check
            if not step.can_run(ctx):
                ctx.skipped.append(step_name)
                self._emit_step(
                    run_id, step_name, decision="skipped",
                    decision_reason="can_run=False",
                )
                continue

            # Execute with timing
            t0 = time.monotonic()
            try:
                ctx = await step.execute(ctx, self.bus)
                elapsed_ms = (time.monotonic() - t0) * 1000
                ctx.steps_executed.append(step_name)
                ctx.step_timings[step_name] = round(elapsed_ms, 1)
                metrics = _extract_step_metrics(step_name, ctx)
                step_cost = metrics.get("cost_usd", 0)
                total_cost += step_cost
                self._emit_step(
                    run_id, step_name, duration_ms=elapsed_ms,
                    decision="executed", **metrics,
                )
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                self.total_errors += 1
                ctx.errors.append({
                    "step": step_name,
                    "error": str(e),
                    "elapsed_ms": round(elapsed_ms, 1),
                })
                logger.error(
                    "Pipeline step failed",
                    step=step_name,
                    error=str(e),
                    elapsed_ms=round(elapsed_ms, 1),
                )
                self._emit_step(
                    run_id, step_name, duration_ms=elapsed_ms,
                    exception=str(e), exception_type=type(e).__name__,
                )

        # Pipeline completion marker
        total_ms = (time.monotonic() - pipeline_t0) * 1000

        # Strategy feedback loop — record actual cost and latency
        if self._strategy and ctx.optimization_decision:
            try:
                mode = getattr(ctx.optimization_decision, 'analysis_mode', '')
                self._strategy.record_tick(
                    actual_cost=ctx.actual_cost,
                    actual_latency_ms=total_ms,
                    mode=mode,
                )
            except Exception as e:
                logger.debug("Strategy record_tick failed", error=str(e))

        self._emit_completion(
            run_id,
            total_ms=total_ms,
            total_cost=total_cost,
            total_steps=len(ctx.steps_executed),
            skipped=len(ctx.skipped),
            errors=len(ctx.errors),
        )

        # Emit pipeline completion event for ReadModel projection
        await self.bus.publish(Event(
            type="pipeline.completed",
            data={
                "run_id": ctx.run_id,
                "steps_executed": ctx.steps_executed,
                "step_timings": ctx.step_timings,
                "errors": ctx.errors,
                "skipped": ctx.skipped,
            },
            source="orchestrator",
            correlation_id=ctx.correlation_id,
        ))

        return ctx

    # -- nfo emission helpers ------------------------------------------------

    def _emit_step(
        self,
        run_id: str,
        step_name: str,
        duration_ms: Optional[float] = None,
        exception: Optional[str] = None,
        exception_type: Optional[str] = None,
        **extra_kwargs: Any,
    ) -> None:
        """Emit a single pipeline step entry to nfo."""
        level = "ERROR" if exception else "INFO"
        extra = {
            "pipeline_run_id": run_id,
            "step_name": step_name,
            **extra_kwargs,
        }
        entry = LogEntry(
            timestamp=LogEntry.now(),
            level=level,
            function_name=f"pipeline.{step_name}",
            module="pipeline.orchestrator",
            args=(),
            kwargs={},
            arg_types=[],
            kwarg_types={},
            duration_ms=round(duration_ms, 1) if duration_ms is not None else None,
            exception=exception,
            exception_type=exception_type,
            extra=extra,
        )
        _get_nfo_logger().emit(entry)

    def _emit_completion(
        self,
        run_id: str,
        total_ms: float,
        total_cost: float,
        total_steps: int,
        skipped: int,
        errors: int,
    ) -> None:
        """Emit the pipeline completion marker for PipelineSink flush."""
        entry = LogEntry(
            timestamp=LogEntry.now(),
            level="INFO",
            function_name="pipeline.complete",
            module="pipeline.orchestrator",
            args=(),
            kwargs={},
            arg_types=[],
            kwarg_types={},
            duration_ms=round(total_ms, 1),
            extra={
                "pipeline_run_id": run_id,
                "pipeline_complete": True,
                "total_ms": round(total_ms, 1),
                "total_cost": total_cost,
                "total_steps": total_steps,
                "skipped": skipped,
                "errors": errors,
            },
        )
        _get_nfo_logger().emit(entry)

    def get_step_names(self) -> List[str]:
        """Get ordered list of step names."""
        return [s.name for s in self.steps]

    def get_stats(self) -> Dict:
        return {
            "total_runs": self.total_runs,
            "total_errors": self.total_errors,
            "steps": self.get_step_names(),
            "step_count": len(self.steps),
        }


# -- module-level helpers for nfo emission --------------------------------

def _get_nfo_logger():
    """Return the nfo default logger (set by nfo.configure)."""
    from nfo.decorators import _get_default_logger
    return _get_default_logger()


def _extract_step_metrics(step_name: str, ctx: PipelineContext) -> Dict[str, Any]:
    """Extract lightweight metrics from PipelineContext after a step executes.

    Returns a dict of extra fields suitable for nfo LogEntry.extra.
    """
    dispatch: Dict[str, Any] = {
        "scan_windows": lambda: {
            "windows_total": len(ctx.all_windows or []),
            "active_window": (
                getattr(ctx.active_window, "title", "")[:50]
                if ctx.active_window else ""
            ),
        },
        "detect_active_window": lambda: {
            "active_window": (
                getattr(ctx.active_window, "title", "")[:50]
                if ctx.active_window else ""
            ),
        },
        "capture_screen": lambda: {
            "data_size_kb": round(len(ctx.image_b64 or "") * 3 / 4 / 1024, 1),
            "has_change": ctx.image_b64 is not None,
        },
        "crop_windows": lambda: {
            "crops_total": (
                getattr(ctx.organized_screen, "total_windows", 0)
                if ctx.organized_screen else 0
            ),
        },
        "build_context": lambda: {
            "context_length": len(ctx.full_context or ""),
            "memories_recalled": len(ctx.recalled_memories or []),
        },
        "analyze": lambda: _analyze_metrics(ctx),
        "suggest_actions": lambda: {
            "actions_count": len(ctx.agent_actions or []),
        },
        "build_broadcast": lambda: {
            "events_count": len(ctx.broadcast_data.keys()) if ctx.broadcast_data else 0,
        },
        "semantic_memory": lambda: {
            "memories_recalled": len(ctx.recalled_memories or []),
        },
        "ocr_post_process": lambda: {
            "ocr_enhanced": ctx.ocr_enhanced,
            "ocr_corrections": ctx.ocr_corrections,
        },
        "predictive": lambda: {
            "has_prediction": ctx.prediction is not None,
            "used_prefetch": ctx.used_prefetch,
        },
        "clipboard": lambda: {
            "clipboard_suggestions": len(ctx.clipboard_suggestions or []),
        },
    }

    factory = dispatch.get(step_name)
    if factory:
        try:
            return factory()
        except Exception:
            return {}
    return {}


def _analyze_metrics(ctx: PipelineContext) -> Dict[str, Any]:
    """Extract analysis-specific metrics (cost, tokens, provider, OCR)."""
    ar = ctx.analysis_result
    if not ar:
        return {}
    metrics: Dict[str, Any] = {
        "cost_usd": float(ar.get("cost", 0) or 0),
        "tokens_in": ar.get("input_tokens", ar.get("tokens", 0)),
        "tokens_out": ar.get("output_tokens", 0),
        "provider": ar.get("provider", ""),
        "model": ar.get("model", ""),
        "mode": ar.get("mode", ""),
    }
    ocr = ar.get("ocr")
    if ocr:
        metrics["ocr_engine"] = ocr.get("engine", "")
        metrics["ocr_ms"] = ocr.get("latency_ms", ocr.get("processing_time_ms", 0))
        metrics["ocr_chars"] = len(ocr.get("text", ""))
    return metrics


def create_pipeline(
    bus: EventBus,
    capture=None,
    analyzer=None,
    context_mgr=None,
    window_mgr=None,
    profile_mgr=None,
    shell_agent=None,
    process_scanner=None,
    window_cropper=None,
    app_state_ref=None,
    multi_monitor=None,
    semantic_memory=None,
    action_library=None,
    ocr_enhancer=None,
    predictive_engine=None,
    clipboard_manager=None,
    cost_budget=None,
    optimization_strategy=None,
) -> PipelineOrchestrator:
    """
    Factory: create the standard analysis pipeline from components.

    Dependency Inversion: components are injected, not imported.
    Open/Closed: caller can add_step() / remove_step() after creation.
    """
    use_roi = os.getenv("CAPTURE_MODE", "fullscreen") == "window"
    scan_cache_ttl = float(os.getenv("SCAN_CACHE_TTL", "3.0"))

    # (guard, step_factory) — steps are added in order when guard is truthy
    step_defs = [
        (process_scanner,   lambda: ScanWindowsStep(process_scanner, cache_ttl=scan_cache_ttl)),
        (window_mgr,        lambda: DetectActiveWindowStep(window_mgr, use_window_roi=use_roi)),
        (capture,           lambda: CaptureScreenStep(capture)),
        (window_cropper,    lambda: CropWindowsStep(window_cropper)),
        (multi_monitor,     lambda: MultiMonitorStep(multi_monitor, window_mgr)),
        (context_mgr,       lambda: BuildContextStep(context_mgr, profile_mgr, app_state_ref, semantic_memory=semantic_memory)),
        (analyzer,          lambda: AnalyzeStep(analyzer, cost_budget=cost_budget)),
        (ocr_enhancer,      lambda: OCRPostProcessStep(ocr_enhancer)),
        (shell_agent,       lambda: SuggestActionsStep(shell_agent)),
        (action_library,    lambda: ActionTemplateStep(action_library)),
        (semantic_memory,   lambda: SemanticMemoryStep(semantic_memory)),
        (predictive_engine, lambda: PredictiveStep(predictive_engine)),
        (clipboard_manager, lambda: ClipboardStep(clipboard_manager)),
        (clipboard_manager, lambda: ClipboardRelationStep(clipboard_manager, app_state_ref)),
        (True,              lambda: BuildBroadcastStep()),
    ]

    pipeline = PipelineOrchestrator(bus, optimization_strategy=optimization_strategy)
    for guard, factory in step_defs:
        if guard:
            pipeline.add_step(factory())

    logger.info(
        "Pipeline created",
        steps=pipeline.get_step_names(),
        total_steps=len(pipeline.steps),
    )

    return pipeline


def create_profile_selector(ocr_manager=None, optimization_strategy=None) -> ProfileSelector:
    """Create ProfileSelector from environment variables."""
    return ProfileSelector(
        full_interval=float(os.getenv("PIPELINE_FULL_INTERVAL", "60.0")),
        force_profile=os.getenv("PIPELINE_PROFILE") or None,
        ocr_manager=ocr_manager,
        optimization_strategy=optimization_strategy,
    )
