"""
AnalysisLoop — testable screen analysis tick extracted from server.py.

Reduces screen_analysis_loop CC=19 → AnalysisLoop._tick CC≈5
by splitting the monolithic while-loop into discrete methods:

- _tick(): single pipeline execution cycle
- _update_window_tracking(): detect active window changes
- _broadcast_state(): push SSE updates for window/screen/analysis
- _store_analysis(): persist analysis result to context history

Usage in server.py:
    loop = AnalysisLoop(app_state, broadcast)
    await loop.run_forever()

Each method is independently testable without asyncio.sleep.
"""
import asyncio
from typing import Any, Callable, Coroutine, Dict, Optional

import nfo
import structlog

from event_bus import EventBus
from pipeline import PipelineContext, PipelineOrchestrator, ProfileSelector

logger = structlog.get_logger()


class AnalysisLoop:
    """
    Encapsulates the main screen analysis loop.

    Single Responsibility: orchestrate pipeline ticks and broadcast results.
    Testable: call tick() directly without run_forever().
    """

    def __init__(self, state: dict, broadcast_fn: Callable):
        self.state = state
        self.broadcast = broadcast_fn
        self._prev_active_wid: int = 0

    @property
    def pipeline(self) -> PipelineOrchestrator:
        return self.state["pipeline"]

    @property
    def profile_selector(self) -> ProfileSelector:
        return self.state["profile_selector"]

    @property
    def capture(self):
        return self.state["capture"]

    @property
    def context_mgr(self):
        return self.state["context"]

    @nfo.log_call(level="INFO")
    async def run_forever(self):
        """Main loop — runs ticks until cancelled."""
        logger.info(
            "Screen analysis loop started (pipeline-based, profile-aware)",
            steps=self.pipeline.get_step_names(),
            total_steps=len(self.pipeline.steps),
        )

        while True:
            try:
                ctx = await self.tick()
                interval = self.capture.adaptive_interval
                # Enforce minimum interval when VLM OCR is active (high latency)
                min_interval = self.profile_selector.get_min_interval()
                if min_interval > interval:
                    interval = min_interval
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Screen analysis loop error", error=str(e))
                self.state["stats"]["total_errors"] += 1
                await self.broadcast("error", {"message": f"Screen analysis error: {str(e)}"})
                await asyncio.sleep(5)

    async def tick(self) -> PipelineContext:
        """
        Execute a single pipeline cycle.

        Returns the PipelineContext for inspection/testing.
        """
        # Select profile and run pipeline
        ctx = PipelineContext()
        profile = self.profile_selector.select(ctx, capture=self.capture)
        ctx.profile = profile.value
        ctx = await self.pipeline.run(ctx)

        # Post-pipeline processing
        self._update_window_tracking(ctx)
        await self._broadcast_state(ctx)
        self._store_analysis(ctx)
        self._log_errors(ctx)

        return ctx

    def _update_window_tracking(self, ctx: PipelineContext):
        """Notify profile selector on active window change (triggers FULL next tick)."""
        if ctx.active_window and hasattr(ctx.active_window, 'window_id'):
            new_wid = ctx.active_window.window_id
            if new_wid != self._prev_active_wid:
                self.profile_selector.notify_active_window_changed(new_wid)
                self._prev_active_wid = new_wid

    async def _broadcast_state(self, ctx: PipelineContext):
        """Push SSE updates for window, screen layout, and analysis."""
        # Active window
        if ctx.active_window:
            self.state["latest_window"] = ctx.active_window.to_dict()
            await self.broadcast("window", ctx.active_window.to_dict())

        # All-windows layout
        if ctx.all_windows:
            await self.broadcast("windows_layout", {
                "total": len(ctx.all_windows),
                "windows": [w.to_dict() for w in ctx.all_windows],
            })

        # Organized screen
        if ctx.organized_screen:
            self.state["latest_organized_screen"] = ctx.organized_screen.to_dict()
            await self.broadcast("organized_screen", {
                "total_windows": ctx.organized_screen.total_windows,
                "summary": ctx.organized_screen.screen_summary,
                "active_app": (
                    ctx.organized_screen.active_app.window.to_dict()
                    if ctx.organized_screen.active_app else None
                ),
                "categories": list(ctx.organized_screen.by_category.keys()),
            })

        # Analysis + agent actions
        if ctx.analysis_result:
            if ctx.agent_actions:
                await self.broadcast("agent_actions", {"actions": ctx.agent_actions})
            if ctx.broadcast_data:
                await self.broadcast("analysis", ctx.broadcast_data)

        # Clipboard intelligence
        if ctx.clipboard_suggestions:
            await self.broadcast("clipboard_suggestions", {
                "suggestions": ctx.clipboard_suggestions,
                "auto_copies": ctx.clipboard_auto_copies,
            })

    def _store_analysis(self, ctx: PipelineContext):
        """Persist analysis result to shared state and context history."""
        if not ctx.analysis_result:
            return

        analysis = ctx.analysis_result
        self.state["latest_analysis"] = analysis["text"]
        self.state["stats"]["total_screen_analyses"] += 1

        self.context_mgr.add(
            content=analysis["text"][:200],
            context_type="screen",
            metadata={
                "tokens": analysis.get("tokens", 0),
                "cost": analysis.get("cost", 0.0),
                "provider": analysis.get("provider", "unknown"),
                "window": ctx.active_window.title if ctx.active_window else None,
                "category": ctx.active_window.category.value if ctx.active_window else None,
                "organized_windows": ctx.organized_screen.total_windows if ctx.organized_screen else 0,
                "pipeline_run_id": ctx.run_id,
                "steps_executed": ctx.steps_executed,
                "step_timings": ctx.step_timings,
            },
        )

    def _log_errors(self, ctx: PipelineContext):
        """Log any pipeline step errors."""
        for err in ctx.errors:
            logger.warning("Pipeline step error", **err)
