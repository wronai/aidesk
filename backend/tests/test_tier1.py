"""
Unit tests for Tier 1 feature modules:
- MultiMonitor Intelligence (multi_monitor.py)
- Semantic Memory (semantic_memory.py)
- Action Templates (action_templates.py)
- OCR Post-Processing (ocr_post_process.py)
- Predictive Pre-fetching (predictive_engine.py)
"""
import asyncio
import os
import sys
import tempfile
import time

import pytest

# Add backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from window_aware import AppCategory, WindowInfo, MonitorInfo


# ===== Multi-Monitor Intelligence =====

from multi_monitor import (
    MonitorAwareCapture,
    MonitorActivity,
    MultiMonitorSnapshot,
    create_multi_monitor_from_env,
)


class TestMonitorAwareCapture:
    """Test multi-monitor intelligence."""

    def _make_monitors(self):
        """Create a 2-monitor setup: left (1920x1080) + right (2560x1440)."""
        return [
            MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True),
            MonitorInfo(index=1, name="HDMI-1", x=1920, y=0, width=2560, height=1440),
        ]

    def _make_window(self, x=100, y=100, width=800, height=600, category=AppCategory.IDE, wid=1001):
        return WindowInfo(
            window_id=wid, title="test.py", wm_class="code", wm_class_name="Code",
            x=x, y=y, width=width, height=height, category=category, monitor_index=0,
        )

    def test_create_from_env(self):
        mm = create_multi_monitor_from_env()
        assert mm is not None
        assert mm.active_only is True

    def test_detect_active_monitor_from_window(self):
        mm = MonitorAwareCapture()
        monitors = self._make_monitors()
        # Window on left monitor (monitor_index=0, center at 500+400=900)
        win = self._make_window(x=500, y=300)
        assert mm.detect_active_monitor(monitors, win) == 0
        # Window on right monitor — set monitor_index=-1 to force center-based lookup
        win_right = self._make_window(x=2500, y=300)
        win_right.monitor_index = -1
        assert mm.detect_active_monitor(monitors, win_right) == 1

    def test_detect_active_monitor_primary_fallback(self):
        mm = MonitorAwareCapture()
        monitors = self._make_monitors()
        # No active window → mouse monitor or primary fallback
        result = mm.detect_active_monitor(monitors, None)
        # Result depends on actual mouse position — just verify it's a valid index
        assert result in (0, 1)

    def test_build_snapshot(self):
        mm = MonitorAwareCapture()
        monitors = self._make_monitors()
        win = self._make_window(x=500, y=300)
        snapshot = mm.build_snapshot(monitors=monitors, active_window=win)

        assert snapshot.total_monitors == 2
        assert snapshot.active_monitor_index == 0
        assert len(snapshot.monitors) == 2
        assert len(snapshot.prioritized_order) == 2

    def test_snapshot_description(self):
        mm = MonitorAwareCapture(include_description=True)
        monitors = self._make_monitors()
        win = self._make_window(x=500, y=300, category=AppCategory.IDE)
        snapshot = mm.build_snapshot(
            monitors=monitors, all_windows=[win], active_window=win,
        )
        assert "🖥️ Monitory:" in snapshot.description
        assert "ACTIVE" in snapshot.description

    def test_single_monitor_skip(self):
        mm = MonitorAwareCapture()
        monitors = [MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True)]
        # build_snapshot always records monitors, but MultiMonitorStep skips for single
        snapshot = mm.build_snapshot(monitors=monitors)
        assert snapshot.total_monitors == 1

    def test_get_capture_roi(self):
        mm = MonitorAwareCapture()
        mon = MonitorInfo(index=1, name="HDMI-1", x=1920, y=0, width=2560, height=1440)
        roi = mm.get_capture_roi_for_monitor(mon)
        assert roi["left"] == 1920
        assert roi["width"] == 2560

    def test_monitors_to_analyze_active_only(self):
        mm = MonitorAwareCapture(active_only=True)
        monitors = self._make_monitors()
        # Window on right monitor with correct monitor_index
        win = self._make_window(x=2500, y=300)
        win.monitor_index = 1
        snapshot = mm.build_snapshot(monitors=monitors, active_window=win)
        to_analyze = mm.get_monitors_to_analyze(snapshot)
        assert len(to_analyze) == 1
        assert to_analyze[0] == 1  # right monitor only

    def test_monitors_to_analyze_all(self):
        mm = MonitorAwareCapture(active_only=False)
        monitors = self._make_monitors()
        win = self._make_window(x=500, y=300)
        snapshot = mm.build_snapshot(monitors=monitors, active_window=win)
        to_analyze = mm.get_monitors_to_analyze(snapshot)
        assert len(to_analyze) == 2

    def test_priority_scoring(self):
        mm = MonitorAwareCapture()
        monitors = self._make_monitors()
        win = self._make_window(x=500, y=300, category=AppCategory.IDE)
        snapshot = mm.build_snapshot(monitors=monitors, all_windows=[win], active_window=win)
        # Monitor 0 (has active window + primary) should have highest priority
        activities = mm.prioritize_monitors(snapshot)
        assert activities[0].monitor.index == 0
        assert activities[0].priority > activities[1].priority

    def test_stats(self):
        mm = MonitorAwareCapture()
        monitors = self._make_monitors()
        mm.build_snapshot(monitors=monitors)
        stats = mm.get_stats()
        assert stats["total_snapshots"] == 1

    def test_snapshot_to_dict(self):
        mm = MonitorAwareCapture()
        monitors = self._make_monitors()
        snapshot = mm.build_snapshot(monitors=monitors)
        d = snapshot.to_dict()
        assert "total_monitors" in d
        assert "monitors" in d


# ===== Semantic Memory =====

from semantic_memory import SemanticMemory, MemoryItem, create_semantic_memory_from_env


class TestSemanticMemory:
    """Test semantic memory with keyword fallback (no sentence-transformers required)."""

    def _make_memory(self, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        return SemanticMemory(
            db_path=db_path,
            enabled=False,  # keyword fallback mode (no model needed)
            max_memories=100,
            **kwargs,
        )

    def test_create_from_env(self):
        sm = create_semantic_memory_from_env()
        assert sm is not None

    def test_add_memory(self):
        sm = self._make_memory()
        mid = sm.add_memory("Test content", context_type="screen")
        assert mid is not None
        assert sm.total_memories == 1

    def test_add_empty_memory(self):
        sm = self._make_memory()
        mid = sm.add_memory("")
        assert mid is None

    def test_recall_recent(self):
        sm = self._make_memory()
        sm.add_memory("First item", context_type="screen")
        sm.add_memory("Second item", context_type="speech")
        sm.add_memory("Third item", context_type="screen")

        recent = sm.recall_recent(n=2)
        assert len(recent) == 2
        assert recent[0].content == "Third item"

    def test_recall_recent_with_filter(self):
        sm = self._make_memory()
        sm.add_memory("Screen A", context_type="screen")
        sm.add_memory("Speech B", context_type="speech")
        sm.add_memory("Screen C", context_type="screen")

        screens = sm.recall_recent(n=10, context_type="screen")
        assert len(screens) == 2
        assert all(m.context_type == "screen" for m in screens)

    def test_keyword_search(self):
        sm = self._make_memory()
        sm.add_memory("Python import error in Django project", context_type="screen")
        sm.add_memory("Git merge conflict resolved", context_type="screen")
        sm.add_memory("Docker container startup failed", context_type="screen")

        results = sm.recall_relevant("Python error", k=2)
        assert len(results) > 0
        assert results[0].content == "Python import error in Django project"

    def test_get_context_string(self):
        sm = self._make_memory()
        sm.add_memory("Test item 1")
        sm.add_memory("Test item 2")
        ctx = sm.get_context_string(n=2)
        assert "Test item" in ctx

    def test_prune_oldest(self):
        sm = self._make_memory()
        sm.max_memories = 3
        for i in range(5):
            sm.add_memory(f"Item {i}", timestamp=time.time() + i * 10)
        # After 5 inserts with max=3, should have pruned to at most 4
        # (prune triggers after exceeding, removes overshoot)
        assert sm.total_memories <= 4

    def test_compress_old_context(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        # Use enabled=True but without embedder — keyword fallback still has DB
        sm = SemanticMemory(db_path=db_path, enabled=False, max_memories=1000)
        # Force _db to be initialized for compression
        sm._init_db()

        # Add old memories
        old_ts = time.time() - 7200  # 2 hours ago
        for i in range(10):
            sm.add_memory(f"Old item {i}", timestamp=old_ts + i)
        # Add recent memories
        sm.add_memory("Recent item", timestamp=time.time())

        compressed = sm.compress_old_context(before_timestamp=time.time() - 3600)
        assert compressed > 0

    def test_memory_item_to_dict(self):
        item = MemoryItem(
            memory_id="test_123",
            content="Test content",
            context_type="screen",
            timestamp=time.time(),
        )
        d = item.to_dict()
        assert d["memory_id"] == "test_123"
        assert d["context_type"] == "screen"

    def test_stats(self):
        sm = self._make_memory()
        sm.add_memory("Test")
        stats = sm.get_stats()
        assert stats["total_memories"] == 1
        assert stats["model"] == "keyword_fallback"

    def test_close(self):
        sm = self._make_memory()
        sm.add_memory("Test")
        sm.close()
        assert sm._db is None


# ===== Action Templates =====

from action_templates import (
    AppActionLibrary,
    ActionTemplate,
    ScoredAction,
    create_action_library_from_env,
)


class TestAppActionLibrary:
    """Test action template learning and suggestion engine."""

    def _make_lib(self, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        return AppActionLibrary(db_path=db_path, **kwargs)

    def test_create_from_env(self):
        lib = create_action_library_from_env()
        assert lib is not None
        assert lib.enabled

    def test_seed_templates_loaded(self):
        lib = self._make_lib()
        assert len(lib._templates) >= 5  # seed templates

    def test_suggest_python_module_error(self):
        lib = self._make_lib()
        text = "ModuleNotFoundError: No module named 'requests'"
        results = lib.suggest_with_confidence(text, app_category="terminal")
        assert len(results) > 0
        assert "pip install requests" in results[0].command

    def test_suggest_node_module_error(self):
        lib = self._make_lib()
        text = "Cannot find module 'express'"
        results = lib.suggest_with_confidence(text, app_category="terminal")
        assert len(results) > 0
        assert "npm install express" in results[0].command

    def test_suggest_wrong_category_filtered(self):
        lib = self._make_lib()
        text = "ModuleNotFoundError: No module named 'flask'"
        results = lib.suggest_with_confidence(text, app_category="browser")
        # browser category should not match terminal/ide templates
        assert len(results) == 0

    def test_confidence_starts_neutral(self):
        lib = self._make_lib()
        t = lib.get_template("py_module_not_found_terminal")
        assert t is not None
        assert t.confidence == 0.5  # neutral prior (0 approved, 0 rejected)

    def test_learn_from_approval(self):
        lib = self._make_lib()
        tid = "py_module_not_found_terminal"
        for _ in range(3):
            lib.learn_from_approval(tid)
        t = lib.get_template(tid)
        assert t.times_approved == 3
        assert t.confidence == 1.0

    def test_learn_from_rejection(self):
        lib = self._make_lib()
        tid = "py_module_not_found_terminal"
        # Reset times_approved first (seed templates start at 0)
        t = lib.get_template(tid)
        t.times_approved = 0
        t.times_rejected = 0
        lib._persist_template(t)

        lib.learn_from_approval(tid)
        lib.learn_from_rejection(tid)
        t = lib.get_template(tid)
        assert t.times_rejected == 1
        assert t.confidence == 0.5

    def test_auto_execute_promotion(self):
        lib = self._make_lib()
        tid = "py_module_not_found_terminal"
        t = lib.get_template(tid)
        # Reset stats
        t.times_approved = 0
        t.times_rejected = 0
        t.is_auto_approved = False
        lib._persist_template(t)

        assert not t.should_auto_execute

        for _ in range(t.auto_approve_after):
            lib.learn_from_approval(tid)

        t = lib.get_template(tid)
        assert t.should_auto_execute

    def test_auto_execute_revocation(self):
        lib = self._make_lib()
        tid = "py_module_not_found_terminal"
        for _ in range(5):
            lib.learn_from_approval(tid)
        assert lib.get_template(tid).is_auto_approved

        # Reject enough to drop confidence below 0.7
        for _ in range(5):
            lib.learn_from_rejection(tid)
        assert not lib.get_template(tid).is_auto_approved

    def test_add_custom_template(self):
        lib = self._make_lib()
        t = lib.add_template(
            template_id="custom_lint",
            app_category="ide",
            trigger_pattern=r"flake8.*error",
            command_template="flake8 --fix .",
            description="Auto-fix lint errors",
        )
        assert t.template_id == "custom_lint"
        assert lib.get_template("custom_lint") is not None

    def test_remove_template(self):
        lib = self._make_lib()
        lib.add_template("to_remove", "ide", "pattern", "cmd")
        assert lib.remove_template("to_remove")
        assert lib.get_template("to_remove") is None

    def test_export_import(self):
        lib1 = self._make_lib()
        json_str = lib1.export_templates()
        assert "templates" in json_str

        lib2 = self._make_lib()
        # Remove all seed templates first
        for tid in list(lib2._templates.keys()):
            lib2.remove_template(tid)
        count = lib2.import_templates(json_str)
        assert count > 0

    def test_get_templates_for_app(self):
        lib = self._make_lib()
        terminal_templates = lib.get_templates_for_app("terminal")
        assert len(terminal_templates) > 0

    def test_scored_action_to_dict(self):
        t = ActionTemplate(
            template_id="test", app_category="ide",
            trigger_pattern="pattern", command_template="cmd",
        )
        action = ScoredAction(template=t, command="resolved cmd", confidence=0.85)
        d = action.to_dict()
        assert d["confidence"] == 0.85
        assert d["command"] == "resolved cmd"

    def test_stats(self):
        lib = self._make_lib()
        stats = lib.get_stats()
        assert stats["enabled"]
        assert stats["total_templates"] > 0

    def test_close(self):
        lib = self._make_lib()
        lib.close()
        assert lib._db is None


# ===== OCR Post-Processing =====

from ocr_post_process import (
    OCREnhancer,
    PostProcessResult,
    TextType,
    create_ocr_enhancer_from_env,
)


class TestOCREnhancer:
    """Test OCR post-processing pipeline."""

    def test_create_from_env(self):
        enh = create_ocr_enhancer_from_env()
        assert enh is not None

    def test_detect_code_type(self):
        enh = OCREnhancer(enable_spell_check=False)
        code_text = """
def hello():
    print("Hello, World!")

class MyClass:
    def __init__(self):
        self.value = 42

import os
from pathlib import Path
"""
        result = enh.enhance(code_text)
        assert result.text_type == TextType.CODE

    def test_detect_terminal_type(self):
        enh = OCREnhancer(enable_spell_check=False)
        terminal_text = """
$ git status
ERROR: Connection refused
WARNING: disk space low
[10:23:45] INFO: Server started
npm ERR! code ELIFECYCLE
Traceback (most recent call last):
"""
        result = enh.enhance(terminal_text)
        assert result.text_type == TextType.TERMINAL

    def test_fix_code_ocr_errors(self):
        enh = OCREnhancer(enable_spell_check=False)
        # se1f → self
        result = enh.enhance("def __init__(se1f):", hint_type=TextType.CODE)
        assert "self" in result.enhanced_text

    def test_fix_terminal_ocr_errors(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("ERR0R: FA1LED to start", hint_type=TextType.TERMINAL)
        assert "ERROR" in result.enhanced_text
        assert "FAILED" in result.enhanced_text

    def test_fix_number_letter_confusion(self):
        enh = OCREnhancer(enable_spell_check=False)
        # O in numbers → 0
        result = enh.enhance("port 8O8O is open", hint_type=TextType.TERMINAL)
        assert "8080" in result.enhanced_text

    def test_fix_prose_common_errors(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("tbe quick brown fox. witb tbe lazy dog.", hint_type=TextType.PROSE)
        assert "the" in result.enhanced_text
        assert "with" in result.enhanced_text

    def test_merge_broken_words(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("pro-\ngramming is fun")
        assert "programming" in result.enhanced_text

    def test_empty_text(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("")
        assert result.enhanced_text == ""
        assert result.text_type == TextType.UNKNOWN

    def test_disabled(self):
        enh = OCREnhancer(enabled=False)
        result = enh.enhance("se1f.va1ue = Tme")
        assert result.enhanced_text == "se1f.va1ue = Tme"  # no changes

    def test_post_process_result_to_dict(self):
        result = PostProcessResult(
            original_text="raw",
            enhanced_text="fixed",
            text_type=TextType.CODE,
            corrections_count=3,
            processing_time_ms=1.5,
        )
        d = result.to_dict()
        assert d["text_type"] == "code"
        assert d["corrections_count"] == 3

    def test_stats(self):
        enh = OCREnhancer(enable_spell_check=False)
        enh.enhance("some text", hint_type=TextType.PROSE)
        stats = enh.get_stats()
        assert stats["total_processed"] == 1

    def test_preserves_valid_code(self):
        enh = OCREnhancer(enable_spell_check=False)
        valid_code = "def __init__(self):\n    self.value = None\n    return True"
        result = enh.enhance(valid_code, hint_type=TextType.CODE)
        # Valid code should pass through mostly unchanged
        assert "def __init__(self)" in result.enhanced_text
        assert "self.value" in result.enhanced_text


# ===== Predictive Pre-fetching =====

from predictive_engine import (
    PredictiveAnalyzer,
    PredictionResult,
    PrefetchCache,
    TransitionStats,
    create_predictive_engine_from_env,
)


class TestPredictiveAnalyzer:
    """Test predictive pre-fetching engine."""

    def test_create_from_env(self):
        pe = create_predictive_engine_from_env()
        assert pe is not None

    def test_observe_transitions(self):
        pe = PredictiveAnalyzer(min_observations=2)
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("terminal", 1002)
        pe.observe_window_change("ide", 1001)
        assert pe.total_transitions == 2

    def test_predict_next_action(self):
        pe = PredictiveAnalyzer(confidence_threshold=0.5, min_observations=2)

        # Build pattern: IDE → Terminal (always)
        for _ in range(5):
            pe.observe_window_change("ide", 1001)
            pe.observe_window_change("terminal", 1002)

        prediction = pe.predict_next_action("ide")
        assert prediction is not None
        assert prediction.predicted_app == "terminal"
        assert prediction.confidence >= 0.5

    def test_no_prediction_below_threshold(self):
        pe = PredictiveAnalyzer(confidence_threshold=0.9, min_observations=2)

        # Mixed transitions → low confidence
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("terminal", 1002)
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("browser", 1003)
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("terminal", 1002)

        prediction = pe.predict_next_action("ide")
        # Should be None because confidence for any single target < 0.9
        # (terminal has 2/3 = 0.67, browser has 1/3 = 0.33)
        assert prediction is None

    def test_no_prediction_insufficient_data(self):
        pe = PredictiveAnalyzer(min_observations=5)
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("terminal", 1002)
        # Only 1 transition, need 5
        prediction = pe.predict_next_action("ide")
        assert prediction is None

    def test_transition_matrix(self):
        pe = PredictiveAnalyzer(min_observations=1)
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("terminal", 1002)
        pe.observe_window_change("ide", 1001)

        matrix = pe.get_transition_matrix()
        assert "ide" in matrix
        assert "terminal" in matrix["ide"]
        assert matrix["ide"]["terminal"] == 1.0

    def test_top_patterns(self):
        pe = PredictiveAnalyzer(min_observations=1)
        for _ in range(5):
            pe.observe_window_change("ide", 1001)
            pe.observe_window_change("terminal", 1002)

        patterns = pe.get_top_patterns(3)
        assert len(patterns) > 0
        assert patterns[0]["from"] == "ide"
        assert patterns[0]["to"] == "terminal"

    def test_prefetch_cache(self):
        pe = PredictiveAnalyzer(prefetch_ttl=5.0)
        cache = PrefetchCache(
            window_id=1001,
            app_category="terminal",
            ocr_text="$ git status",
            timestamp=time.time(),
        )
        pe._prefetch_cache[1001] = cache

        result = pe.get_prefetched(1001)
        assert result is not None
        assert result.ocr_text == "$ git status"

    def test_prefetch_cache_expired(self):
        pe = PredictiveAnalyzer(prefetch_ttl=0.1)
        cache = PrefetchCache(
            window_id=1001,
            app_category="terminal",
            timestamp=time.time() - 1.0,  # expired
            ttl=0.1,
        )
        pe._prefetch_cache[1001] = cache

        result = pe.get_prefetched(1001)
        assert result is None

    def test_cleanup_cache(self):
        pe = PredictiveAnalyzer(prefetch_ttl=0.01)
        pe._prefetch_cache[1001] = PrefetchCache(
            window_id=1001, app_category="ide",
            timestamp=time.time() - 1.0, ttl=0.01,
        )
        pe._prefetch_cache[1002] = PrefetchCache(
            window_id=1002, app_category="terminal",
            timestamp=time.time(), ttl=100.0,
        )
        pe.cleanup_cache()
        assert 1001 not in pe._prefetch_cache
        assert 1002 in pe._prefetch_cache

    def test_prediction_result_to_dict(self):
        pred = PredictionResult(
            predicted_app="terminal",
            confidence=0.85,
            predicted_window_id=1002,
        )
        d = pred.to_dict()
        assert d["predicted_app"] == "terminal"
        assert d["confidence"] == 0.85

    def test_get_prefetched_for_category(self):
        pe = PredictiveAnalyzer()
        pe._prefetch_cache[1001] = PrefetchCache(
            window_id=1001, app_category="terminal",
            ocr_text="hello", timestamp=time.time(), ttl=100.0,
        )
        result = pe.get_prefetched_for_category("terminal")
        assert result is not None
        assert result.ocr_text == "hello"

    def test_stats(self):
        pe = PredictiveAnalyzer(min_observations=1)
        pe.observe_window_change("ide", 1001)
        pe.observe_window_change("terminal", 1002)
        pe.predict_next_action("ide")

        stats = pe.get_stats()
        assert stats["total_transitions"] == 1
        assert stats["total_predictions"] == 1
        assert stats["enabled"]

    def test_disabled(self):
        pe = PredictiveAnalyzer(enabled=False)
        pe.observe_window_change("ide", 1001)
        assert pe.total_transitions == 0
        assert pe.predict_next_action("ide") is None


# ===== Pipeline Step Integration Tests =====

from pipeline import (
    PipelineContext,
    MultiMonitorStep,
    SemanticMemoryStep,
    ActionTemplateStep,
    OCRPostProcessStep,
    PredictiveStep,
)
from event_bus import EventBus


class TestTier1PipelineSteps:
    """Test Tier 1 pipeline steps with mock data."""

    def _make_bus(self):
        return EventBus()

    def _make_ctx(self, **kwargs):
        defaults = dict(profile="normal")
        defaults.update(kwargs)
        return PipelineContext(**defaults)

    def _make_window(self, category=AppCategory.IDE, wid=1001):
        return WindowInfo(
            window_id=wid, title="test.py", wm_class="code",
            wm_class_name="Code", pid=1234, x=100, y=200,
            width=800, height=600, category=category,
        )

    # -- Multi-Monitor Step --

    def test_multi_monitor_step_skips_single(self):
        """Multi-monitor step should skip when only 1 monitor."""
        from unittest.mock import MagicMock
        mm = MonitorAwareCapture()
        wm = MagicMock()
        wm.get_monitors.return_value = [
            MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True),
        ]
        step = MultiMonitorStep(mm, wm)
        ctx = self._make_ctx()
        bus = self._make_bus()
        ctx2 = asyncio.get_event_loop().run_until_complete(step.execute(ctx, bus))
        assert ctx2.multi_monitor_snapshot is None

    # -- Semantic Memory Step --

    def test_semantic_memory_step_stores_and_recalls(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        sm = SemanticMemory(db_path=db_path, enabled=False)

        step = SemanticMemoryStep(sm)
        ctx = self._make_ctx(
            analysis_result={"text": "Python error detected in VS Code"},
            active_window=self._make_window(),
        )
        bus = self._make_bus()

        assert step.can_run(ctx)
        ctx2 = asyncio.get_event_loop().run_until_complete(step.execute(ctx, bus))
        assert sm.total_memories == 1

    def test_semantic_memory_step_skips_no_analysis(self):
        sm = SemanticMemory(db_path=":memory:", enabled=False)
        step = SemanticMemoryStep(sm)
        ctx = self._make_ctx(analysis_result=None)
        assert not step.can_run(ctx)

    # -- Action Template Step --

    def test_action_template_step_suggests(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        lib = AppActionLibrary(db_path=db_path)

        step = ActionTemplateStep(lib)
        ctx = self._make_ctx(
            analysis_result={
                "text": "ModuleNotFoundError: No module named 'flask'",
                "ocr": {"text": "ModuleNotFoundError: No module named 'flask'"},
            },
            active_window=self._make_window(category=AppCategory.TERMINAL),
        )
        bus = self._make_bus()

        assert step.can_run(ctx)
        ctx2 = asyncio.get_event_loop().run_until_complete(step.execute(ctx, bus))
        assert len(ctx2.template_actions) > 0

    # -- OCR Post-Process Step --

    def test_ocr_post_process_step(self):
        enh = OCREnhancer(enable_spell_check=False)
        step = OCRPostProcessStep(enh)
        ctx = self._make_ctx(
            analysis_result={
                "text": "analysis",
                "ocr": {"text": "ERR0R: FA1LED"},
            },
        )
        bus = self._make_bus()

        assert step.can_run(ctx)
        ctx2 = asyncio.get_event_loop().run_until_complete(step.execute(ctx, bus))
        assert ctx2.ocr_enhanced
        assert ctx2.ocr_corrections > 0

    def test_ocr_post_process_step_skips_no_ocr(self):
        enh = OCREnhancer(enable_spell_check=False)
        step = OCRPostProcessStep(enh)
        ctx = self._make_ctx(analysis_result={"text": "hello"})
        assert not step.can_run(ctx)

    # -- Predictive Step --

    def test_predictive_step_records_transition(self):
        pe = PredictiveAnalyzer(min_observations=1)
        step = PredictiveStep(pe)
        bus = self._make_bus()

        # First window
        ctx1 = self._make_ctx(active_window=self._make_window(AppCategory.IDE, 1001))
        asyncio.get_event_loop().run_until_complete(step.execute(ctx1, bus))

        # Switch to terminal
        ctx2 = self._make_ctx(active_window=self._make_window(AppCategory.TERMINAL, 1002))
        asyncio.get_event_loop().run_until_complete(step.execute(ctx2, bus))

        assert pe.total_transitions == 1

    def test_predictive_step_skips_when_disabled(self):
        pe = PredictiveAnalyzer(enabled=False)
        step = PredictiveStep(pe)
        ctx = self._make_ctx(active_window=self._make_window())
        assert not step.can_run(ctx)


# ===== PipelineContext Fields =====

class TestPipelineContextTier1Fields:
    """Verify Tier 1 fields exist on PipelineContext."""

    def test_multi_monitor_fields(self):
        ctx = PipelineContext()
        assert ctx.multi_monitor_snapshot is None
        assert ctx.monitor_description == ""

    def test_semantic_memory_fields(self):
        ctx = PipelineContext()
        assert ctx.recalled_memories == []

    def test_action_template_fields(self):
        ctx = PipelineContext()
        assert ctx.template_actions == []

    def test_ocr_post_process_fields(self):
        ctx = PipelineContext()
        assert ctx.ocr_enhanced is False
        assert ctx.ocr_corrections == 0

    def test_predictive_fields(self):
        ctx = PipelineContext()
        assert ctx.prediction is None
        assert ctx.used_prefetch is False
