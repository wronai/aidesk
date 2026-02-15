"""Tests for pipeline orchestrator nfo instrumentation and _extract_step_metrics."""

import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.context import PipelineContext
from pipeline.orchestrator import (
    PipelineOrchestrator,
    _extract_step_metrics,
    _analyze_metrics,
)
from event_bus import EventBus


# ---------------------------------------------------------------------------
# _extract_step_metrics
# ---------------------------------------------------------------------------

class TestExtractStepMetrics:

    def test_scan_windows_metrics(self):
        ctx = PipelineContext()
        win = MagicMock()
        win.title = "Visual Studio Code"
        ctx.all_windows = [win, MagicMock(), MagicMock()]
        ctx.active_window = win

        m = _extract_step_metrics("scan_windows", ctx)
        assert m["windows_total"] == 3
        assert "Visual Studio Code" in m["active_window"]

    def test_scan_windows_no_active(self):
        ctx = PipelineContext()
        ctx.all_windows = [MagicMock()]
        ctx.active_window = None

        m = _extract_step_metrics("scan_windows", ctx)
        assert m["windows_total"] == 1
        assert m["active_window"] == ""

    def test_capture_screen_metrics(self):
        ctx = PipelineContext()
        ctx.image_b64 = "A" * 4096  # ~3KB base64

        m = _extract_step_metrics("capture_screen", ctx)
        assert m["has_change"] is True
        assert m["data_size_kb"] > 0

    def test_capture_screen_no_image(self):
        ctx = PipelineContext()
        ctx.image_b64 = None

        m = _extract_step_metrics("capture_screen", ctx)
        assert m["has_change"] is False
        assert m["data_size_kb"] == 0.0

    def test_build_context_metrics(self):
        ctx = PipelineContext()
        ctx.full_context = "x" * 500
        ctx.recalled_memories = ["mem1", "mem2"]

        m = _extract_step_metrics("build_context", ctx)
        assert m["context_length"] == 500
        assert m["memories_recalled"] == 2

    def test_suggest_actions_metrics(self):
        ctx = PipelineContext()
        ctx.agent_actions = [{"cmd": "git status"}, {"cmd": "make test"}]

        m = _extract_step_metrics("suggest_actions", ctx)
        assert m["actions_count"] == 2

    def test_build_broadcast_metrics(self):
        ctx = PipelineContext()
        ctx.broadcast_data = {"text": "hi", "cost": 0.01, "tokens": 100}

        m = _extract_step_metrics("build_broadcast", ctx)
        assert m["events_count"] == 3

    def test_unknown_step_returns_empty(self):
        ctx = PipelineContext()
        m = _extract_step_metrics("nonexistent_step", ctx)
        assert m == {}

    def test_crop_windows_metrics(self):
        ctx = PipelineContext()
        org = MagicMock()
        org.total_windows = 5
        ctx.organized_screen = org

        m = _extract_step_metrics("crop_windows", ctx)
        assert m["crops_total"] == 5

    def test_crop_windows_no_screen(self):
        ctx = PipelineContext()
        ctx.organized_screen = None

        m = _extract_step_metrics("crop_windows", ctx)
        assert m["crops_total"] == 0

    def test_exception_in_factory_returns_empty(self):
        """If a factory raises, return {} instead of crashing."""
        ctx = PipelineContext()
        # all_windows is None by default; len(None) raises TypeError
        # but the lambda wraps it with `or []` so it won't crash
        # Let's force an error by setting a bad attribute
        ctx.all_windows = MagicMock()
        ctx.all_windows.__len__ = MagicMock(side_effect=RuntimeError("boom"))

        m = _extract_step_metrics("scan_windows", ctx)
        assert m == {}


# ---------------------------------------------------------------------------
# _analyze_metrics
# ---------------------------------------------------------------------------

class TestAnalyzeMetrics:

    def test_full_analysis_result(self):
        ctx = PipelineContext()
        ctx.analysis_result = {
            "cost": 0.0023,
            "input_tokens": 1200,
            "output_tokens": 350,
            "provider": "google",
            "model": "gemini-2.0-flash",
            "mode": "hybrid",
            "ocr": {
                "engine": "paddleocr",
                "latency_ms": 23.5,
                "text": "Hello World",
            },
        }

        m = _analyze_metrics(ctx)
        assert m["cost_usd"] == pytest.approx(0.0023)
        assert m["tokens_in"] == 1200
        assert m["tokens_out"] == 350
        assert m["provider"] == "google"
        assert m["model"] == "gemini-2.0-flash"
        assert m["mode"] == "hybrid"
        assert m["ocr_engine"] == "paddleocr"
        assert m["ocr_ms"] == 23.5
        assert m["ocr_chars"] == 11

    def test_no_ocr(self):
        ctx = PipelineContext()
        ctx.analysis_result = {
            "cost": 0.001,
            "tokens": 500,
            "output_tokens": 100,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "mode": "vision_only",
        }

        m = _analyze_metrics(ctx)
        assert "ocr_engine" not in m
        assert m["tokens_in"] == 500  # falls back to "tokens"

    def test_no_analysis_result(self):
        ctx = PipelineContext()
        ctx.analysis_result = None

        m = _analyze_metrics(ctx)
        assert m == {}

    def test_cost_none_coerced_to_zero(self):
        ctx = PipelineContext()
        ctx.analysis_result = {"cost": None, "tokens": 0, "output_tokens": 0,
                                "provider": "", "model": "", "mode": ""}
        m = _analyze_metrics(ctx)
        assert m["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# PipelineOrchestrator.run() nfo emission
# ---------------------------------------------------------------------------

class _FakeStep:
    """Minimal step for testing orchestrator instrumentation."""
    def __init__(self, step_name, can=True, fail=False):
        self.name = step_name
        self._can = can
        self._fail = fail

    def can_run(self, ctx):
        return self._can

    async def execute(self, ctx, bus):
        if self._fail:
            raise RuntimeError(f"{self.name} failed")
        return ctx


class TestOrchestratorNfoEmission:

    @pytest.mark.asyncio
    async def test_run_emits_step_entries(self):
        """Each step emits an nfo LogEntry with pipeline_run_id."""
        mock_logger = MagicMock()
        bus = EventBus(enable_store=False)
        orch = PipelineOrchestrator(bus=bus)
        orch.add_step(_FakeStep("step_a"))
        orch.add_step(_FakeStep("step_b"))

        with patch("pipeline.orchestrator._get_nfo_logger", return_value=mock_logger):
            ctx = await orch.run()

        # 2 step entries + 1 completion = 3 emit calls
        assert mock_logger.emit.call_count == 3
        entries = [call[0][0] for call in mock_logger.emit.call_args_list]

        # Step entries
        step_a = entries[0]
        assert step_a.extra["step_name"] == "step_a"
        assert step_a.extra["pipeline_run_id"] == ctx.run_id
        assert step_a.extra["decision"] == "executed"

        step_b = entries[1]
        assert step_b.extra["step_name"] == "step_b"

        # Completion entry
        completion = entries[2]
        assert completion.extra["pipeline_complete"] is True
        assert completion.extra["total_steps"] == 2

    @pytest.mark.asyncio
    async def test_skipped_step_emits_decision(self):
        """Skipped steps emit entries with decision=skipped."""
        mock_logger = MagicMock()
        bus = EventBus(enable_store=False)
        orch = PipelineOrchestrator(bus=bus)
        orch.add_step(_FakeStep("step_skip", can=False))
        orch.add_step(_FakeStep("step_ok"))

        with patch("pipeline.orchestrator._get_nfo_logger", return_value=mock_logger):
            await orch.run()

        entries = [call[0][0] for call in mock_logger.emit.call_args_list]
        skip_entry = entries[0]
        assert skip_entry.extra["step_name"] == "step_skip"
        assert skip_entry.extra["decision"] == "skipped"
        assert skip_entry.extra["decision_reason"] == "can_run=False"

    @pytest.mark.asyncio
    async def test_failed_step_emits_error(self):
        """Failed steps emit ERROR-level entries with exception info."""
        mock_logger = MagicMock()
        bus = EventBus(enable_store=False)
        orch = PipelineOrchestrator(bus=bus)
        orch.add_step(_FakeStep("step_fail", fail=True))

        with patch("pipeline.orchestrator._get_nfo_logger", return_value=mock_logger):
            await orch.run()

        entries = [call[0][0] for call in mock_logger.emit.call_args_list]
        fail_entry = entries[0]
        assert fail_entry.level == "ERROR"
        assert fail_entry.exception == "step_fail failed"
        assert fail_entry.exception_type == "RuntimeError"
        assert fail_entry.extra["step_name"] == "step_fail"

    @pytest.mark.asyncio
    async def test_completion_marker_has_totals(self):
        """Completion entry includes total_ms, total_cost, total_steps, skipped, errors."""
        mock_logger = MagicMock()
        bus = EventBus(enable_store=False)
        orch = PipelineOrchestrator(bus=bus)
        orch.add_step(_FakeStep("a"))
        orch.add_step(_FakeStep("b", can=False))
        orch.add_step(_FakeStep("c", fail=True))

        with patch("pipeline.orchestrator._get_nfo_logger", return_value=mock_logger):
            await orch.run()

        entries = [call[0][0] for call in mock_logger.emit.call_args_list]
        completion = entries[-1]
        assert completion.extra["pipeline_complete"] is True
        assert completion.extra["total_steps"] == 1  # only "a" executed
        assert completion.extra["skipped"] == 1       # "b" skipped
        assert completion.extra["errors"] == 1         # "c" failed
        assert completion.extra["total_ms"] > 0

    @pytest.mark.asyncio
    async def test_all_run_ids_match(self):
        """All entries in a single run share the same pipeline_run_id."""
        mock_logger = MagicMock()
        bus = EventBus(enable_store=False)
        orch = PipelineOrchestrator(bus=bus)
        orch.add_step(_FakeStep("a"))
        orch.add_step(_FakeStep("b"))

        with patch("pipeline.orchestrator._get_nfo_logger", return_value=mock_logger):
            ctx = await orch.run()

        entries = [call[0][0] for call in mock_logger.emit.call_args_list]
        for entry in entries:
            assert entry.extra["pipeline_run_id"] == ctx.run_id
