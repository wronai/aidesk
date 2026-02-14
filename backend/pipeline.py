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

import nfo
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

    # Pipeline profile for this run
    profile: str = "normal"  # PipelineProfile value

    # Tier 1 module fields
    multi_monitor_snapshot: Optional[Any] = None
    monitor_description: str = ""
    recalled_memories: List[Any] = field(default_factory=list)
    template_actions: List[Dict] = field(default_factory=list)
    ocr_enhanced: bool = False
    ocr_corrections: int = 0
    prediction: Optional[Dict] = None
    used_prefetch: bool = False
    clipboard_suggestions: List[Dict] = field(default_factory=list)
    clipboard_auto_copies: List[Dict] = field(default_factory=list)

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
            ctx.all_windows = self._scanner.scan_all_windows()
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
        # Prefer PIL image from capture (avoids base64 decode + JPEG re-parse)
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
        self._cached_key: Optional[tuple] = None
        self._cached_context_str = ""
        self._cached_full_context = ""

    def can_run(self, ctx: PipelineContext) -> bool:
        return ctx.image_b64 is not None

    def _context_version(self) -> Optional[int]:
        """Best-effort version marker to detect context history changes."""
        total_items = getattr(self._context, "total_items", None)
        if isinstance(total_items, int):
            return total_items

        history = getattr(self._context, "history", None)
        if history is None:
            return None
        try:
            return len(history)
        except Exception:
            return None

    @staticmethod
    def _focus_window_prefix(ctx: PipelineContext) -> str:
        if not (ctx.organized_screen and ctx.organized_screen.focus_window):
            return ""

        fw = ctx.organized_screen.focus_window
        return (
            f"🎯 Fokus pracy (wykryto zmiany): {fw.window.wm_class_name or fw.window.title} "
            f"({fw.window.category.value}, zmiana: {fw.change_score:.0f})\n"
            "Skup się na tym oknie — tu użytkownik aktualnie pracuje."
        )

    @nfo.log_call(level="INFO")
    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        prompt_addon = ""
        if self._profiles and ctx.active_window:
            prompt_addon = self._profiles.get_prompt_addon(ctx.active_window.category)
        ctx.prompt_addon = prompt_addon

        latest_transcript = self._state.get("latest_transcript", "") or ""
        focus_prefix = self._focus_window_prefix(ctx)
        cache_key = (
            self._context_version(),
            ctx.window_context_str or "",
            ctx.screen_summary or "",
            focus_prefix,
            prompt_addon,
            latest_transcript,
        )

        cached = False
        if self._cached_key == cache_key:
            ctx.context_str = self._cached_context_str
            ctx.full_context = self._cached_full_context
            cached = True
        else:
            base_context = self._context.get_context_string(n=5, max_length=500)

            context_parts = []
            if ctx.window_context_str:
                context_parts.append(ctx.window_context_str)
            if ctx.screen_summary:
                context_parts.append(f"📊 Ekran: {ctx.screen_summary}")
            if focus_prefix:
                context_parts.append(focus_prefix)
            if base_context:
                context_parts.append(base_context)

            ctx.context_str = "\n\n".join(context_parts)

            full_context = ctx.context_str
            if prompt_addon:
                full_context = f"{prompt_addon}\n\n{full_context}" if full_context else prompt_addon
            if latest_transcript:
                transcript_prefix = f"🎤 Użytkownik powiedział: {latest_transcript}"
                full_context = f"{transcript_prefix}\n\n{full_context}" if full_context else transcript_prefix

            ctx.full_context = full_context
            self._cached_key = cache_key
            self._cached_context_str = ctx.context_str
            self._cached_full_context = ctx.full_context

        await bus.publish(Event(
            type=EventType.CONTEXT_BUILT.value,
            data={"context_length": len(ctx.full_context), "cached": cached},
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


# ===== Tier 1 Pipeline Steps =====

class MultiMonitorStep:
    """Detect active monitor and build multi-monitor snapshot."""
    name = "multi_monitor"

    def __init__(self, monitor_capture, window_manager=None):
        self._monitor = monitor_capture
        self._wm = window_manager

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._monitor is not None and ctx.active_window is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        monitors = []
        if self._wm and hasattr(self._wm, 'get_monitors'):
            monitors = self._wm.get_monitors()

        if len(monitors) <= 1:
            ctx.multi_monitor_snapshot = None
            return ctx

        snapshot = self._monitor.build_snapshot(
            monitors=monitors,
            active_window=ctx.active_window,
            all_windows=ctx.all_windows,
        )
        ctx.multi_monitor_snapshot = snapshot
        if snapshot:
            ctx.monitor_description = snapshot.get_description() if hasattr(snapshot, 'get_description') else ""

        await bus.publish(Event(
            type="pipeline.multi_monitor",
            data={"monitors": len(monitors), "active_monitor": snapshot.active_index if snapshot else None},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class SemanticMemoryStep:
    """Store analysis results and recall relevant memories."""
    name = "semantic_memory"

    def __init__(self, semantic_memory):
        self._memory = semantic_memory

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False
        return self._memory is not None and ctx.analysis_result is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis_text = ctx.analysis_result.get("text", "")
        app_name = ""
        if ctx.active_window:
            app_name = getattr(ctx.active_window, 'wm_class_name', '') or getattr(ctx.active_window, 'title', '')

        self._memory.add_memory(
            content=analysis_text,
            context_type="screen",
            metadata={"run_id": ctx.run_id, "app": app_name},
        )

        recalled = self._memory.recall_relevant(analysis_text, k=3)
        ctx.recalled_memories = recalled

        await bus.publish(Event(
            type="pipeline.semantic_memory",
            data={"stored": True, "recalled": len(recalled)},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class ActionTemplateStep:
    """Match analysis against action templates and suggest actions."""
    name = "action_templates"

    def __init__(self, action_library):
        self._library = action_library

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False
        return self._library is not None and ctx.analysis_result is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis_text = ctx.analysis_result.get("text", "")
        ocr_text = ""
        if ctx.analysis_result.get("ocr") and ctx.analysis_result["ocr"].get("text"):
            ocr_text = ctx.analysis_result["ocr"]["text"]
        combined = f"{analysis_text}\n{ocr_text}"

        app_cat = ""
        if ctx.active_window:
            cat = ctx.active_window.category
            app_cat = cat.value if hasattr(cat, 'value') else str(cat or '')

        matches = self._library.suggest_with_confidence(combined, app_category=app_cat)
        ctx.template_actions = [m.to_dict() if hasattr(m, 'to_dict') else m for m in matches]

        await bus.publish(Event(
            type="pipeline.action_templates",
            data={"matched": len(matches)},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class OCRPostProcessStep:
    """Post-process OCR text to fix common errors."""
    name = "ocr_post_process"

    def __init__(self, enhancer):
        self._enhancer = enhancer

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False
        return (
            self._enhancer is not None
            and ctx.analysis_result is not None
            and ctx.analysis_result.get("ocr") is not None
            and ctx.analysis_result["ocr"].get("text")
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        ocr_text = ctx.analysis_result["ocr"]["text"]
        result = self._enhancer.enhance(ocr_text)

        ctx.analysis_result["ocr"]["text"] = result.enhanced_text
        ctx.analysis_result["ocr"]["post_process"] = result.to_dict()
        ctx.ocr_enhanced = True
        ctx.ocr_corrections = result.corrections_count

        await bus.publish(Event(
            type="pipeline.ocr_post_process",
            data={"corrections": result.corrections_count, "text_type": result.text_type},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class PredictiveStep:
    """Record window transitions and predict next app switch."""
    name = "predictive"

    def __init__(self, predictive_analyzer):
        self._predictor = predictive_analyzer

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False
        return self._predictor is not None and getattr(self._predictor, 'enabled', True) and ctx.active_window is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        cat = getattr(ctx.active_window, 'category', None)
        cat_str = cat.value if hasattr(cat, 'value') else str(cat or 'unknown')
        wid = getattr(ctx.active_window, 'window_id', 0)
        self._predictor.observe_window_change(cat_str, new_window_id=wid)

        prediction = self._predictor.predict_next_action()
        if prediction:
            ctx.prediction = prediction.to_dict() if hasattr(prediction, 'to_dict') else {"app": str(prediction)}

        await bus.publish(Event(
            type="pipeline.predictive",
            data={"predicted": prediction is not None},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class ClipboardStep:
    """Auto-copy relevant content to clipboard intelligence."""
    name = "clipboard"

    def __init__(self, clipboard_manager):
        self._clipboard = clipboard_manager

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False
        return self._clipboard is not None and ctx.analysis_result is not None

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        from window_aware import AppCategory
        analysis_text = ctx.analysis_result.get("text", "")
        category = AppCategory.UNKNOWN
        if ctx.active_window:
            category = getattr(ctx.active_window, 'category', AppCategory.UNKNOWN)

        auto_copies = self._clipboard.scan_and_copy(analysis_text, category=category)
        ctx.clipboard_auto_copies = [r.to_dict() if hasattr(r, 'to_dict') else r for r in auto_copies]

        # Auto-copy agent actions to clipboard
        for action in ctx.agent_actions:
            cmd = action.get("command", "")
            if cmd:
                from clipboard_intel import ClipSource
                self._clipboard.push(cmd, source=ClipSource.AGENT, category=category.value if hasattr(category, 'value') else '', label=action.get("description", ""))

        suggestions = self._clipboard.suggest_paste(category=category, screen_text=analysis_text)
        ctx.clipboard_suggestions = [s.to_dict() if hasattr(s, 'to_dict') else {"text": str(s)} for s in suggestions]

        await bus.publish(Event(
            type="pipeline.clipboard",
            data={"suggestions": len(ctx.clipboard_suggestions)},
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


# ===== Parallel Group (concurrent step execution) =====

class ParallelGroup:
    """
    Runs multiple pipeline steps concurrently via asyncio.gather.

    Acts as a single composite step — can be inserted into the pipeline
    wherever independent steps can safely run in parallel.
    """

    def __init__(self, steps: List, name: str = ""):
        self.steps = steps
        self.name = name or f"parallel({','.join(s.name for s in steps)})"

    def can_run(self, ctx: PipelineContext) -> bool:
        return any(s.can_run(ctx) for s in self.steps)

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        runnable = [s for s in self.steps if s.can_run(ctx)]
        if not runnable:
            return ctx

        async def _run_one(step):
            try:
                await step.execute(ctx, bus)
                ctx.steps_executed.append(step.name)
            except Exception as e:
                ctx.errors.append({
                    "step": step.name,
                    "error": str(e),
                })
                logger.error("Parallel step failed", step=step.name, error=str(e))

        await asyncio.gather(*[_run_one(s) for s in runnable])
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
        (context_mgr,       lambda: BuildContextStep(context_mgr, profile_mgr, app_state_ref)),
        (analyzer,          lambda: AnalyzeStep(analyzer)),
        (ocr_enhancer,      lambda: OCRPostProcessStep(ocr_enhancer)),
        (shell_agent,       lambda: SuggestActionsStep(shell_agent)),
        (action_library,    lambda: ActionTemplateStep(action_library)),
        (semantic_memory,   lambda: SemanticMemoryStep(semantic_memory)),
        (predictive_engine, lambda: PredictiveStep(predictive_engine)),
        (clipboard_manager, lambda: ClipboardStep(clipboard_manager)),
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
