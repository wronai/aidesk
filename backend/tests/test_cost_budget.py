"""Tests for CostBudget and auto-degradation logic."""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cost_budget import CostBudget, BudgetConfig, create_cost_budget_from_env


class TestCostBudget:
    def test_init_defaults(self, tmp_path):
        config = BudgetConfig()
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        assert budget.daily_spent == 0.0
        assert budget.hourly_spent == 0.0

    def test_record_spend(self, tmp_path):
        config = BudgetConfig()
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        
        budget.record_spend(0.5)
        assert budget.daily_spent == 0.5
        assert budget.hourly_spent == 0.5
        
        # Verify persistence
        budget2 = CostBudget(config, state_path=str(tmp_path / "state.json"))
        assert budget2.daily_spent == 0.5

    def test_can_spend(self, tmp_path):
        config = BudgetConfig(daily_limit_usd=1.0)
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        
        assert budget.can_spend(0.5) is True
        budget.record_spend(0.5)
        
        assert budget.can_spend(0.6) is False  # 0.5 + 0.6 > 1.0
        assert budget.can_spend(0.4) is True   # 0.5 + 0.4 <= 1.0

    def test_hourly_reset(self, tmp_path):
        config = BudgetConfig(hourly_limit_usd=1.0)
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        
        # Simulate spend in past hour
        budget.hourly_spent = 0.9
        budget.last_reset_hour = int(time.time() / 3600) - 2  # 2 hours ago
        
        # Should reset on check
        assert budget.can_spend(0.2) is True
        assert budget.hourly_spent == 0.0

    def test_daily_reset(self, tmp_path):
        config = BudgetConfig(daily_limit_usd=1.0)
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        
        budget.daily_spent = 0.9
        budget.last_reset_day = int(time.time() / 86400) - 1  # Yesterday
        
        assert budget.can_spend(0.2) is True
        assert budget.daily_spent == 0.0

    def test_get_suggested_mode(self, tmp_path):
        config = BudgetConfig(daily_limit_usd=1.0, degradation_mode="ocr_only")
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        
        # Under budget
        assert budget.get_suggested_mode("hybrid") == "hybrid"
        
        # Exceed budget
        budget.record_spend(1.1)
        assert budget.get_suggested_mode("hybrid") == "ocr_only"
        
        # Cheap modes allowed
        assert budget.get_suggested_mode("ocr_only") == "ocr_only"

    def test_record_ocr_spend_tracked_separately(self, tmp_path):
        config = BudgetConfig()
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))

        budget.record_spend(0.01, source="ocr")
        budget.record_spend(0.05, source="analysis")
        budget.record_spend(0.02, source="ocr")

        assert budget.ocr_spent == pytest.approx(0.03)
        assert budget.analysis_spent == pytest.approx(0.05)
        assert budget.daily_spent == pytest.approx(0.08)

        stats = budget.get_stats()
        assert stats["ocr_cost_usd"] == pytest.approx(0.03, abs=1e-5)
        assert stats["analysis_cost_usd"] == pytest.approx(0.05, abs=1e-5)

    def test_ocr_spend_persisted(self, tmp_path):
        config = BudgetConfig()
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))
        budget.record_spend(0.007, source="ocr")

        budget2 = CostBudget(config, state_path=str(tmp_path / "state.json"))
        assert budget2.ocr_spent == pytest.approx(0.007)

    def test_budget_blocks_vlm_ocr_after_limit(self, tmp_path):
        """Budget with $0.01/h blocks after enough VLM OCR calls."""
        config = BudgetConfig(hourly_limit_usd=0.01)
        budget = CostBudget(config, state_path=str(tmp_path / "state.json"))

        # Simulate 100 Gemini Flash OCR calls at ~$0.0001 each
        for _ in range(100):
            budget.record_spend(0.0001, source="ocr")

        assert budget.ocr_spent == pytest.approx(0.01)
        assert budget.can_spend(0.0001) is False

    def test_factory(self, monkeypatch):
        # Mock settings
        settings = MagicMock()
        settings.daily_budget = 10.0
        settings.hourly_budget = 2.0
        
        budget = create_cost_budget_from_env(settings=settings)
        assert budget.config.daily_limit_usd == 10.0
        assert budget.config.hourly_limit_usd == 2.0
