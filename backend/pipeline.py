"""
Pipeline - Composable analysis pipeline with SOLID step abstraction.

Implements:
- Strategy pattern: each PipelineStep is independently testable and swappable
- Open/Closed: add new steps without modifying existing ones
- Event Sourcing: each step emits events to the EventBus
- CQRS: pipeline context separates read-state from write-commands
- Pipeline Profiles: FAST / NORMAL / FULL for specialized task routing

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
from enum import Enum
from io import BytesIO
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import structlog
from PIL import Image

from event_bus import Event, EventBus, EventType

logger = structlog.get_logger()


# ===== Pipeline Profiles =====

class PipelineProfile(str, Enum):
    """
    Pipeline execution profiles for specialized task routing.

    FAST   — low-latency: skip cropping, use OCR-only/hybrid, cached window scan
    NORMAL — balanced: cached scan, top-K crops, hybrid analysis
    FULL   — quality-first: full scan, crop all, ocr+vision analysis
    """
    FAST = "fast"
    NORMAL = "normal"
    FULL = "full"


class ProfileSelector:
    """
    Adaptively selects pipeline profile per tick based on heuristics.

    Rules:
    - FULL every `full_interval` seconds (periodic deep scan)
    - FULL when active window changes (new app context)
    - NORMAL when screen change detected (default)
    - FAST when no significant change or in idle approach
    """

    def __init__(
        self,
        full_interval: float = 60.0,
        force_profile: Optional[str] = None,
    ):
        self.full_interval = full_interval
        self.force_profile = PipelineProfile(force_profile) if force_profile else None
        self._last_full_time = 0.0
        self._last_active_wid = 0
        self._consecutive_fast = 0
        # Stats
        self.profile_counts = {p.value: 0 for p in PipelineProfile}

    def select(self, ctx: 'PipelineContext', capture=None) -> PipelineProfile:
        """
        Choose optimal profile for this pipeline tick.

        Args:
            ctx: Current pipeline context (may have cached windows from prev tick)
            capture: SmartScreenCapture instance for idle detection

        Returns:
            Selected PipelineProfile
        """
        if self.force_profile:
            self.profile_counts[self.force_profile.value] += 1
            return self.force_profile

        now = time.time()

        # Periodic FULL scan
        if now - self._last_full_time >= self.full_interval:
            self._last_full_time = now
            self._consecutive_fast = 0
            self.profile_counts[PipelineProfile.FULL.value] += 1
            return PipelineProfile.FULL

        # Check if capture system is in idle mode
        is_idle = False
        if capture and hasattr(capture, 'consecutive_unchanged'):
            is_idle = capture.consecutive_unchanged > getattr(capture, 'idle_threshold', 30)

        if is_idle:
            self._consecutive_fast += 1
            self.profile_counts[PipelineProfile.FAST.value] += 1
            return PipelineProfile.FAST

        # Default: NORMAL
        self._consecutive_fast = 0
        self.profile_counts[PipelineProfile.NORMAL.value] += 1
        return PipelineProfile.NORMAL

    def notify_active_window_changed(self, new_wid: int):
        """Signal that active window changed — next tick should be FULL."""
        if new_wid != self._last_active_wid:
            self._last_active_wid = new_wid
            self._last_full_time = 0  # Force FULL on next tick

    def get_stats(self) -> Dict:
        return {
            "profile_counts": self.profile_counts,
            "full_interval": self.full_interval,
            "force_profile": self.force_profile.value if self.force_profile else None,
            "last_full_ago": round(time.time() - self._last_full_time, 1) if self._last_full_time else None,
        }


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
    capture_image: Optional[Any] = None     # PIL.Image (resized) — avoids base64 re-decode
    fullscreen_path: Optional[str] = None   # Path to full-res original frame (for high-quality cropping)

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

    # Tier 1: Multi-monitor
    multi_monitor_snapshot: Optional[Any] = None  # MultiMonitorSnapshot
    monitor_description: str = ""

    # Tier 1: Semantic memory
    recalled_memories: List[Any] = field(default_factory=list)

    # Tier 1: Action templates (learned)
    template_actions: List[Dict] = field(default_factory=list)

    # Tier 1: OCR post-processing
    ocr_enhanced: bool = False
    ocr_corrections: int = 0

    # Tier 1: Predictive pre-fetch
    prediction: Optional[Any] = None  # PredictionResult
    used_prefetch: bool = False

    # Pipeline profile for this run
    profile: str = "normal"  # PipelineProfile value

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
    """Phase 1a: Scan all visible windows with process info.

    Supports caching: on FAST/NORMAL profiles, reuses cached results
    if they are fresher than `cache_ttl`. On FULL profile, always re-scans.
    This decouples expensive subprocess calls from every pipeline tick.
    """
    name = "scan_windows"

    def __init__(self, process_scanner, cache_ttl: float = 3.0):
        self._scanner = process_scanner
        self._cache_ttl = cache_ttl
        self._cached_windows: List[Any] = []
        self._cache_time: float = 0.0

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._scanner is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        now = time.time()
        cache_age = now - self._cache_time

        # FAST/NORMAL: use cache if fresh enough
        if ctx.profile != PipelineProfile.FULL.value and cache_age < self._cache_ttl and self._cached_windows:
            ctx.all_windows = self._cached_windows
            logger.debug("scan_windows: using cache", age=round(cache_age, 1), total=len(self._cached_windows))
        else:
            ctx.all_windows = await asyncio.to_thread(self._scanner.scan_all_windows)
            self._cached_windows = ctx.all_windows
            self._cache_time = now

        await bus.publish(Event(
            type=EventType.WINDOWS_SCANNED.value,
            data={"total": len(ctx.all_windows), "cached": ctx.profile != PipelineProfile.FULL.value and cache_age < self._cache_ttl},
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
        info = await asyncio.to_thread(self._wm.get_active_window)
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
        ctx.fullscreen_path = result.get("fullscreen_path")

        # Store PIL image directly for downstream steps (avoids base64 re-decode)
        if hasattr(self._capture, '_last_resized_image') and self._capture._last_resized_image is not None:
            ctx.capture_image = self._capture._last_resized_image

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
    """Phase 3+4: Crop each visible application from fullscreen screenshot.

    Skipped on FAST profile (cropping is expensive and unnecessary for quick insights).
    """
    name = "crop_windows"

    def __init__(self, window_cropper):
        self._cropper = window_cropper

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False  # FAST: skip cropping entirely
        return (
            self._cropper is not None
            and ctx.image_b64 is not None
            and len(ctx.all_windows) > 0
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        # Prefer full-resolution original frame for cropping (better OCR quality)
        fullscreen_img = None
        if ctx.fullscreen_path and os.path.exists(ctx.fullscreen_path):
            try:
                fullscreen_img = Image.open(ctx.fullscreen_path).convert("RGB")
                logger.info(
                    "Using full-res frame for cropping",
                    path=ctx.fullscreen_path,
                    size=f"{fullscreen_img.size[0]}x{fullscreen_img.size[1]}",
                )
            except Exception as e:
                logger.warning("Failed to load full-res frame, using resized", error=str(e))

        # Fallback: resized PIL image from capture or base64
        if fullscreen_img is None:
            if ctx.capture_image is not None:
                fullscreen_img = ctx.capture_image
            else:
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

        # Prepend focus window info (where user is actively working, based on diffs)
        if ctx.organized_screen and ctx.organized_screen.focus_window:
            fw = ctx.organized_screen.focus_window
            ctx.context_str = (
                f"🎯 Fokus pracy (wykryto zmiany): {fw.window.wm_class_name or fw.window.title} "
                f"({fw.window.category.value}, zmiana: {fw.change_score:.0f})\n"
                f"Skup się na tym oknie — tu użytkownik aktualnie pracuje.\n\n"
                + ctx.context_str
            )

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
            org = ctx.organized_screen
            data["organized_screen"] = {
                "total_windows": org.total_windows,
                "summary": org.screen_summary,
                "categories": list(org.by_category.keys()),
                "focus_window": (
                    {
                        "app": org.focus_window.window.wm_class_name or org.focus_window.window.title,
                        "category": org.focus_window.window.category.value,
                        "change_score": round(org.focus_window.change_score, 1),
                    }
                    if org.focus_window else None
                ),
                "changed_count": len(org.changed_windows),
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


# ===== Tier 1 Pipeline Steps =====

class MultiMonitorStep:
    """Tier 1: Build multi-monitor snapshot and inject monitor description into context."""
    name = "multi_monitor"

    def __init__(self, multi_monitor, window_mgr=None):
        self._mm = multi_monitor
        self._wm = window_mgr

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._mm is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        monitors = self._wm.get_monitors() if self._wm else []
        if not monitors or len(monitors) <= 1:
            return ctx  # Single monitor — skip

        snapshot = self._mm.build_snapshot(
            monitors=monitors,
            all_windows=ctx.all_windows,
            active_window=ctx.active_window,
            organized_screen=ctx.organized_screen,
        )
        ctx.multi_monitor_snapshot = snapshot
        ctx.monitor_description = snapshot.description
        return ctx


class SemanticMemoryStep:
    """Tier 1: Store context to semantic memory and recall relevant past memories."""
    name = "semantic_memory"

    def __init__(self, semantic_memory):
        self._mem = semantic_memory

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._mem is not None and ctx.analysis_result is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis_text = ctx.analysis_result.get("text", "")
        if not analysis_text:
            return ctx

        # Store current analysis as memory
        metadata = {
            "window": ctx.active_window.title if ctx.active_window else None,
            "category": ctx.active_window.category.value if ctx.active_window else None,
            "run_id": ctx.run_id,
        }
        self._mem.add_memory(
            content=analysis_text[:500],
            context_type="screen",
            metadata=metadata,
        )

        # Recall relevant past memories for enriching next pipeline runs
        recalled = self._mem.recall_relevant(analysis_text[:200], k=3)
        ctx.recalled_memories = recalled
        return ctx


class ActionTemplateStep:
    """Tier 1: Suggest learned action templates with confidence scoring."""
    name = "action_templates"

    def __init__(self, action_library):
        self._lib = action_library

    def can_run(self, ctx: PipelineContext) -> bool:
        return (
            self._lib is not None
            and self._lib.enabled
            and ctx.analysis_result is not None
            and ctx.active_window is not None
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis_text = ctx.analysis_result.get("text", "")
        ocr_text = ""
        if ctx.analysis_result.get("ocr") and ctx.analysis_result["ocr"].get("text"):
            ocr_text = ctx.analysis_result["ocr"]["text"]
        combined = f"{analysis_text}\n{ocr_text}"

        scored = self._lib.suggest_with_confidence(
            text=combined,
            app_category=ctx.active_window.category.value,
            cwd=getattr(ctx.active_window, "cwd", None),
        )
        if scored:
            ctx.template_actions = [a.to_dict() for a in scored]
        return ctx


class OCRPostProcessStep:
    """Tier 1: Enhance OCR output with post-processing corrections."""
    name = "ocr_post_process"

    def __init__(self, ocr_enhancer):
        self._enhancer = ocr_enhancer

    def can_run(self, ctx: PipelineContext) -> bool:
        return (
            self._enhancer is not None
            and self._enhancer.enabled
            and ctx.analysis_result is not None
            and ctx.analysis_result.get("ocr")
            and ctx.analysis_result["ocr"].get("text")
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        ocr_data = ctx.analysis_result["ocr"]
        raw_text = ocr_data.get("text", "")
        if not raw_text:
            return ctx

        result = self._enhancer.enhance(raw_text)
        if result.corrections_count > 0:
            # Update OCR text in analysis result with enhanced version
            ctx.analysis_result["ocr"]["text"] = result.enhanced_text
            ctx.analysis_result["ocr"]["post_process"] = result.to_dict()
            ctx.ocr_enhanced = True
            ctx.ocr_corrections = result.corrections_count

            logger.debug(
                "OCR post-processed",
                corrections=result.corrections_count,
                text_type=result.text_type,
                time_ms=round(result.processing_time_ms, 1),
            )
        return ctx


class PredictiveStep:
    """Tier 1: Observe window transitions and trigger pre-fetch for predicted next window."""
    name = "predictive"

    def __init__(self, predictive_engine):
        self._engine = predictive_engine
        self._prev_category = ""
        self._prev_window_id = 0

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._engine is not None and self._engine.enabled and ctx.active_window is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        win = ctx.active_window
        category = win.category.value if hasattr(win.category, 'value') else str(win.category)
        wid = win.window_id

        # Record transition if window changed
        if category != self._prev_category or wid != self._prev_window_id:
            self._engine.observe_window_change(category, wid)
            self._prev_category = category
            self._prev_window_id = wid

        # Check if pre-fetched data is available for current window
        cached = self._engine.get_prefetched(wid)
        if not cached:
            cached = self._engine.get_prefetched_for_category(category)
        if cached:
            ctx.used_prefetch = True

        # Make prediction and store it
        prediction = self._engine.predict_next_action(category)
        ctx.prediction = prediction

        # Trigger background pre-fetch (non-blocking)
        if prediction:
            await self._engine.maybe_prefetch(prediction)

        # Cleanup expired cache entries
        self._engine.cleanup_cache()
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
) -> PipelineOrchestrator:
    """
    Factory: create the standard analysis pipeline from components.

    Dependency Inversion: components are injected, not imported.
    Open/Closed: caller can add_step() / remove_step() after creation.

    Pipeline order (with Tier 1 additions marked with *):
      1. ScanWindows
      2. DetectActiveWindow
      3. CaptureScreen
      4. CropWindows
      5* MultiMonitor          — multi-monitor snapshot + description
      6. BuildContext
      7. Analyze
      8* OCRPostProcess         — enhance OCR text after analysis
      9. SuggestActions
     10* ActionTemplates        — learned action templates with confidence
     11* SemanticMemory         — store + recall relevant past context
     12* Predictive             — learn transitions + trigger pre-fetch
     13. BuildBroadcast
    """
    use_roi = os.getenv("CAPTURE_MODE", "fullscreen") == "window"
    scan_cache_ttl = float(os.getenv("SCAN_CACHE_TTL", "3.0"))

    pipeline = PipelineOrchestrator(bus)

    # Phase 1: Detect windows and processes (with caching for FAST/NORMAL)
    if process_scanner:
        pipeline.add_step(ScanWindowsStep(process_scanner, cache_ttl=scan_cache_ttl))
    if window_mgr:
        pipeline.add_step(DetectActiveWindowStep(window_mgr, use_window_roi=use_roi))

    # Phase 2: Capture
    if capture:
        pipeline.add_step(CaptureScreenStep(capture))

    # Phase 3+4: Crop and organize (skipped on FAST profile)
    if window_cropper:
        pipeline.add_step(CropWindowsStep(window_cropper))

    # Phase 5*: Multi-monitor intelligence (after cropping, before context)
    if multi_monitor:
        pipeline.add_step(MultiMonitorStep(multi_monitor, window_mgr))

    # Phase 6: Build context
    if context_mgr:
        pipeline.add_step(BuildContextStep(context_mgr, profile_mgr, app_state_ref))

    # Phase 7: Analyze (wrapped with circuit breaker + retry for API resilience)
    if analyzer:
        from circuit_breaker import wrap_step_with_guard
        pipeline.add_step(wrap_step_with_guard(
            AnalyzeStep(analyzer),
            failure_threshold=int(os.getenv("ANALYZE_CIRCUIT_THRESHOLD", "5")),
            reset_timeout=float(os.getenv("ANALYZE_CIRCUIT_RESET", "60.0")),
            max_retries=int(os.getenv("ANALYZE_MAX_RETRIES", "2")),
        ))

    # Phase 8*: OCR post-processing (after analysis produces OCR text)
    if ocr_enhancer:
        pipeline.add_step(OCRPostProcessStep(ocr_enhancer))

    # Phase 9: Suggest actions (shell agent)
    if shell_agent:
        pipeline.add_step(SuggestActionsStep(shell_agent))

    # Phase 10*: Action templates (learned patterns with confidence)
    if action_library:
        pipeline.add_step(ActionTemplateStep(action_library))

    # Phase 11*: Semantic memory (store analysis + recall relevant past)
    if semantic_memory:
        pipeline.add_step(SemanticMemoryStep(semantic_memory))

    # Phase 12*: Predictive pre-fetch (learn transitions, trigger background pre-fetch)
    if predictive_engine:
        pipeline.add_step(PredictiveStep(predictive_engine))

    # Final: build broadcast payload
    pipeline.add_step(BuildBroadcastStep())

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
