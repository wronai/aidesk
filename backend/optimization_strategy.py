"""
OptimizationStrategy — centralized decision engine for pipeline optimization.

Replaces scattered decision logic in AnalyzeStep, ProfileSelector, and CostBudget
with a single decide() call per tick that returns a complete OptimizationDecision.

Supports four priority modes:
  - BUDGET:  minimize API costs (local OCR, hybrid mode, fallback models)
  - SPEED:   minimize latency (GPU OCR, parallel processing, primary models)
  - QUALITY: maximize accuracy (ocr_plus_vision, primary models, lower skip threshold)
  - AUTO:    adaptive switching based on rolling metrics and budget state

Zero new dependencies — uses only Python stdlib.
"""
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

import nfo
import structlog

logger = structlog.get_logger()


# ===== Enums =====

class Priority(Enum):
    """Optimization priority mode."""
    BUDGET = "budget"
    SPEED = "speed"
    QUALITY = "quality"
    AUTO = "auto"


class HardwareProfile(Enum):
    """Detected or configured hardware capability."""
    GPU_HIGH = "gpu_high"   # ≥8GB VRAM (RTX 3060+, A4000+)
    GPU_LOW = "gpu_low"     # <8GB VRAM (GTX 1660, MX450)
    CPU_ONLY = "cpu_only"   # No CUDA/ROCm GPU
    AUTO = "auto"           # Detect at runtime


# ===== Decision dataclass =====

@dataclass
class OptimizationDecision:
    """Result of a single optimization decision for one pipeline tick."""
    ocr_engine: str           # paddleocr | vlm_ocr | tesseract | none
    analysis_mode: str        # vision_only | hybrid | ocr_only | ocr_plus_vision | skip
    vision_model_tier: str    # primary | fallback | emergency | none
    prefer_local: bool        # True = prefer local OCR/LLM
    reason: str               # Human-readable decision reason
    estimated_cost: float     # Predicted cost for this tick ($)
    estimated_latency_ms: int # Predicted latency for this tick (ms)


# ===== EMA helper =====

def _ema(values: List[float], alpha: float = 0.3) -> float:
    """Exponential Moving Average over a list of values."""
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


# ===== Main class =====

class OptimizationStrategy:
    """
    Centralized optimization decision engine.

    Called once per pipeline tick to produce an OptimizationDecision that
    determines OCR engine, analysis mode, vision model tier, and local
    preference. Uses a rolling window of recent costs and latencies for
    adaptive behavior in AUTO mode.
    """

    # Dispatch table: Priority → strategy method name
    _STRATEGY_DISPATCH = {
        Priority.BUDGET: "_budget_strategy",
        Priority.SPEED: "_speed_strategy",
        Priority.QUALITY: "_quality_strategy",
        Priority.AUTO: "_auto_strategy",
    }

    def __init__(
        self,
        priority: Priority,
        hardware: HardwareProfile,
        cost_budget: Any = None,
        settings: Any = None,
        metrics_window: int = 20,
    ):
        """
        Initialize strategy.

        Args:
            priority: Optimization priority mode.
            hardware: Hardware profile (detected or forced).
            cost_budget: CostBudget instance for budget state queries.
            settings: Settings instance for threshold configuration.
            metrics_window: Rolling window size for cost/latency tracking.
        """
        self.priority = priority
        self.hardware = hardware
        self._budget = cost_budget
        self._settings = settings
        self._max_window = metrics_window
        self._recent_costs: Deque[float] = deque(maxlen=metrics_window)
        self._recent_latencies: Deque[float] = deque(maxlen=metrics_window)
        # Per-mode EMA trackers
        self._mode_costs: Dict[str, List[float]] = {}
        self._mode_latencies: Dict[str, List[float]] = {}

    def decide(
        self,
        screen_changed: bool,
        change_magnitude: float = 0.0,
        idle_frames: int = 0,
    ) -> OptimizationDecision:
        """
        Main decision method — called once per pipeline tick.

        Args:
            screen_changed: Whether screen content changed since last tick.
            change_magnitude: Magnitude of screen change (0-100 scale).
            idle_frames: Number of consecutive unchanged frames.

        Returns:
            OptimizationDecision with all parameters for this tick.
        """
        method_name = self._STRATEGY_DISPATCH[self.priority]
        strategy_fn = getattr(self, method_name)
        return strategy_fn(screen_changed, change_magnitude, idle_frames)

    def record_tick(self, actual_cost: float, actual_latency_ms: float,
                    mode: str = "") -> None:
        """
        Feedback loop — record actual cost and latency after tick completes.

        Updates rolling windows and per-mode EMA trackers for future predictions.

        Args:
            actual_cost: Actual cost in USD for this tick.
            actual_latency_ms: Actual latency in ms for this tick.
            mode: Analysis mode used (for per-mode EMA tracking).
        """
        self._recent_costs.append(actual_cost)
        self._recent_latencies.append(actual_latency_ms)

        if mode:
            self._mode_costs.setdefault(mode, [])
            self._mode_latencies.setdefault(mode, [])
            self._mode_costs[mode].append(actual_cost)
            self._mode_latencies[mode].append(actual_latency_ms)
            # Keep per-mode lists bounded
            if len(self._mode_costs[mode]) > self._max_window:
                self._mode_costs[mode] = self._mode_costs[mode][-self._max_window:]
                self._mode_latencies[mode] = self._mode_latencies[mode][-self._max_window:]

    # ── Strategy implementations ────────────────────────────────────────

    def _budget_strategy(
        self, changed: bool, magnitude: float, idle_frames: int,
    ) -> OptimizationDecision:
        """Minimize costs: local OCR, hybrid mode, fallback model."""
        if not changed:
            return self._skip_decision("no_change+budget")

        budget_pct = self._budget_usage_pct()

        # Critical budget → OCR only, no LLM
        critical_pct = self._get_critical_pct()
        if budget_pct > critical_pct:
            return OptimizationDecision(
                ocr_engine=self._local_ocr_engine(),
                analysis_mode="ocr_only",
                vision_model_tier="none",
                prefer_local=True,
                reason=f"budget_critical ({budget_pct:.0f}%>{critical_pct}%)",
                estimated_cost=0.0,
                estimated_latency_ms=self._estimate_latency("ocr_only", self._local_ocr_engine()),
            )

        # Normal budget mode: hybrid + fallback model
        ocr = self._local_ocr_engine()
        return OptimizationDecision(
            ocr_engine=ocr,
            analysis_mode="hybrid",
            vision_model_tier="fallback",
            prefer_local=True,
            reason=f"budget_mode ({budget_pct:.0f}%)",
            estimated_cost=self._estimate_cost("hybrid"),
            estimated_latency_ms=self._estimate_latency("hybrid", ocr),
        )

    def _speed_strategy(
        self, changed: bool, magnitude: float, idle_frames: int,
    ) -> OptimizationDecision:
        """Minimize latency: GPU OCR, primary model, parallel processing."""
        if not changed:
            return self._skip_decision("no_change+speed")

        use_local = self.hardware in (HardwareProfile.GPU_HIGH, HardwareProfile.GPU_LOW)
        ocr = "paddleocr" if use_local else "vlm_ocr"

        # Large change → full analysis, small → hybrid
        mode = "ocr_plus_vision" if magnitude > 15 else "hybrid"

        return OptimizationDecision(
            ocr_engine=ocr,
            analysis_mode=mode,
            vision_model_tier="primary",
            prefer_local=use_local,
            reason=f"speed_mode (delta={magnitude:.0f})",
            estimated_cost=self._estimate_cost(mode),
            estimated_latency_ms=self._estimate_latency(mode, ocr),
        )

    def _quality_strategy(
        self, changed: bool, magnitude: float, idle_frames: int,
    ) -> OptimizationDecision:
        """Maximize accuracy: ocr_plus_vision, primary model, lower skip threshold."""
        # Quality mode has a lower skip threshold — only skip after 3+ idle frames
        if not changed and idle_frames >= 3:
            return self._skip_decision("no_change+quality (idle>=3)")

        if not changed:
            # Still analyze even without change if recently active
            pass

        use_local = self.hardware in (HardwareProfile.GPU_HIGH, HardwareProfile.GPU_LOW)
        ocr = "paddleocr" if use_local else "vlm_ocr"

        return OptimizationDecision(
            ocr_engine=ocr,
            analysis_mode="ocr_plus_vision",
            vision_model_tier="primary",
            prefer_local=use_local,
            reason=f"quality_mode (delta={magnitude:.0f}, idle={idle_frames})",
            estimated_cost=self._estimate_cost("ocr_plus_vision"),
            estimated_latency_ms=self._estimate_latency("ocr_plus_vision", ocr),
        )

    def _auto_strategy(
        self, changed: bool, magnitude: float, idle_frames: int,
    ) -> OptimizationDecision:
        """Adaptive: switches behavior based on budget state and recent metrics."""
        budget_pct = self._budget_usage_pct()
        avg_latency = self._avg_latency()
        max_latency = self._get_max_tick_latency()

        # High budget usage → delegate to budget strategy
        warning_pct = self._get_warning_pct()
        if budget_pct > warning_pct:
            decision = self._budget_strategy(changed, magnitude, idle_frames)
            decision.reason = f"auto→budget ({budget_pct:.0f}%>{warning_pct}%)"
            return decision

        # Recent latency too high → downgrade mode
        if avg_latency > max_latency and avg_latency > 0:
            decision = self._budget_strategy(changed, magnitude, idle_frames)
            decision.reason = f"auto→budget (avg_latency={avg_latency:.0f}ms>{max_latency}ms)"
            return decision

        # Low budget usage → quality with fallback model for cost control
        if budget_pct < 50:
            decision = self._speed_strategy(changed, magnitude, idle_frames)
            decision.reason = f"auto→speed ({budget_pct:.0f}%<50%)"
            return decision

        # Mid-range: quality behavior with fallback model
        decision = self._quality_strategy(changed, magnitude, idle_frames)
        decision.vision_model_tier = "fallback"
        decision.reason = f"auto→quality+fallback ({budget_pct:.0f}%)"
        return decision

    # ── Helper methods ──────────────────────────────────────────────────

    def _skip_decision(self, reason: str) -> OptimizationDecision:
        """Return a no-op skip decision."""
        return OptimizationDecision(
            ocr_engine="none",
            analysis_mode="skip",
            vision_model_tier="none",
            prefer_local=True,
            reason=reason,
            estimated_cost=0.0,
            estimated_latency_ms=0,
        )

    def _local_ocr_engine(self) -> str:
        """Choose local OCR engine based on hardware profile."""
        if self.hardware in (HardwareProfile.GPU_HIGH, HardwareProfile.GPU_LOW):
            return "paddleocr"
        return "tesseract"

    def _budget_usage_pct(self) -> float:
        """Return current budget usage as percentage (0-100)."""
        if not self._budget:
            return 0.0
        try:
            stats = self._budget.get_stats()
            daily_limit = stats.get("daily_limit", 0)
            if daily_limit <= 0:
                return 0.0
            return (stats.get("daily_spent", 0) / daily_limit) * 100
        except Exception:
            return 0.0

    def _avg_latency(self) -> float:
        """Average latency from rolling window (ms)."""
        if not self._recent_latencies:
            return 0.0
        return sum(self._recent_latencies) / len(self._recent_latencies)

    def _avg_cost(self) -> float:
        """Average cost from rolling window ($)."""
        if not self._recent_costs:
            return 0.0
        return sum(self._recent_costs) / len(self._recent_costs)

    def _estimate_cost(self, mode: str) -> float:
        """Estimate cost for a given mode using EMA of historical costs."""
        values = self._mode_costs.get(mode, [])
        if values:
            return round(_ema(values), 6)
        # Default estimates when no history
        defaults = {
            "skip": 0.0,
            "ocr_only": 0.0,
            "hybrid": 0.001,
            "ocr_plus_vision": 0.003,
            "vision_only": 0.005,
        }
        return defaults.get(mode, 0.001)

    def _estimate_latency(self, mode: str, ocr_engine: str = "") -> int:
        """Estimate latency for a given mode+engine using EMA of historical latencies."""
        values = self._mode_latencies.get(mode, [])
        if values:
            return round(_ema(values))
        # Default estimates when no history
        defaults = {
            "skip": 0,
            "ocr_only": 200 if ocr_engine == "paddleocr" else 500,
            "hybrid": 2000,
            "ocr_plus_vision": 5000,
            "vision_only": 8000,
        }
        return defaults.get(mode, 2000)

    def _get_warning_pct(self) -> float:
        """Get budget warning percentage from settings or default."""
        if self._settings and hasattr(self._settings, "budget_warning_pct"):
            return float(self._settings.budget_warning_pct)
        return 80.0

    def _get_critical_pct(self) -> float:
        """Get budget critical percentage from settings or default."""
        if self._settings and hasattr(self._settings, "budget_critical_pct"):
            return float(self._settings.budget_critical_pct)
        return 95.0

    def _get_max_tick_latency(self) -> float:
        """Get max tick latency from settings or default."""
        if self._settings and hasattr(self._settings, "max_tick_latency_ms"):
            return float(self._settings.max_tick_latency_ms)
        return 5000.0

    def get_stats(self) -> Dict:
        """Return strategy state for diagnostics."""
        return {
            "priority": self.priority.value,
            "hardware": self.hardware.value,
            "budget_pct": round(self._budget_usage_pct(), 1),
            "avg_latency_ms": round(self._avg_latency(), 1),
            "avg_cost": round(self._avg_cost(), 6),
            "window_size": len(self._recent_costs),
            "max_window": self._max_window,
        }

    # ── Hardware detection ──────────────────────────────────────────────

    @classmethod
    def detect_hardware(cls) -> HardwareProfile:
        """
        Auto-detect hardware profile by checking CUDA availability and VRAM.

        Does not require torch — gracefully returns CPU_ONLY on ImportError.

        Returns:
            Detected HardwareProfile.
        """
        try:
            import torch
            if not torch.cuda.is_available():
                return HardwareProfile.CPU_ONLY

            # Check VRAM of first device
            vram_bytes = torch.cuda.get_device_properties(0).total_mem
            vram_gb = vram_bytes / (1024 ** 3)
            if vram_gb >= 8:
                return HardwareProfile.GPU_HIGH
            return HardwareProfile.GPU_LOW

        except (ImportError, Exception):
            return HardwareProfile.CPU_ONLY


# ===== Factory function =====

@nfo.log_call(level="INFO")
def create_optimization_strategy(
    settings: Any = None,
    cost_budget: Any = None,
) -> OptimizationStrategy:
    """
    Create OptimizationStrategy from application settings.

    Parses OPTIMIZATION_PRIORITY and HARDWARE_PROFILE from settings.
    Auto-detects hardware if HARDWARE_PROFILE=auto.

    Args:
        settings: Settings instance (or None for defaults).
        cost_budget: CostBudget instance (or None).

    Returns:
        Configured OptimizationStrategy.
    """
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    # Parse priority
    priority_str = getattr(settings, "optimization_priority", "auto")
    try:
        priority = Priority(priority_str)
    except ValueError:
        logger.warning("Invalid optimization_priority, defaulting to auto",
                        value=priority_str)
        priority = Priority.AUTO

    # Parse or detect hardware
    hw_str = getattr(settings, "hardware_profile", "auto")
    try:
        hardware = HardwareProfile(hw_str)
    except ValueError:
        logger.warning("Invalid hardware_profile, defaulting to auto",
                        value=hw_str)
        hardware = HardwareProfile.AUTO

    if hardware == HardwareProfile.AUTO:
        hardware = OptimizationStrategy.detect_hardware()
        logger.info("Hardware auto-detected", profile=hardware.value)

    strategy = OptimizationStrategy(
        priority=priority,
        hardware=hardware,
        cost_budget=cost_budget,
        settings=settings,
    )

    logger.info(
        "OptimizationStrategy created",
        priority=priority.value,
        hardware=hardware.value,
    )

    return strategy
