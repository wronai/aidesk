"""
Pipeline - Composable analysis pipeline with SOLID step abstraction.

Implements:
- Strategy pattern: each PipelineStep is independently testable and swappable
- Open/Closed: add new steps without modifying existing ones
- Event Sourcing: each step emits events to the EventBus
- CQRS: pipeline context separates read-state from write-commands

Pipeline flow:
    ScanWindows → CaptureScreen → CropWindows → OrganizeScreen
    → BuildContext → Analyze → SuggestActions → Broadcast

Each step receives a shared PipelineContext (accumulator) and the EventBus.
Steps can be skipped, reordered, or replaced at runtime.
"""
import asyncio
import base64
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import structlog
from PIL import Image

from event_bus import Event, EventBus, EventType

logger = structlog.get_logger()


# ===== Pipeline Context (shared state accumulator) =====

@dataclass
class PipelineContext:
    """
    Immutable-ish accumulator passed through pipeline steps.

    Each step reads what it needs and writes its output.
    The context is the single source of truth for one pipeline run.
    """
    # Identity
    run_id: str = ""
    correlation_id: str = ""
    timestamp: float = 0.0

    # Phase 1: Window scanning
    all_windows: List[Any] = field(default_factory=list)
    active_window: Optional[Any] = None  # WindowInfo
    window_context_str: str = ""
    roi: Optional[Dict] = None

    # Phase 2: Screen capture
    image_b64: Optional[str] = None
    capture_result: Optional[Dict] = None
    fullscreen_image: Optional[Any] = None  # PIL.Image

    # Phase 3+4: Cropping & organizing
    organized_screen: Optional[Any] = None  # OrganizedScreenData
    screen_summary: str = ""

    # Phase 5: Context building
    context_str: str = ""
    prompt_addon: str = ""
    full_context: str = ""

    # Phase 6: Analysis
    analysis_result: Optional[Dict] = None

    # Phase 7: Agent actions
    agent_actions: List[Dict] = field(default_factory=list)

    # Broadcast payload
    broadcast_data: Optional[Dict] = None

    # Metadata
    steps_executed: List[str] = field(default_factory=list)
    step_timings: Dict[str, float] = field(default_factory=dict)
    errors: List[Dict] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = str(uuid.uuid4())[:8]
        if not self.correlation_id:
            self.correlation_id = self.run_id
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ===== Pipeline Step Protocol (Interface Segregation) =====

@runtime_checkable
class PipelineStep(Protocol):
    """
    Protocol for pipeline steps (Interface Segregation Principle).

    Every step must:
    - Have a unique name
    - Define can_run() to check preconditions
    - Implement execute() to do work and mutate context
    """
    name: str

    def can_run(self, ctx: PipelineContext) -> bool:
        """Check if this step should run given current context."""
        ...

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        """Execute step, update context, emit events. Return updated context."""
        ...


# ===== Concrete Pipeline Steps =====

class ScanWindowsStep:
    """Phase 1a: Scan all visible windows with process info."""
    name = "scan_windows"

    def __init__(self, process_scanner):
        self._scanner = process_scanner

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._scanner is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        ctx.all_windows = self._scanner.scan_all_windows()
        await bus.publish(Event(
            type=EventType.WINDOWS_SCANNED.value,
            data={"total": len(ctx.all_windows)},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class DetectActiveWindowStep:
    """Phase 1b: Detect active window and build window context."""
    name = "detect_active_window"

    def __init__(self, window_manager, use_window_roi: bool = False):
        self._wm = window_manager
        self._use_roi = use_window_roi

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._wm is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        info = self._wm.get_active_window()
        ctx.active_window = info
        ctx.window_context_str = info.to_context_string()

        if self._use_roi and info.width > 0:
            ctx.roi = self._wm.get_window_roi(info)

        await bus.publish(Event(
            type=EventType.WINDOWS_SCANNED.value,
            data={"active": info.to_dict()},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class CaptureScreenStep:
    """Phase 2: Capture fullscreen or ROI screenshot."""
    name = "capture_screen"

    def __init__(self, capture):
        self._capture = capture

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._capture is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        result = self._capture.capture(roi=ctx.roi)
        if not result:
            ctx.skipped.append("capture_no_change")
            return ctx

        ctx.capture_result = result
        ctx.image_b64 = result["image_b64"]

        await bus.publish(Event(
            type=EventType.SCREEN_CAPTURED.value,
            data={
                "size_kb": result.get("size_kb", 0),
                "timestamp": result.get("timestamp", 0),
                "has_change": True,
            },
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class CropWindowsStep:
    """Phase 3+4: Crop each visible application from fullscreen screenshot."""
    name = "crop_windows"

    def __init__(self, window_cropper):
        self._cropper = window_cropper

    def can_run(self, ctx: PipelineContext) -> bool:
        return (
            self._cropper is not None
            and ctx.image_b64 is not None
            and len(ctx.all_windows) > 0
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        img_bytes = base64.b64decode(ctx.image_b64)
        fullscreen_img = Image.open(BytesIO(img_bytes))

        organized = self._cropper.organize_screen(fullscreen_img, ctx.all_windows)
        ctx.organized_screen = organized
        ctx.screen_summary = organized.screen_summary

        await bus.publish(Event(
            type=EventType.SCREEN_ORGANIZED.value,
            data={
                "total_windows": organized.total_windows,
                "summary": organized.screen_summary,
                "categories": list(organized.by_category.keys()),
            },
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class BuildContextStep:
    """Phase 5: Build rich context from window info, screen summary, profiles, transcript."""
    name = "build_context"

    def __init__(self, context_mgr, profile_mgr=None, app_state_ref=None):
        self._context = context_mgr
        self._profiles = profile_mgr
        self._state = app_state_ref or {}

    def can_run(self, ctx: PipelineContext) -> bool:
        return ctx.image_b64 is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        # Base context from history
        ctx.context_str = self._context.get_context_string(n=5, max_length=500)

        # Prepend organized screen summary
        if ctx.screen_summary:
            ctx.context_str = f"📊 Ekran: {ctx.screen_summary}\n\n{ctx.context_str}"

        # Prepend window context
        if ctx.window_context_str:
            ctx.context_str = f"{ctx.window_context_str}\n\n{ctx.context_str}"

        # Per-app profile prompt
        if self._profiles and ctx.active_window:
            ctx.prompt_addon = self._profiles.get_prompt_addon(ctx.active_window.category)

        # Combine
        ctx.full_context = ctx.context_str
        if ctx.prompt_addon:
            ctx.full_context = f"{ctx.prompt_addon}\n\n{ctx.context_str}"

        # Include latest speech transcript
        latest_transcript = self._state.get("latest_transcript", "")
        if latest_transcript:
            ctx.full_context = f"🎤 Użytkownik powiedział: {latest_transcript}\n\n{ctx.full_context}"

        await bus.publish(Event(
            type=EventType.CONTEXT_BUILT.value,
            data={"context_length": len(ctx.full_context)},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class AnalyzeStep:
    """Phase 6: Run OCR + LLM analysis."""
    name = "analyze"

    def __init__(self, analyzer):
        self._analyzer = analyzer

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._analyzer is not None and ctx.image_b64 is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis = await self._analyzer.analyze(ctx.image_b64, ctx.full_context)
        ctx.analysis_result = analysis

        await bus.publish(Event(
            type=EventType.ANALYSIS_COMPLETED.value,
            data={
                "tokens": analysis.get("tokens", 0),
                "cost": analysis.get("cost", 0.0),
                "provider": analysis.get("provider", "unknown"),
                "mode": analysis.get("mode", "unknown"),
                "has_ocr": "ocr" in analysis,
            },
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class SuggestActionsStep:
    """Phase 7: Shell agent suggests actions based on analysis text."""
    name = "suggest_actions"

    def __init__(self, shell_agent):
        self._agent = shell_agent

    def can_run(self, ctx: PipelineContext) -> bool:
        return (
            self._agent is not None
            and ctx.analysis_result is not None
            and ctx.active_window is not None
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis_text = ctx.analysis_result.get("text", "")
        ocr_text = ""
        if ctx.analysis_result.get("ocr") and ctx.analysis_result["ocr"].get("text"):
            ocr_text = ctx.analysis_result["ocr"]["text"]
        combined = f"{analysis_text}\n{ocr_text}"

        actions = self._agent.suggest_actions(
            detected_text=combined,
            category=ctx.active_window.category,
            cwd=getattr(ctx.active_window, "cwd", None),
        )
        if actions:
            ctx.agent_actions = [a.to_dict() for a in actions]

            await bus.publish(Event(
                type=EventType.AGENT_SUGGESTED.value,
                data={"count": len(actions)},
                source=self.name,
                correlation_id=ctx.correlation_id,
            ))
        return ctx


class BuildBroadcastStep:
    """Final: assemble broadcast payload from pipeline context."""
    name = "build_broadcast"

    def can_run(self, ctx: PipelineContext) -> bool:
        return ctx.analysis_result is not None and ctx.capture_result is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis = ctx.analysis_result
        result = ctx.capture_result

        data = {
            "text": analysis["text"],
            "timestamp": result["timestamp"],
            "size_kb": result["size_kb"],
            "tokens": analysis.get("tokens", 0),
            "cost": round(analysis.get("cost", 0.0), 6),
            "provider": analysis.get("provider", "unknown"),
            "mode": analysis.get("mode", "vision_only"),
            "ocr": analysis.get("ocr"),
        }

        if ctx.active_window:
            data["window"] = {
                "title": ctx.active_window.title,
                "category": ctx.active_window.category.value,
                "app": ctx.active_window.wm_class_name,
                "git_branch": getattr(ctx.active_window, "git_branch", None),
            }

        if ctx.organized_screen:
            data["organized_screen"] = {
                "total_windows": ctx.organized_screen.total_windows,
                "summary": ctx.organized_screen.screen_summary,
                "categories": list(ctx.organized_screen.by_category.keys()),
            }

        if ctx.agent_actions:
            data["agent_actions"] = ctx.agent_actions

        ctx.broadcast_data = data

        await bus.publish(Event(
            type=EventType.BROADCAST_SENT.value,
            data={"keys": list(data.keys())},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
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
) -> PipelineOrchestrator:
    """
    Factory: create the standard analysis pipeline from components.

    Dependency Inversion: components are injected, not imported.
    Open/Closed: caller can add_step() / remove_step() after creation.
    """
    use_roi = os.getenv("CAPTURE_MODE", "fullscreen") == "window"

    pipeline = PipelineOrchestrator(bus)

    # Phase 1: Detect windows and processes
    if process_scanner:
        pipeline.add_step(ScanWindowsStep(process_scanner))
    if window_mgr:
        pipeline.add_step(DetectActiveWindowStep(window_mgr, use_window_roi=use_roi))

    # Phase 2: Capture
    if capture:
        pipeline.add_step(CaptureScreenStep(capture))

    # Phase 3+4: Crop and organize
    if window_cropper:
        pipeline.add_step(CropWindowsStep(window_cropper))

    # Phase 5: Build context
    if context_mgr:
        pipeline.add_step(BuildContextStep(context_mgr, profile_mgr, app_state_ref))

    # Phase 6: Analyze
    if analyzer:
        pipeline.add_step(AnalyzeStep(analyzer))

    # Phase 7: Suggest actions
    if shell_agent:
        pipeline.add_step(SuggestActionsStep(shell_agent))

    # Final: build broadcast payload
    pipeline.add_step(BuildBroadcastStep())

    logger.info(
        "Pipeline created",
        steps=pipeline.get_step_names(),
        total_steps=len(pipeline.steps),
    )

    return pipeline
