"""Pipeline context, profiles, and profile selector."""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


# ===== Pipeline Profiles =====

class PipelineProfile(str, Enum):
    """
    Pipeline execution profiles for specialized task routing.

    FAST   — low-latency: skip cropping, use OCR-only/hybrid, cached window scan
    NORMAL — balanced: cached scan, top-K crops, hybrid analysis
    FULL   — quality-first: full scan, crop all, ocr+vision analysis
    """
    FAST = "fast"
    NORMAL = "normal"
    FULL = "full"


class ProfileSelector:
    """
    Adaptively selects pipeline profile per tick based on heuristics.

    Rules:
    - FULL every `full_interval` seconds (periodic deep scan)
    - FULL when active window changes (new app context)
    - NORMAL when screen change detected (default)
    - FAST when no significant change or in idle approach
    """

    def __init__(
        self,
        full_interval: float = 60.0,
        force_profile: Optional[str] = None,
        ocr_manager=None,
    ):
        self.full_interval = full_interval
        self.force_profile = PipelineProfile(force_profile) if force_profile else None
        self._ocr_manager = ocr_manager
        self._last_full_time = 0.0
        self._last_active_wid = 0
        self._consecutive_fast = 0
        # Stats
        self.profile_counts = {p.value: 0 for p in PipelineProfile}

    def select(self, ctx: 'PipelineContext', capture=None) -> PipelineProfile:
        """
        Choose optimal profile for this pipeline tick.

        Args:
            ctx: Current pipeline context (may have cached windows from prev tick)
            capture: SmartScreenCapture instance for idle detection

        Returns:
            Selected PipelineProfile
        """
        if self.force_profile:
            self.profile_counts[self.force_profile.value] += 1
            return self.force_profile

        now = time.time()

        # Periodic FULL scan
        if now - self._last_full_time >= self.full_interval:
            self._last_full_time = now
            self._consecutive_fast = 0
            self.profile_counts[PipelineProfile.FULL.value] += 1
            return PipelineProfile.FULL

        # Check if capture system is in idle mode
        is_idle = False
        if capture and hasattr(capture, 'consecutive_unchanged'):
            is_idle = capture.consecutive_unchanged > getattr(capture, 'idle_threshold', 30)

        if is_idle:
            # VLM OCR is cloud-based and high-latency — FAST profile is an oxymoron
            if self._is_vlm_ocr_active():
                self._consecutive_fast = 0
                self.profile_counts[PipelineProfile.NORMAL.value] += 1
                return PipelineProfile.NORMAL
            self._consecutive_fast += 1
            self.profile_counts[PipelineProfile.FAST.value] += 1
            return PipelineProfile.FAST

        # Default: NORMAL
        self._consecutive_fast = 0
        self.profile_counts[PipelineProfile.NORMAL.value] += 1
        return PipelineProfile.NORMAL

    def _is_vlm_ocr_active(self) -> bool:
        """Check if the active OCR engine is cloud-based VLM OCR (high latency)."""
        if not self._ocr_manager:
            return False
        return getattr(self._ocr_manager, "active_engine_name", "") == "vlm_ocr"

    def notify_active_window_changed(self, new_wid: int):
        """Signal that active window changed — next tick should be FULL."""
        if new_wid != self._last_active_wid:
            self._last_active_wid = new_wid
            self._last_full_time = 0  # Force FULL on next tick

    def get_stats(self) -> Dict:
        return {
            "profile_counts": self.profile_counts,
            "full_interval": self.full_interval,
            "force_profile": self.force_profile.value if self.force_profile else None,
            "last_full_ago": round(time.time() - self._last_full_time, 1) if self._last_full_time else None,
        }


# ===== Pipeline Context (shared state accumulator) =====

@dataclass
class PipelineContext:
    """
    Immutable-ish accumulator passed through pipeline steps.

    Each step reads what it needs and writes its output.
    The context is the single source of truth for one pipeline run.
    """
    # Identity
    run_id: str = ""
    correlation_id: str = ""
    timestamp: float = 0.0

    # Phase 1: Window scanning
    all_windows: List[Any] = field(default_factory=list)
    active_window: Optional[Any] = None  # WindowInfo
    window_context_str: str = ""
    roi: Optional[Dict] = None

    # Phase 2: Screen capture
    image_b64: Optional[str] = None
    capture_result: Optional[Dict] = None
    fullscreen_image: Optional[Any] = None  # PIL.Image
    capture_image: Optional[Any] = None     # PIL.Image (resized) — avoids base64 re-decode

    # Phase 3+4: Cropping & organizing
    organized_screen: Optional[Any] = None  # OrganizedScreenData
    screen_summary: str = ""

    # Phase 5: Context building
    context_str: str = ""
    prompt_addon: str = ""
    full_context: str = ""

    # Phase 6: Analysis
    analysis_result: Optional[Dict] = None
    analysis_failed: bool = False

    # Phase 7: Agent actions
    agent_actions: List[Dict] = field(default_factory=list)

    # Broadcast payload
    broadcast_data: Optional[Dict] = None

    # Pipeline profile for this run
    profile: str = "normal"  # PipelineProfile value

    # Tier 1 module fields
    multi_monitor_snapshot: Optional[Any] = None
    monitor_description: str = ""
    recalled_memories: List[Any] = field(default_factory=list)
    template_actions: List[Dict] = field(default_factory=list)
    ocr_enhanced: bool = False
    ocr_corrections: int = 0
    prediction: Optional[Dict] = None
    used_prefetch: bool = False
    clipboard_suggestions: List[Dict] = field(default_factory=list)
    clipboard_auto_copies: List[Dict] = field(default_factory=list)

    # Metadata
    steps_executed: List[str] = field(default_factory=list)
    step_timings: Dict[str, float] = field(default_factory=dict)
    errors: List[Dict] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = str(uuid.uuid4())[:8]
        if not self.correlation_id:
            self.correlation_id = self.run_id
        if self.timestamp == 0.0:
            self.timestamp = time.time()
