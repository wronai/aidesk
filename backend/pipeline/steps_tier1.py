"""Tier 1 pipeline steps: multi-monitor, semantic memory, action templates, OCR, predictive, clipboard."""
import time
from typing import Optional

import structlog

from event_bus import Event, EventBus, EventType
from .context import PipelineContext, PipelineProfile

logger = structlog.get_logger()


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

    @staticmethod
    def _extract_category(ctx: PipelineContext):
        """Extract AppCategory from active window."""
        from window_aware import AppCategory
        if ctx.active_window:
            return getattr(ctx.active_window, 'category', AppCategory.UNKNOWN)
        return AppCategory.UNKNOWN

    def _push_agent_actions(self, ctx: PipelineContext, category):
        """Push agent-suggested commands to clipboard queue."""
        from clipboard_intel import ClipSource
        for action in ctx.agent_actions:
            cmd = action.get("command", "")
            if cmd:
                self._clipboard.push(
                    cmd, source=ClipSource.AGENT,
                    category=category.value if hasattr(category, 'value') else '',
                    label=action.get("description", ""),
                )

    def _collect_queue_stats(self) -> tuple:
        """Collect queue size and active sources via fast path or fallback."""
        queue = getattr(self._clipboard, "queue", None)
        if queue is not None and hasattr(queue, "get_all"):
            try:
                items = queue.get_all()
                source_values = set()
                for item in items:
                    src = getattr(item, "source", None)
                    if hasattr(src, "value"):
                        source_values.add(src.value)
                    elif src:
                        source_values.add(str(src))
                return len(items), sorted(source_values)
            except Exception:
                pass

        if hasattr(self._clipboard, "get_stats"):
            stats = self._clipboard.get_stats()
            if isinstance(stats, dict):
                sources = stats.get("sources", {})
                non_zero = [k for k, v in sources.items() if v] if isinstance(sources, dict) else []
                return stats.get("queue_size", 0), non_zero

        return 0, []

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        analysis_text = ctx.analysis_result.get("text", "")
        category = self._extract_category(ctx)

        auto_copies = self._clipboard.scan_and_copy(analysis_text, category=category)
        ctx.clipboard_auto_copies = [r.to_dict() if hasattr(r, 'to_dict') else r for r in auto_copies]

        self._push_agent_actions(ctx, category)

        suggestions = self._clipboard.suggest_paste(category=category, screen_text=analysis_text)
        ctx.clipboard_suggestions = [s.to_dict() if hasattr(s, 'to_dict') else {"text": str(s)} for s in suggestions]

        queue_size, non_zero_sources = self._collect_queue_stats()

        await bus.publish(Event(
            type=EventType.CLIPBOARD_UPDATED.value,
            data={
                "auto_copied": len(ctx.clipboard_auto_copies),
                "suggestions": len(ctx.clipboard_suggestions),
                "queue_size": queue_size,
                "sources": non_zero_sources,
            },
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx


class ClipboardRelationStep:
    """Proactive clipboard↔screen relation detection.

    Runs ClipboardRelationSkill.detect() against the latest OCR/analysis text
    combined with the current clipboard top item.  When a strong intent is found,
    emits a CLIPBOARD_RELATION event so the overlay can show a suggestion badge
    without requiring the user to manually trigger selection analysis.

    Skipped on FAST profile and when clipboard is empty.
    """
    name = "clipboard_relation"

    def __init__(self, clipboard_manager, app_state_ref=None):
        self._clipboard = clipboard_manager
        self._state = app_state_ref or {}
        self._skill = None  # lazy-init to avoid circular imports at module load
        self._last_intent_key: Optional[str] = None  # dedup repeated events

    def can_run(self, ctx: PipelineContext) -> bool:
        if ctx.profile == PipelineProfile.FAST.value:
            return False
        if self._clipboard is None or ctx.analysis_result is None:
            return False
        # Need clipboard content
        recent = self._clipboard.queue.get_recent(1) if hasattr(self._clipboard, 'queue') else []
        return bool(recent)

    def _ensure_skill(self):
        """Lazy-init ClipboardRelationSkill to avoid circular imports."""
        if self._skill is None:
            try:
                from skills.clipboard_relation import ClipboardRelationSkill
                self._skill = ClipboardRelationSkill()
            except Exception as e:
                logger.warning("ClipboardRelationStep: failed to init skill", error=str(e))
        return self._skill

    @staticmethod
    def _extract_screen_text(ctx: PipelineContext) -> str:
        """Build screen text from analysis + OCR."""
        analysis_text = ctx.analysis_result.get("text", "")
        ocr_text = ""
        if ctx.analysis_result.get("ocr") and ctx.analysis_result["ocr"].get("text"):
            ocr_text = ctx.analysis_result["ocr"]["text"]
        return f"{analysis_text}\n{ocr_text}".strip()

    def _build_skill_ctx(self, screen_text: str, clipboard_top: str):
        """Build minimal SkillContext for clipboard relation detection."""
        from skills.base import SkillContext
        latest_window = self._state.get("latest_window") or {}
        return SkillContext(
            text=screen_text[:500],
            clipboard_top=clipboard_top,
            window_category=latest_window.get("category", "unknown"),
            window_title=latest_window.get("title", ""),
            cwd=latest_window.get("cwd", ""),
            locale=self._state.get("locale", "pl"),
        )

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        skill = self._ensure_skill()
        if not skill:
            return ctx

        screen_text = self._extract_screen_text(ctx)
        if len(screen_text) < 10:
            return ctx

        recent = self._clipboard.queue.get_recent(1)
        clipboard_top = recent[0].text if recent else ""
        if not clipboard_top:
            return ctx

        skill_ctx = self._build_skill_ctx(screen_text, clipboard_top)

        confidence = skill.detect(screen_text[:500], skill_ctx)
        if confidence < 0.5:
            self._last_intent_key = None
            return ctx

        intent = skill._best_intent(screen_text[:500], skill_ctx)
        if not intent:
            self._last_intent_key = None
            return ctx

        # Dedup: don't re-emit same intent for same clipboard+screen pair
        intent_key = f"{intent.name}:{clipboard_top[:80]}:{screen_text[:80]}"
        if intent_key == self._last_intent_key:
            return ctx
        self._last_intent_key = intent_key

        options = skill.get_options(screen_text[:500], skill_ctx)

        await bus.publish(Event(
            type=EventType.CLIPBOARD_RELATION.value,
            data={
                "intent": intent.name,
                "confidence": round(confidence, 3),
                "label": intent.label,
                "icon": intent.icon,
                "options_count": len(options),
                "clipboard_preview": clipboard_top[:60],
                "screen_preview": screen_text[:60],
            },
            source=self.name,
            correlation_id=ctx.correlation_id,
        ))
        return ctx
