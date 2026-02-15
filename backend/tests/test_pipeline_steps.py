import pytest
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import AnalyzeStep, PipelineContext
from event_bus import EventBus, EventType

class TestAnalyzeStepWithBudget:
    @pytest.mark.asyncio
    async def test_budget_check_downgrades_mode(self):
        # Setup
        analyzer = MagicMock()
        analyzer.analysis_mode = "hybrid"
        analyzer.analyze = AsyncMock(return_value={
            "text": "analysis",
            "cost": 0.05,
            "tokens": 100,
            "mode": "ocr_only",
            "model": "mock-model",
        })

        budget = MagicMock()
        budget.get_suggested_mode.return_value = "ocr_only" # Suggest downgrade

        step = AnalyzeStep(analyzer, cost_budget=budget)
        ctx = PipelineContext()
        ctx.image_b64 = "base64"
        bus = EventBus(enable_store=False)
        
        # Execute
        await step.execute(ctx, bus)
        
        # Verify
        budget.get_suggested_mode.assert_called_with("hybrid")
        analyzer.set_mode.assert_any_call("ocr_only")
        analyzer.set_mode.assert_any_call("hybrid")
        assert analyzer.set_mode.call_count == 2
        budget.record_spend.assert_called_with(0.05, source="analysis")
        assert ctx.analysis_result["mode"] == "ocr_only"

    @pytest.mark.asyncio
    async def test_budget_ok_keeps_mode(self):
        # Setup
        analyzer = MagicMock()
        analyzer.analysis_mode = "hybrid"
        analyzer.analyze = AsyncMock(return_value={
            "text": "analysis",
            "cost": 0.05,
            "tokens": 100,
            "mode": "hybrid",
        })
        
        budget = MagicMock()
        budget.get_suggested_mode.return_value = "hybrid" # No change
        
        step = AnalyzeStep(analyzer, cost_budget=budget)
        ctx = PipelineContext()
        ctx.image_b64 = "base64"
        bus = EventBus(enable_store=False)
        
        # Execute
        await step.execute(ctx, bus)
        
        # Verify
        budget.get_suggested_mode.assert_called_with("hybrid")
        analyzer.set_mode.assert_not_called()
        budget.record_spend.assert_called_with(0.05, source="analysis")

    @pytest.mark.asyncio
    async def test_no_budget_component(self):
        # Setup
        analyzer = MagicMock()
        analyzer.analyze = AsyncMock(return_value={"text": "analysis", "cost": 0.05, "mode": "hybrid"})
        
        step = AnalyzeStep(analyzer, cost_budget=None)
        ctx = PipelineContext()
        ctx.image_b64 = "base64"
        bus = EventBus(enable_store=False)
        
        # Execute
        await step.execute(ctx, bus)
        
        # Verify execution passed
        analyzer.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_budget_downgrade_is_per_run_not_persistent(self):
        analyzer = MagicMock()
        analyzer.analysis_mode = "hybrid"
        analyzer.analyze = AsyncMock(return_value={"text": "analysis", "cost": 0.01, "mode": "ocr_only"})

        budget = MagicMock()
        budget.get_suggested_mode.return_value = "ocr_only"

        step = AnalyzeStep(analyzer, cost_budget=budget)
        ctx = PipelineContext(image_b64="base64")
        bus = EventBus(enable_store=False)

        await step.execute(ctx, bus)

        # Temporary switch to cheap mode, then restoration back to requested mode.
        assert analyzer.set_mode.call_args_list[0].args == ("ocr_only",)
        assert analyzer.set_mode.call_args_list[-1].args == ("hybrid",)

    @pytest.mark.asyncio
    async def test_ocr_failure_does_not_block_analysis(self):
        """OCR failure should not prevent LLM analysis from completing (graceful degradation)."""
        analyzer = MagicMock()
        analyzer.analysis_mode = "hybrid"
        # Analysis returns result even when OCR failed internally
        analyzer.analyze = AsyncMock(return_value={
            "text": "analysis without OCR",
            "cost": 0.02,
            "tokens": 50,
            "mode": "hybrid",
            "ocr": {"text": "", "engine": "paddleocr", "confidence": 0.0},
        })

        step = AnalyzeStep(analyzer, cost_budget=None)
        ctx = PipelineContext(image_b64="base64")
        bus = EventBus(enable_store=False)

        result = await step.execute(ctx, bus)
        assert result.analysis_result is not None
        assert result.analysis_result["text"] == "analysis without OCR"
        assert result.analysis_failed is not True

    @pytest.mark.asyncio
    async def test_analysis_completed_event_contains_budget_metadata(self):
        analyzer = MagicMock()
        analyzer.analysis_mode = "hybrid"
        analyzer.analyze = AsyncMock(return_value={
            "text": "analysis",
            "cost": 0.02,
            "tokens": 12,
            "provider": "mock",
            "model": "mock-model",
            "mode": "ocr_only",
        })

        budget = MagicMock()
        budget.get_suggested_mode.return_value = "ocr_only"

        step = AnalyzeStep(analyzer, cost_budget=budget)
        ctx = PipelineContext(image_b64="base64")
        bus = EventBus(enable_store=False)
        received = []

        async def _on_analysis(event):
            received.append(event)

        bus.subscribe(EventType.ANALYSIS_COMPLETED.value, _on_analysis)
        await step.execute(ctx, bus)

        assert len(received) == 1
        payload = received[0].data
        assert payload["requested_mode"] == "hybrid"
        assert payload["effective_mode"] == "ocr_only"
        assert payload["budget_degraded"] is True
        assert payload["model"] == "mock-model"
        assert payload["latency_ms"] >= 0
