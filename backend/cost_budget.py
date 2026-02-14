"""
Cost Budget & Auto-Degradation — Prevents runaway API bills.

Tracks daily and hourly spend. If limits are exceeded, forces the pipeline
into a cheaper mode (HYBRID → OCR_ONLY) until budget resets.
"""
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

import nfo
import structlog

from event_bus import EventBus, EventType

logger = structlog.get_logger()


@dataclass
class BudgetConfig:
    daily_limit_usd: float = 5.0
    hourly_limit_usd: float = 1.0
    degradation_mode: str = "ocr_only"  # fallback mode when budget exceeded


class CostBudget:
    """
    Tracks API spend and enforces budget limits.
    
    Persists state to disk to survive restarts.
    """

    def __init__(self, config: BudgetConfig, state_path: str = "logs/cost_state.json"):
        self.config = config
        self.state_path = state_path
        self.daily_spent = 0.0
        self.hourly_spent = 0.0
        self.last_reset_day = 0
        self.last_reset_hour = 0
        
        # Load state if exists
        self._load_state()

    def check_and_reset(self):
        """Reset counters if new day/hour started."""
        now = time.time()
        # Day reset (UTC)
        current_day = int(now / 86400)
        if current_day > self.last_reset_day:
            self.daily_spent = 0.0
            self.last_reset_day = current_day
            self._save_state()
            logger.info("Daily budget reset")

        # Hour reset
        current_hour = int(now / 3600)
        if current_hour > self.last_reset_hour:
            self.hourly_spent = 0.0
            self.last_reset_hour = current_hour
            self._save_state()
            logger.debug("Hourly budget reset")

    def can_spend(self, estimated_cost: float = 0.0) -> bool:
        """Check if we can afford the next call."""
        self.check_and_reset()
        
        if self.daily_spent + estimated_cost > self.config.daily_limit_usd:
            logger.warning(
                "Daily budget exceeded",
                spent=self.daily_spent,
                limit=self.config.daily_limit_usd
            )
            return False
            
        if self.hourly_spent + estimated_cost > self.config.hourly_limit_usd:
            logger.warning(
                "Hourly budget exceeded",
                spent=self.hourly_spent,
                limit=self.config.hourly_limit_usd
            )
            return False
            
        return True

    def record_spend(self, cost: float):
        """Record actual spend after API call."""
        if cost <= 0:
            return
            
        self.check_and_reset()
        self.daily_spent += cost
        self.hourly_spent += cost
        self._save_state()

    def get_suggested_mode(self, requested_mode: str) -> str:
        """
        Return safe analysis mode based on budget.
        
        If budget exceeded, downgrades to configured degradation mode (e.g. ocr_only).
        """
        if self.can_spend():
            return requested_mode
        
        # Budget exceeded — downgrade if current mode uses LLM
        if requested_mode in ("vision_only", "hybrid", "ocr_plus_vision"):
            return self.config.degradation_mode
            
        return requested_mode

    def _load_state(self):
        try:
            path = Path(self.state_path)
            if path.exists():
                data = json.loads(path.read_text())
                self.daily_spent = data.get("daily_spent", 0.0)
                self.hourly_spent = data.get("hourly_spent", 0.0)
                self.last_reset_day = data.get("last_reset_day", 0)
                self.last_reset_hour = data.get("last_reset_hour", 0)
        except Exception as e:
            logger.warning("Failed to load cost state", error=str(e))

    def _save_state(self):
        try:
            path = Path(self.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "daily_spent": self.daily_spent,
                "hourly_spent": self.hourly_spent,
                "last_reset_day": self.last_reset_day,
                "last_reset_hour": self.last_reset_hour,
                "updated": time.time(),
            }
            path.write_text(json.dumps(data))
        except Exception as e:
            logger.warning("Failed to save cost state", error=str(e))

    def get_stats(self) -> Dict:
        return {
            "daily_spent": round(self.daily_spent, 4),
            "daily_limit": self.config.daily_limit_usd,
            "daily_remaining": round(max(0, self.config.daily_limit_usd - self.daily_spent), 4),
            "hourly_spent": round(self.hourly_spent, 4),
            "hourly_limit": self.config.hourly_limit_usd,
            "status": "ok" if self.can_spend() else "exceeded",
        }


@nfo.log_call(level="INFO")
def create_cost_budget_from_env(settings=None) -> CostBudget:
    """Create CostBudget from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()
    
    config = BudgetConfig(
        daily_limit_usd=settings.daily_budget,
        hourly_limit_usd=settings.hourly_budget,
        degradation_mode="ocr_only",
    )
    return CostBudget(config)
