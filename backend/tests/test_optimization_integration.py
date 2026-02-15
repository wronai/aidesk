"""
Integration tests — OptimizationStrategy × Pipeline × AnalyzeStep.

Tests end-to-end flows: strategy decision → profile selection → analysis mode → feedback loop.
"""
import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimization_strategy import (
    OptimizationStrategy, OptimizationDecision, Priority, HardwareProfile,
    create_optimization_strategy,
)
from pipeline.context import PipelineContext, PipelineProfile, ProfileSelector
from pipeline.orchestrator import (
    PipelineOrchestrator, create_pipeline, create_profile_selector,
)
from pipeline.steps_core import AnalyzeStep
from event_bus import EventBus, create_event_bus


# ===== Helpers =====

def _make_budget(daily_spent=0.0, daily_limit=5.0):
    budget = MagicMock()
    budget.get_stats.return_value = {
        "daily_spent": daily_spent,
        "daily_limit": daily_limit,
        "hourly_spent": 0.0,
        "hourly_limit": 1.0,
    }
    budget.can_spend.return_value = daily_spent < daily_limit
    budget.get_suggested_mode.return_value = "hybrid"
    budget.record_spend = MagicMock()
    return budget


def _make_settings(**overrides):
    defaults = {
        "optimization_priority": "auto",
        "hardware_profile": "cpu_only",
        "budget_warning_pct": 80,
        "budget_critical_pct": 95,
        "max_tick_latency_ms": 5000,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_analyzer(mode="hybrid", cost=0.002):
    analyzer = MagicMock()
    analyzer.analysis_mode = mode
    analyzer.set_mode = MagicMock(return_value=True)
    analyzer.analyze = AsyncMock(return_value={
        "text": "test analysis",
        "cost": cost,
        "tokens": 500,
        "provider": "test",
        "model": "test-model",
        "mode": mode,
    })
    return analyzer


def _make_bus():
    bus = MagicMock(spec=EventBus)
    bus.publish = AsyncMock()
    return bus


def _make_strategy(priority="budget", hardware="cpu_only", daily_spent=0.0):
    budget = _make_budget(daily_spent=daily_spent)
    settings = _make_settings()
    return OptimizationStrategy(
        priority=Priority(priority),
        hardware=HardwareProfile(hardware),
        cost_budget=budget,
        settings=settings,
    )


# ===== TestPipelineWithStrategy =====

class TestPipelineWithStrategy:
    """Test pipeline orchestrator with and without OptimizationStrategy."""

    @pytest.mark.asyncio
    async def test_pipeline_runs_without_strategy(self):
        """Backward compat: pipeline works fine with no strategy."""
        bus = _make_bus()
        pipeline = PipelineOrchestrator(bus)
        ctx = PipelineContext()
        result = await pipeline.run(ctx)
        assert result is not None
        assert result.optimization_decision is None

    @pytest.mark.asyncio
    async def test_pipeline_runs_with_budget_strategy(self):
        """Pipeline with budget strategy records ticks."""
        strategy = _make_strategy("budget", "cpu_only")
        bus = _make_bus()
        pipeline = PipelineOrchestrator(bus, optimization_strategy=strategy)

        ctx = PipelineContext()
        ctx.optimization_decision = strategy.decide(True, 10.0)
        ctx.actual_cost = 0.001
        result = await pipeline.run(ctx)

        assert len(strategy._recent_costs) == 1
        assert strategy._recent_costs[0] == 0.001

    @pytest.mark.asyncio
    async def test_pipeline_runs_with_speed_strategy(self):
        """Speed strategy produces valid decisions."""
        strategy = _make_strategy("speed", "gpu_high")
        bus = _make_bus()
        pipeline = PipelineOrchestrator(bus, optimization_strategy=strategy)

        ctx = PipelineContext()
        ctx.optimization_decision = strategy.decide(True, 20.0)
        ctx.actual_cost = 0.003

        result = await pipeline.run(ctx)
        assert result.optimization_decision.analysis_mode == "ocr_plus_vision"

    @pytest.mark.asyncio
    async def test_pipeline_skips_analysis_when_no_change(self):
        """Strategy skip decision should be respected."""
        strategy = _make_strategy("budget", "cpu_only")
        decision = strategy.decide(screen_changed=False)
        assert decision.analysis_mode == "skip"

    @pytest.mark.asyncio
    async def test_strategy_downgrades_on_budget_critical(self):
        """Critical budget → ocr_only, no LLM."""
        strategy = _make_strategy("budget", "cpu_only", daily_spent=4.8)
        decision = strategy.decide(screen_changed=True, change_magnitude=10)
        assert decision.analysis_mode == "ocr_only"
        assert decision.vision_model_tier == "none"

    @pytest.mark.asyncio
    async def test_record_tick_called_after_pipeline_complete(self):
        """Verify record_tick is called during pipeline completion."""
        strategy = _make_strategy("budget", "cpu_only")
        bus = _make_bus()
        pipeline = PipelineOrchestrator(bus, optimization_strategy=strategy)

        ctx = PipelineContext()
        ctx.optimization_decision = OptimizationDecision(
            "tesseract", "hybrid", "fallback", True, "test", 0.001, 2000
        )
        ctx.actual_cost = 0.002

        await pipeline.run(ctx)
        assert len(strategy._recent_costs) == 1

    @pytest.mark.asyncio
    async def test_feedback_loop_affects_next_decision(self):
        """After recording ticks, EMA estimates should change."""
        strategy = _make_strategy("budget", "cpu_only")
        for _ in range(5):
            strategy.record_tick(0.005, 3000.0, mode="hybrid")

        est_before = strategy._estimate_cost("hybrid")
        strategy.record_tick(0.001, 1000.0, mode="hybrid")
        est_after = strategy._estimate_cost("hybrid")

        # After recording a low cost, EMA should decrease
        assert est_after < est_before


# ===== TestE2EOptimization =====

class TestE2EOptimization:
    """End-to-end optimization behavior tests."""

    def test_budget_mode_uses_hybrid_not_vision(self):
        strategy = _make_strategy("budget", "gpu_high")
        d = strategy.decide(screen_changed=True, change_magnitude=10)
        assert d.analysis_mode == "hybrid"
        assert d.analysis_mode != "vision_only"

    def test_speed_mode_with_gpu_uses_local_ocr(self):
        strategy = _make_strategy("speed", "gpu_high")
        d = strategy.decide(screen_changed=True, change_magnitude=10)
        assert d.ocr_engine == "paddleocr"
        assert d.prefer_local is True

    def test_auto_mode_adapts_to_budget_state(self):
        # Low spend → speed-like
        s_low = _make_strategy("auto", "gpu_high", daily_spent=0.5)
        d_low = s_low.decide(True, 10)
        assert "speed" in d_low.reason

        # High spend → budget-like
        s_high = _make_strategy("auto", "gpu_high", daily_spent=4.5)
        d_high = s_high.decide(True, 10)
        assert "budget" in d_high.reason

    def test_fallback_chain_on_budget_exhaustion(self):
        """Critical budget forces ocr_only (end of fallback chain)."""
        strategy = _make_strategy("budget", "cpu_only", daily_spent=4.9)
        d = strategy.decide(screen_changed=True, change_magnitude=20)
        assert d.analysis_mode == "ocr_only"
        assert d.vision_model_tier == "none"


# ===== TestModelTierSwitching =====

class TestModelTierSwitching:
    def test_primary_model_used_when_budget_ok(self):
        strategy = _make_strategy("speed", "gpu_high", daily_spent=0.5)
        d = strategy.decide(True, 10)
        assert d.vision_model_tier == "primary"

    def test_fallback_model_on_budget_warning(self):
        strategy = _make_strategy("budget", "gpu_high", daily_spent=2.0)
        d = strategy.decide(True, 10)
        assert d.vision_model_tier == "fallback"

    def test_emergency_model_on_critical(self):
        """Critical budget → no model (ocr_only)."""
        strategy = _make_strategy("budget", "cpu_only", daily_spent=4.8)
        d = strategy.decide(True, 10)
        assert d.vision_model_tier == "none"


# ===== TestAnalyzeStepWithDecision =====

class TestAnalyzeStepWithDecision:
    """Test AnalyzeStep behavior when OptimizationDecision is on context."""

    @pytest.mark.asyncio
    async def test_analyze_skips_on_skip_decision(self):
        analyzer = _make_analyzer()
        step = AnalyzeStep(analyzer)
        bus = _make_bus()

        ctx = PipelineContext(image_b64="dGVzdA==")
        ctx.optimization_decision = OptimizationDecision(
            "none", "skip", "none", True, "no_change", 0.0, 0
        )

        result = await step.execute(ctx, bus)
        analyzer.analyze.assert_not_called()
        assert result.analysis_result is None

    @pytest.mark.asyncio
    async def test_analyze_uses_decision_mode(self):
        analyzer = _make_analyzer(mode="hybrid")
        step = AnalyzeStep(analyzer)
        bus = _make_bus()

        ctx = PipelineContext(image_b64="dGVzdA==", full_context="test")
        ctx.optimization_decision = OptimizationDecision(
            "paddleocr", "ocr_plus_vision", "primary", True, "quality", 0.003, 5000
        )

        result = await step.execute(ctx, bus)
        # Analyzer.set_mode should have been called with the decision mode
        # (note: _restore_mode may call set_mode again afterward)
        analyzer.set_mode.assert_any_call("ocr_plus_vision")
        assert result.actual_cost >= 0

    @pytest.mark.asyncio
    async def test_analyze_records_actual_cost(self):
        analyzer = _make_analyzer(cost=0.005)
        step = AnalyzeStep(analyzer)
        bus = _make_bus()

        ctx = PipelineContext(image_b64="dGVzdA==", full_context="test")
        ctx.optimization_decision = OptimizationDecision(
            "paddleocr", "hybrid", "fallback", True, "budget", 0.001, 2000
        )

        result = await step.execute(ctx, bus)
        assert result.actual_cost == 0.005
        assert result.actual_latency_ms >= 0  # may be 0 for instant mock

    @pytest.mark.asyncio
    async def test_analyze_legacy_path_without_decision(self):
        """Without decision, existing _apply_budget_downgrade path is used."""
        analyzer = _make_analyzer()
        budget = _make_budget()
        step = AnalyzeStep(analyzer, cost_budget=budget)
        bus = _make_bus()

        ctx = PipelineContext(image_b64="dGVzdA==", full_context="test")
        # No optimization_decision set → legacy path

        result = await step.execute(ctx, bus)
        assert result.analysis_result is not None
        budget.get_suggested_mode.assert_called_once()


# ===== TestProfileSelectorWithStrategy =====

class TestProfileSelectorWithStrategy:
    """Test ProfileSelector integration with OptimizationStrategy."""

    def test_selector_without_strategy_uses_heuristic(self):
        selector = ProfileSelector(full_interval=60.0)
        ctx = PipelineContext()
        profile = selector.select(ctx)
        # First tick → FULL (periodic scan)
        assert profile == PipelineProfile.FULL

    def test_selector_with_strategy_uses_decision(self):
        strategy = _make_strategy("budget", "cpu_only")
        selector = ProfileSelector(full_interval=60.0, optimization_strategy=strategy)
        ctx = PipelineContext()

        # Screen changed → budget strategy → hybrid → NORMAL
        capture = MagicMock()
        capture.consecutive_unchanged = 0
        capture.last_change_magnitude = 10.0

        # First call triggers periodic full scan, second uses strategy
        _ = selector.select(ctx, capture)  # first tick = FULL
        profile = selector.select(ctx, capture)
        assert profile in (PipelineProfile.NORMAL, PipelineProfile.FULL)

    def test_selector_skip_decision_returns_fast(self):
        strategy = _make_strategy("budget", "cpu_only")
        selector = ProfileSelector(full_interval=60.0, optimization_strategy=strategy)
        # Force past the first periodic scan
        selector._last_full_time = 9999999999.0

        ctx = PipelineContext()
        capture = MagicMock()
        capture.consecutive_unchanged = 50  # idle → no change
        capture.last_change_magnitude = 0.0

        profile = selector.select(ctx, capture)
        assert profile == PipelineProfile.FAST
        assert ctx.optimization_decision is not None
        assert ctx.optimization_decision.analysis_mode == "skip"

    def test_factory_passes_strategy(self):
        strategy = _make_strategy("speed", "gpu_high")
        selector = create_profile_selector(optimization_strategy=strategy)
        assert selector._strategy is strategy

    def test_factory_without_strategy(self):
        selector = create_profile_selector()
        assert selector._strategy is None
