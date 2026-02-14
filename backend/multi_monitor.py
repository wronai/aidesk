"""
Multi-Monitor Intelligence — smart per-monitor capture and prioritization.

Provides:
- Detect which monitor has user's attention (mouse position / active window)
- Capture per-monitor screenshots independently
- Prioritize monitors by activity level (active > recently-changed > idle)
- Cost optimization: analyze only active monitor by default (60-80% savings)
- Rich monitor descriptions for LLM context ("left monitor shows docs, right shows code")

Integrates with:
- WindowManager (window_aware.py) — monitor list, active window monitor
- SmartScreenCapture (capture.py) — per-monitor ROI capture
- PipelineContext (pipeline.py) — new multi_monitor_data field
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import nfo
import structlog

from window_aware import MonitorInfo, WindowInfo, AppCategory

logger = structlog.get_logger()


@dataclass
class MonitorActivity:
    """Tracks activity level for a single monitor."""
    monitor: MonitorInfo
    window_count: int = 0
    has_active_window: bool = False
    has_focus_window: bool = False
    category_summary: List[str] = field(default_factory=list)
    last_change_time: float = 0.0
    change_score: float = 0.0  # aggregate change from window crops
    priority: int = 0          # computed rank (higher = more important)

    def to_dict(self) -> Dict:
        return {
            "monitor": self.monitor.to_dict(),
            "window_count": self.window_count,
            "has_active_window": self.has_active_window,
            "has_focus_window": self.has_focus_window,
            "category_summary": self.category_summary,
            "last_change_time": self.last_change_time,
            "change_score": round(self.change_score, 2),
            "priority": self.priority,
        }


@dataclass
class MultiMonitorSnapshot:
    """Complete multi-monitor state for one pipeline tick."""
    timestamp: float = 0.0
    total_monitors: int = 0
    active_monitor_index: int = 0
    monitors: List[MonitorActivity] = field(default_factory=list)
    prioritized_order: List[int] = field(default_factory=list)  # monitor indices sorted by priority
    description: str = ""  # Human-readable summary for LLM

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "total_monitors": self.total_monitors,
            "active_monitor_index": self.active_monitor_index,
            "monitors": [m.to_dict() for m in self.monitors],
            "prioritized_order": self.prioritized_order,
            "description": self.description,
        }


class MonitorAwareCapture:
    """
    Multi-monitor intelligence layer.

    Sits between WindowManager/ProcessScanner and the capture pipeline.
    Determines which monitors are worth analyzing and in what order.
    """

    # Priority weights
    WEIGHT_ACTIVE_WINDOW = 100
    WEIGHT_FOCUS_WINDOW = 80
    WEIGHT_WINDOW_COUNT = 5
    WEIGHT_CHANGE_SCORE = 10
    WEIGHT_PRIMARY = 20

    def __init__(
        self,
        active_only: bool = True,
        include_description: bool = True,
    ):
        """
        Args:
            active_only: If True, pipeline should only analyze the active monitor
            include_description: Generate human-readable monitor descriptions for LLM
        """
        self.active_only = active_only
        self.include_description = include_description

        # State
        self._last_snapshot: Optional[MultiMonitorSnapshot] = None
        self._mouse_monitor_cache: Optional[int] = None
        self._mouse_cache_time: float = 0.0

        # Stats
        self.total_snapshots = 0
        self.monitors_skipped = 0

        logger.info(
            "MonitorAwareCapture initialized",
            active_only=active_only,
        )

    def detect_active_monitor(
        self,
        monitors: List[MonitorInfo],
        active_window: Optional[WindowInfo] = None,
    ) -> int:
        """
        Determine which monitor has the user's attention.

        Priority:
        1. Monitor containing active window (most reliable)
        2. Monitor under mouse cursor (fallback)
        3. Primary monitor (final fallback)

        Args:
            monitors: List of connected monitors
            active_window: Current active window info

        Returns:
            Monitor index with user's attention
        """
        if not monitors:
            return 0

        # 1. Active window monitor
        if active_window:
            # Try stored monitor_index first
            if active_window.monitor_index >= 0:
                for mon in monitors:
                    if mon.index == active_window.monitor_index:
                        return mon.index

            # Fallback: window center → monitor lookup
            if active_window.width > 0:
                cx = active_window.x + active_window.width // 2
                cy = active_window.y + active_window.height // 2
                for mon in monitors:
                    if (mon.x <= cx < mon.x + mon.width and
                            mon.y <= cy < mon.y + mon.height):
                        return mon.index

        # 2. Mouse cursor monitor
        mouse_mon = self._get_mouse_monitor(monitors)
        if mouse_mon is not None:
            return mouse_mon

        # 3. Primary monitor
        for mon in monitors:
            if mon.is_primary:
                return mon.index

        return 0

    def build_snapshot(
        self,
        monitors: List[MonitorInfo],
        all_windows: List = None,
        active_window: Optional[WindowInfo] = None,
        organized_screen=None,
    ) -> MultiMonitorSnapshot:
        """
        Build a complete multi-monitor activity snapshot.

        Maps windows to monitors, computes per-monitor activity,
        and generates prioritized ordering.

        Args:
            monitors: Connected monitors
            all_windows: All visible windows (VisibleWindow or WindowInfo list)
            active_window: Current active window
            organized_screen: OrganizedScreenData (for change scores)

        Returns:
            MultiMonitorSnapshot with per-monitor activity data
        """
        self.total_snapshots += 1
        now = time.time()
        all_windows = all_windows or []

        active_idx = self.detect_active_monitor(monitors, active_window)

        # Build per-monitor activity
        activities = []
        for mon in monitors:
            activity = MonitorActivity(monitor=mon)

            # Map windows to this monitor
            mon_windows = self._windows_on_monitor(mon, all_windows)
            activity.window_count = len(mon_windows)

            # Check if active window is on this monitor
            activity.has_active_window = (mon.index == active_idx)

            # Collect categories
            categories = set()
            for w in mon_windows:
                cat = getattr(w, 'category', AppCategory.UNKNOWN)
                if isinstance(cat, AppCategory):
                    categories.add(cat.value)
                elif isinstance(cat, str):
                    categories.add(cat)
            activity.category_summary = sorted(categories)

            # Aggregate change scores from organized screen data
            if organized_screen:
                for crop in getattr(organized_screen, 'crops', []):
                    win = crop.window
                    if self._window_on_monitor(mon, win):
                        activity.change_score += crop.change_score
                        if crop.is_focus:
                            activity.has_focus_window = True

            # Compute priority score
            activity.priority = self._compute_priority(activity, mon)

            activities.append(activity)

        # Sort by priority (descending)
        prioritized = sorted(activities, key=lambda a: a.priority, reverse=True)
        prioritized_order = [a.monitor.index for a in prioritized]

        # Count skipped monitors
        if self.active_only and len(monitors) > 1:
            self.monitors_skipped += len(monitors) - 1

        # Build description
        description = ""
        if self.include_description and monitors:
            description = self._build_description(activities, active_idx)

        snapshot = MultiMonitorSnapshot(
            timestamp=now,
            total_monitors=len(monitors),
            active_monitor_index=active_idx,
            monitors=activities,
            prioritized_order=prioritized_order,
            description=description,
        )
        self._last_snapshot = snapshot
        return snapshot

    def get_capture_roi_for_monitor(self, monitor: MonitorInfo) -> Dict:
        """
        Get the ROI dict for capturing a specific monitor.

        Args:
            monitor: MonitorInfo to capture

        Returns:
            ROI dict compatible with SmartScreenCapture.capture(roi=...)
        """
        return {
            "left": monitor.x,
            "top": monitor.y,
            "width": monitor.width,
            "height": monitor.height,
        }

    def get_monitors_to_analyze(
        self,
        snapshot: Optional[MultiMonitorSnapshot] = None,
    ) -> List[int]:
        """
        Get list of monitor indices that should be analyzed this tick.

        In active_only mode: returns just the active monitor.
        Otherwise: returns all monitors in priority order.

        Args:
            snapshot: Current snapshot (uses last if None)

        Returns:
            List of monitor indices to analyze
        """
        snap = snapshot or self._last_snapshot
        if not snap or not snap.monitors:
            return [0]

        if self.active_only:
            return [snap.active_monitor_index]

        return snap.prioritized_order

    def prioritize_monitors(
        self,
        snapshot: Optional[MultiMonitorSnapshot] = None,
    ) -> List[MonitorActivity]:
        """
        Get monitors sorted by activity priority (highest first).

        Args:
            snapshot: Current snapshot (uses last if None)

        Returns:
            Sorted list of MonitorActivity
        """
        snap = snapshot or self._last_snapshot
        if not snap:
            return []
        return sorted(snap.monitors, key=lambda a: a.priority, reverse=True)

    # ── Internal helpers ──────────────────────────────────────────────

    def _compute_priority(self, activity: MonitorActivity, mon: MonitorInfo) -> int:
        """Compute priority score for a monitor."""
        score = 0
        if activity.has_active_window:
            score += self.WEIGHT_ACTIVE_WINDOW
        if activity.has_focus_window:
            score += self.WEIGHT_FOCUS_WINDOW
        if mon.is_primary:
            score += self.WEIGHT_PRIMARY
        score += activity.window_count * self.WEIGHT_WINDOW_COUNT
        score += int(activity.change_score * self.WEIGHT_CHANGE_SCORE)
        return score

    def _windows_on_monitor(self, mon: MonitorInfo, windows: List) -> List:
        """Filter windows that are on a specific monitor."""
        result = []
        for w in windows:
            if self._window_on_monitor(mon, w):
                result.append(w)
        return result

    @staticmethod
    def _window_on_monitor(mon: MonitorInfo, w) -> bool:
        """Check if a window's center falls within a monitor."""
        wx = getattr(w, 'x', 0)
        wy = getattr(w, 'y', 0)
        ww = getattr(w, 'width', 0)
        wh = getattr(w, 'height', 0)
        if ww == 0 or wh == 0:
            return False
        cx = wx + ww // 2
        cy = wy + wh // 2
        return (mon.x <= cx < mon.x + mon.width and
                mon.y <= cy < mon.y + mon.height)

    def _get_mouse_monitor(self, monitors: List[MonitorInfo]) -> Optional[int]:
        """Get monitor index where mouse cursor is located."""
        now = time.time()
        # Cache for 1 second
        if self._mouse_monitor_cache is not None and (now - self._mouse_cache_time) < 1.0:
            return self._mouse_monitor_cache

        try:
            result = subprocess.run(
                ["xdotool", "getmouselocation", "--shell"],
                capture_output=True, text=True, timeout=1,
            )
            if result.returncode != 0:
                return None

            mx, my = 0, 0
            for line in result.stdout.splitlines():
                if line.startswith("X="):
                    mx = int(line.split("=")[1])
                elif line.startswith("Y="):
                    my = int(line.split("=")[1])

            for mon in monitors:
                if (mon.x <= mx < mon.x + mon.width and
                        mon.y <= my < mon.y + mon.height):
                    self._mouse_monitor_cache = mon.index
                    self._mouse_cache_time = now
                    return mon.index
        except Exception:
            pass

        return None

    def _build_description(
        self,
        activities: List[MonitorActivity],
        active_idx: int,
    ) -> str:
        """Build human-readable description of monitor layout for LLM context."""
        if not activities:
            return ""

        parts = []
        # Sort by physical position (leftmost first)
        sorted_acts = sorted(activities, key=lambda a: (a.monitor.x, a.monitor.y))

        position_labels = self._assign_position_labels(sorted_acts)

        for i, act in enumerate(sorted_acts):
            mon = act.monitor
            label = position_labels.get(mon.index, f"monitor {mon.index}")
            is_active = "👁️ ACTIVE" if act.has_active_window else ""
            cats = ", ".join(act.category_summary) if act.category_summary else "empty"
            res = f"{mon.width}x{mon.height}"

            part = f"{label} ({res}): {cats}"
            if is_active:
                part = f"{part} {is_active}"
            if act.has_focus_window:
                part = f"{part} 🎯focus"

            parts.append(part)

        return "🖥️ Monitory: " + " | ".join(parts)

    @staticmethod
    def _assign_position_labels(sorted_activities: List[MonitorActivity]) -> Dict[int, str]:
        """Assign human-readable position labels (left/center/right or top/bottom)."""
        labels = {}
        n = len(sorted_activities)
        if n == 1:
            labels[sorted_activities[0].monitor.index] = "main"
        elif n == 2:
            labels[sorted_activities[0].monitor.index] = "left"
            labels[sorted_activities[1].monitor.index] = "right"
        elif n == 3:
            labels[sorted_activities[0].monitor.index] = "left"
            labels[sorted_activities[1].monitor.index] = "center"
            labels[sorted_activities[2].monitor.index] = "right"
        else:
            for i, act in enumerate(sorted_activities):
                labels[act.monitor.index] = f"monitor-{i}"
        return labels

    # ── Stats ─────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get multi-monitor statistics."""
        return {
            "active_only": self.active_only,
            "total_snapshots": self.total_snapshots,
            "monitors_skipped": self.monitors_skipped,
            "last_active_monitor": (
                self._last_snapshot.active_monitor_index
                if self._last_snapshot else None
            ),
            "total_monitors": (
                self._last_snapshot.total_monitors
                if self._last_snapshot else 0
            ),
        }


def create_multi_monitor_from_env(settings=None) -> MonitorAwareCapture:
    """Create MonitorAwareCapture from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return MonitorAwareCapture(
        active_only=settings.multi_monitor_active_only,
        include_description=settings.multi_monitor_description,
    )
