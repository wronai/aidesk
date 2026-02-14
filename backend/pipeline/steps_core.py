"""Core pipeline steps: scan, detect, capture, crop, context, analyze, suggest, broadcast."""
import base64
import os
import time
from io import BytesIO
from typing import Any, Dict, List, Optional

import structlog
from PIL import Image

from event_bus import Event, EventBus, EventType
from .context import PipelineContext, PipelineProfile

logger = structlog.get_logger()


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

    def __init__(self, context_mgr, profile_mgr=None, app_state_ref=None, semantic_memory=None):
        self._context = context_mgr
        self._profiles = profile_mgr
        self._state = app_state_ref or {}
        self._semantic = semantic_memory
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

    def _recall_semantic_context(self, query: str, max_chars: int = 400) -> str:
        """Recall relevant past memories from SemanticMemory to enrich context."""
        if not self._semantic or not query:
            return ""
        try:
            recalled = self._semantic.recall_relevant(query, k=2)
            if not recalled:
                return ""
            snippets = []
            total = 0
            for mem in recalled:
                text = mem.content[:150] if hasattr(mem, 'content') else str(mem)[:150]
                if total + len(text) > max_chars:
                    break
                snippets.append(text)
                total += len(text)
            if snippets:
                return "🧠 Relevant memories:\n" + "\n".join(f"- {s}" for s in snippets)
        except Exception as e:
            logger.debug("Semantic recall in BuildContextStep failed", error=str(e))
        return ""

    def _assemble_context_parts(self, ctx: PipelineContext, focus_prefix: str) -> str:
        """Build context string from window info, screen summary, focus, semantic memory, and history."""
        base_context = self._context.get_context_string(n=5, max_length=500)
        parts = []
        if ctx.window_context_str:
            parts.append(ctx.window_context_str)
        if ctx.screen_summary:
            parts.append(f"📊 Ekran: {ctx.screen_summary}")
        if focus_prefix:
            parts.append(focus_prefix)
        # Enrich with semantic memory recall (uses screen summary or window context as query)
        recall_query = ctx.screen_summary or ctx.window_context_str or ""
        semantic_ctx = self._recall_semantic_context(recall_query)
        if semantic_ctx:
            parts.append(semantic_ctx)
        if base_context:
            parts.append(base_context)
        return "\n\n".join(parts)

    @staticmethod
    def _build_full_context(context_str: str, prompt_addon: str, transcript: str) -> str:
        """Prepend prompt addon and transcript to context string."""
        full = context_str
        if prompt_addon:
            full = f"{prompt_addon}\n\n{full}" if full else prompt_addon
        if transcript:
            prefix = f"🎤 Użytkownik powiedział: {transcript}"
            full = f"{prefix}\n\n{full}" if full else prefix
        return full

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

        cached = self._cached_key == cache_key
        if cached:
            ctx.context_str = self._cached_context_str
            ctx.full_context = self._cached_full_context
        else:
            ctx.context_str = self._assemble_context_parts(ctx, focus_prefix)
            ctx.full_context = self._build_full_context(ctx.context_str, prompt_addon, latest_transcript)
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

    def __init__(self, analyzer, cost_budget=None):
        self._analyzer = analyzer
        self._budget = cost_budget

    def can_run(self, ctx: PipelineContext) -> bool:
        return self._analyzer is not None and ctx.image_b64 is not None

    def _apply_budget_downgrade(self, requested_mode: str) -> tuple:
        """Try budget-aware mode downgrade. Returns (effective_mode, mode_switched)."""
        if not self._budget or not hasattr(self._budget, "get_suggested_mode"):
            return requested_mode, False

        try:
            effective_mode = self._budget.get_suggested_mode(requested_mode)
        except Exception as e:
            logger.warning("Budget mode suggestion failed", error=str(e))
            return requested_mode, False

        if effective_mode == requested_mode:
            return requested_mode, False

        if not hasattr(self._analyzer, "set_mode"):
            logger.warning(
                "Budget requested mode downgrade but analyzer has no set_mode",
                requested_mode=requested_mode, suggested_mode=effective_mode,
            )
            return requested_mode, False

        logger.warning("Budget exceeded, downgrading mode", from_mode=requested_mode, to_mode=effective_mode)
        try:
            switched = self._analyzer.set_mode(effective_mode)
            if switched is False:
                logger.warning("Analyzer rejected budget-safe mode switch", mode=effective_mode)
                return requested_mode, False
            return effective_mode, True
        except Exception as e:
            logger.warning("Failed to switch to budget-safe mode", mode=effective_mode, error=str(e))
            return requested_mode, False

    def _restore_mode(self, requested_mode: str):
        """Restore analyzer to user-selected mode after budget downgrade."""
        try:
            restored = self._analyzer.set_mode(requested_mode)
            if restored is False:
                logger.warning("Analyzer rejected restore of requested mode", mode=requested_mode)
        except Exception as e:
            logger.warning("Failed to restore analysis mode after budget downgrade", mode=requested_mode, error=str(e))

    def _record_spend(self, cost: float):
        """Record analysis cost to budget tracker."""
        if self._budget and cost > 0 and hasattr(self._budget, "record_spend"):
            try:
                self._budget.record_spend(cost, source="analysis")
            except Exception as e:
                logger.warning("Failed to record analysis spend", error=str(e), cost=cost)

    def _record_ocr_spend(self, cost: float):
        """Record OCR cost to budget tracker (separate from analysis)."""
        if self._budget and cost > 0 and hasattr(self._budget, "record_spend"):
            try:
                self._budget.record_spend(cost, source="ocr")
            except Exception as e:
                logger.warning("Failed to record OCR spend", error=str(e), cost=cost)

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        t0 = time.time()
        requested_mode = getattr(self._analyzer, "analysis_mode", "hybrid")
        effective_mode, mode_switched = self._apply_budget_downgrade(requested_mode)

        try:
            analysis = await self._analyzer.analyze(ctx.image_b64, ctx.full_context)
        except Exception:
            ctx.analysis_failed = True
            raise
        finally:
            if mode_switched:
                self._restore_mode(requested_mode)

        if analysis.get("error"):
            ctx.analysis_failed = True

        ctx.analysis_result = analysis
        analysis_cost = float(analysis.get("cost", 0.0) or 0.0)
        actual_mode = analysis.get("mode", effective_mode)
        latency_ms = round((time.time() - t0) * 1000)

        self._record_spend(analysis_cost)

        await bus.publish(Event(
            type=EventType.ANALYSIS_COMPLETED.value,
            data={
                "tokens": analysis.get("tokens", 0),
                "cost": analysis_cost,
                "provider": analysis.get("provider", "unknown"),
                "model": analysis.get("model", "unknown"),
                "mode": actual_mode,
                "latency_ms": latency_ms,
                "has_ocr": "ocr" in analysis,
                "requested_mode": requested_mode,
                "effective_mode": actual_mode,
                "budget_degraded": actual_mode != requested_mode,
            },
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))

        # Emit separate OCR cost event when VLM OCR reports cost
        ocr_data = analysis.get("ocr")
        if ocr_data and ocr_data.get("engine") == "vlm_ocr":
            ocr_mgr = getattr(self._analyzer, "ocr_manager", None)
            if ocr_mgr:
                active = ocr_mgr.active_engine
                ocr_cost = getattr(active, "_last_cost", 0.0) if active else 0.0
                if ocr_cost > 0:
                    self._record_ocr_spend(ocr_cost)
                    await bus.publish(Event(
                        type=EventType.OCR_COST.value,
                        data={
                            "engine": "vlm_ocr",
                            "cost": round(ocr_cost, 6),
                            "tokens": getattr(active, "_total_tokens_used", 0),
                            "model": getattr(active, "model", "unknown"),
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
            and not ctx.analysis_failed
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
