"""
Tests for OptimizationStrategy — centralized pipeline optimization decisions.
"""
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimization_strategy import (
    OptimizationStrategy, OptimizationDecision, Priority, HardwareProfile,
    create_optimization_strategy, _ema,
)


# ===== Helpers =====

def _make_budget(daily_spent=0.0, daily_limit=5.0, hourly_spent=0.0, hourly_limit=1.0):
    """Create a mock CostBudget with configurable stats."""
    budget = MagicMock()
    budget.get_stats.return_value = {
        "daily_spent": daily_spent,
        "daily_limit": daily_limit,
        "hourly_spent": hourly_spent,
        "hourly_limit": hourly_limit,
    }
    budget.can_spend.return_value = daily_spent < daily_limit
    return budget


def _make_settings(**overrides):
    """Create a mock Settings with defaults + overrides."""
    defaults = {
        "optimization_priority": "auto",
        "hardware_profile": "auto",
        "budget_warning_pct": 80,
        "budget_critical_pct": 95,
        "max_tick_latency_ms": 5000,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_strategy(priority="budget", hardware="cpu_only", daily_spent=0.0, **kw):
    """Shortcut to create a configured strategy."""
    budget = _make_budget(daily_spent=daily_spent, **{k: v for k, v in kw.items() if k in ("daily_limit", "hourly_spent", "hourly_limit")})
    settings = _make_settings(**{k: v for k, v in kw.items() if k not in ("daily_limit", "hourly_spent", "hourly_limit")})
    return OptimizationStrategy(
        priority=Priority(priority),
        hardware=HardwareProfile(hardware),
        cost_budget=budget,
        settings=settings,
    )


# ===== TestOptimizationDecision =====

class TestOptimizationDecision:
    def test_skip_decision_has_zero_cost(self):
        d = OptimizationDecision("none", "skip", "none", True, "test", 0.0, 0)
        assert d.estimated_cost == 0.0
        assert d.estimated_latency_ms == 0

    def test_decision_fields_populated(self):
        d = OptimizationDecision("paddleocr", "hybrid", "fallback", True, "reason", 0.001, 2000)
        assert d.ocr_engine == "paddleocr"
        assert d.analysis_mode == "hybrid"
        assert d.vision_model_tier == "fallback"
        assert d.prefer_local is True
        assert d.reason == "reason"
        assert d.estimated_cost == 0.001
        assert d.estimated_latency_ms == 2000


# ===== TestHardwareDetection =====

class TestHardwareDetection:
    def test_auto_detect_without_torch(self):
        with patch.dict("sys.modules", {"torch": None}):
            # Force reimport check — detect_hardware uses try/except ImportError
            result = OptimizationStrategy.detect_hardware()
            assert result == HardwareProfile.CPU_ONLY

    def test_auto_detect_with_cuda(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_props = MagicMock()
        mock_props.total_mem = 12 * (1024 ** 3)  # 12GB
        mock_torch.cuda.get_device_properties.return_value = mock_props
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = OptimizationStrategy.detect_hardware()
            assert result == HardwareProfile.GPU_HIGH

    def test_auto_detect_low_vram(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_props = MagicMock()
        mock_props.total_mem = 4 * (1024 ** 3)  # 4GB
        mock_torch.cuda.get_device_properties.return_value = mock_props
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = OptimizationStrategy.detect_hardware()
            assert result == HardwareProfile.GPU_LOW

    def test_explicit_override(self):
        s = OptimizationStrategy(Priority.BUDGET, HardwareProfile.GPU_LOW)
        assert s.hardware == HardwareProfile.GPU_LOW


# ===== TestBudgetStrategy =====

class TestBudgetStrategy:
    def test_no_change_returns_skip(self):
        s = _make_strategy("budget", "cpu_only")
        d = s.decide(screen_changed=False)
        assert d.analysis_mode == "skip"
        assert d.estimated_cost == 0.0

    def test_change_with_gpu_uses_paddleocr(self):
        s = _make_strategy("budget", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert d.ocr_engine == "paddleocr"
        assert d.analysis_mode == "hybrid"

    def test_change_without_gpu_uses_tesseract(self):
        s = _make_strategy("budget", "cpu_only")
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert d.ocr_engine == "tesseract"
        assert d.analysis_mode == "hybrid"

    def test_critical_budget_forces_ocr_only(self):
        s = _make_strategy("budget", "cpu_only", daily_spent=4.8, daily_limit=5.0)
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert d.analysis_mode == "ocr_only"
        assert d.vision_model_tier == "none"

    def test_uses_fallback_model_tier(self):
        s = _make_strategy("budget", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert d.vision_model_tier == "fallback"


# ===== TestSpeedStrategy =====

class TestSpeedStrategy:
    def test_no_change_returns_skip(self):
        s = _make_strategy("speed", "gpu_high")
        d = s.decide(screen_changed=False)
        assert d.analysis_mode == "skip"

    def test_gpu_prefers_local_ocr(self):
        s = _make_strategy("speed", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=5)
        assert d.ocr_engine == "paddleocr"
        assert d.prefer_local is True

    def test_no_gpu_uses_cloud_ocr(self):
        s = _make_strategy("speed", "cpu_only")
        d = s.decide(screen_changed=True, change_magnitude=5)
        assert d.ocr_engine == "vlm_ocr"
        assert d.prefer_local is False

    def test_large_change_uses_ocr_plus_vision(self):
        s = _make_strategy("speed", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=20)
        assert d.analysis_mode == "ocr_plus_vision"

    def test_small_change_uses_hybrid(self):
        s = _make_strategy("speed", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=5)
        assert d.analysis_mode == "hybrid"


# ===== TestQualityStrategy =====

class TestQualityStrategy:
    def test_always_ocr_plus_vision(self):
        s = _make_strategy("quality", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=5)
        assert d.analysis_mode == "ocr_plus_vision"

    def test_uses_primary_model(self):
        s = _make_strategy("quality", "gpu_high")
        d = s.decide(screen_changed=True, change_magnitude=5)
        assert d.vision_model_tier == "primary"

    def test_no_change_still_may_analyze(self):
        s = _make_strategy("quality", "gpu_high")
        d = s.decide(screen_changed=False, idle_frames=1)
        assert d.analysis_mode != "skip"

    def test_no_change_skips_after_idle_threshold(self):
        s = _make_strategy("quality", "gpu_high")
        d = s.decide(screen_changed=False, idle_frames=3)
        assert d.analysis_mode == "skip"


# ===== TestAutoStrategy =====

class TestAutoStrategy:
    def test_low_budget_behaves_like_speed(self):
        s = _make_strategy("auto", "gpu_high", daily_spent=1.0, daily_limit=5.0)  # 20%
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert "auto→speed" in d.reason

    def test_high_budget_behaves_like_budget(self):
        s = _make_strategy("auto", "gpu_high", daily_spent=4.5, daily_limit=5.0)  # 90%
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert "auto→budget" in d.reason

    def test_mid_budget_behaves_like_quality_with_fallback(self):
        s = _make_strategy("auto", "gpu_high", daily_spent=3.0, daily_limit=5.0)  # 60%
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert "auto→quality" in d.reason
        assert d.vision_model_tier == "fallback"

    def test_high_latency_triggers_budget_mode(self):
        s = _make_strategy("auto", "gpu_high", daily_spent=1.0, daily_limit=5.0)
        # Feed high latencies
        for _ in range(5):
            s.record_tick(0.001, 6000.0)
        d = s.decide(screen_changed=True, change_magnitude=10)
        assert "auto→budget" in d.reason
        assert "latency" in d.reason


# ===== TestFeedbackLoop =====

class TestFeedbackLoop:
    def test_record_tick_updates_rolling_window(self):
        s = _make_strategy("budget", "cpu_only")
        s.record_tick(0.001, 2000)
        assert len(s._recent_costs) == 1
        assert len(s._recent_latencies) == 1

    def test_rolling_window_bounded(self):
        s = OptimizationStrategy(Priority.BUDGET, HardwareProfile.CPU_ONLY, metrics_window=5)
        for i in range(10):
            s.record_tick(float(i), float(i * 100))
        assert len(s._recent_costs) == 5
        assert len(s._recent_latencies) == 5

    def test_estimate_cost_uses_ema(self):
        s = _make_strategy("budget", "cpu_only")
        for _ in range(5):
            s.record_tick(0.002, 1500, mode="hybrid")
        est = s._estimate_cost("hybrid")
        assert 0.001 < est < 0.003

    def test_estimate_latency_uses_ema(self):
        s = _make_strategy("budget", "cpu_only")
        for _ in range(5):
            s.record_tick(0.001, 1500, mode="hybrid")
        est = s._estimate_latency("hybrid")
        assert 1000 < est < 2000

    def test_ema_function(self):
        assert _ema([]) == 0.0
        assert _ema([10.0]) == 10.0
        result = _ema([1.0, 2.0, 3.0], alpha=0.5)
        assert 2.0 < result < 3.0

    def test_per_mode_tracking(self):
        s = _make_strategy("budget", "cpu_only")
        s.record_tick(0.001, 1000, mode="hybrid")
        s.record_tick(0.005, 5000, mode="ocr_plus_vision")
        assert len(s._mode_costs["hybrid"]) == 1
        assert len(s._mode_costs["ocr_plus_vision"]) == 1


# ===== TestFactory =====

class TestFactory:
    def test_create_from_settings_defaults(self):
        settings = _make_settings(optimization_priority="auto", hardware_profile="cpu_only")
        strategy = create_optimization_strategy(settings=settings)
        assert strategy.priority == Priority.AUTO
        assert strategy.hardware == HardwareProfile.CPU_ONLY

    def test_create_with_explicit_priority(self):
        settings = _make_settings(optimization_priority="budget", hardware_profile="gpu_high")
        strategy = create_optimization_strategy(settings=settings)
        assert strategy.priority == Priority.BUDGET
        assert strategy.hardware == HardwareProfile.GPU_HIGH

    def test_auto_hardware_detection(self):
        settings = _make_settings(optimization_priority="speed", hardware_profile="auto")
        with patch.object(OptimizationStrategy, "detect_hardware", return_value=HardwareProfile.CPU_ONLY):
            strategy = create_optimization_strategy(settings=settings)
            assert strategy.hardware == HardwareProfile.CPU_ONLY

    def test_create_with_cost_budget(self):
        settings = _make_settings(optimization_priority="budget", hardware_profile="cpu_only")
        budget = _make_budget(daily_spent=1.0)
        strategy = create_optimization_strategy(settings=settings, cost_budget=budget)
        assert strategy._budget is budget


# ===== TestGetStats =====

class TestGetStats:
    def test_stats_returns_dict(self):
        s = _make_strategy("budget", "cpu_only")
        stats = s.get_stats()
        assert stats["priority"] == "budget"
        assert stats["hardware"] == "cpu_only"
        assert "avg_latency_ms" in stats
        assert "avg_cost" in stats

    def test_stats_reflects_recorded_ticks(self):
        s = _make_strategy("budget", "cpu_only")
        s.record_tick(0.002, 3000)
        s.record_tick(0.004, 1000)
        stats = s.get_stats()
        assert stats["window_size"] == 2
        assert stats["avg_cost"] == pytest.approx(0.003, abs=0.0001)
        assert stats["avg_latency_ms"] == pytest.approx(2000, abs=1)
