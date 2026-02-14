"""Pipeline orchestrator, parallel group, and factory functions."""
import asyncio
import os
import time
from typing import Dict, List, Optional

import nfo
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

    def __init__(self, bus: EventBus, steps: Optional[List] = None):
        self.bus = bus
        self.steps: List = steps or []
        self.total_runs = 0
        self.total_errors = 0

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
        """
        if ctx is None:
            ctx = PipelineContext()

        self.total_runs += 1

        for step in self.steps:
            step_name = step.name

            # Gate check
            if not step.can_run(ctx):
                ctx.skipped.append(step_name)
                continue

            # Execute with timing
            t0 = time.time()
            try:
                ctx = await step.execute(ctx, self.bus)
                elapsed = time.time() - t0
                ctx.steps_executed.append(step_name)
                ctx.step_timings[step_name] = round(elapsed * 1000, 1)
            except Exception as e:
                elapsed = time.time() - t0
                self.total_errors += 1
                ctx.errors.append({
                    "step": step_name,
                    "error": str(e),
                    "elapsed_ms": round(elapsed * 1000, 1),
                })
                logger.error(
                    "Pipeline step failed",
                    step=step_name,
                    error=str(e),
                    elapsed_ms=round(elapsed * 1000, 1),
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

    pipeline = PipelineOrchestrator(bus)
    for guard, factory in step_defs:
        if guard:
            pipeline.add_step(factory())

    logger.info(
        "Pipeline created",
        steps=pipeline.get_step_names(),
        total_steps=len(pipeline.steps),
    )

    return pipeline


def create_profile_selector() -> ProfileSelector:
    """Create ProfileSelector from environment variables."""
    return ProfileSelector(
        full_interval=float(os.getenv("PIPELINE_FULL_INTERVAL", "60.0")),
        force_profile=os.getenv("PIPELINE_PROFILE") or None,
    )
