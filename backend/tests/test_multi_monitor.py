"""Tests for multi_monitor.py — snapshot building, active monitor detection, priority, descriptions."""
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from multi_monitor import (
    MonitorActivity, MultiMonitorSnapshot, MonitorAwareCapture,
)
from window_aware import MonitorInfo, WindowInfo, AppCategory


def _make_monitors():
    """Two-monitor setup: left 1920x1080, right 2560x1440."""
    return [
        MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(index=1, name="DP-2", x=1920, y=0, width=2560, height=1440),
    ]


def _make_windows():
    """Windows spread across two monitors."""
    return [
        WindowInfo(x=100, y=100, width=800, height=600, category=AppCategory.IDE, wm_class_name="Code"),
        WindowInfo(x=200, y=200, width=600, height=400, category=AppCategory.TERMINAL, wm_class_name="Alacritty"),
        WindowInfo(x=2000, y=100, width=1200, height=900, category=AppCategory.BROWSER, wm_class_name="Firefox"),
    ]


# ── Data structures ──────────────────────────────────────────────────

class TestMonitorActivity:
    def test_to_dict_keys(self):
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        act = MonitorActivity(monitor=mon, window_count=3, has_active_window=True)
        d = act.to_dict()
        assert d["window_count"] == 3
        assert d["has_active_window"] is True
        assert "monitor" in d
        assert "priority" in d


class TestMultiMonitorSnapshot:
    def test_to_dict_keys(self):
        snap = MultiMonitorSnapshot(
            timestamp=time.time(),
            total_monitors=2,
            active_monitor_index=0,
            prioritized_order=[0, 1],
            description="test",
        )
        d = snap.to_dict()
        assert d["total_monitors"] == 2
        assert d["prioritized_order"] == [0, 1]
        assert d["description"] == "test"


# ── Active monitor detection ─────────────────────────────────────────

class TestDetectActiveMonitor:
    def setup_method(self):
        self.mac = MonitorAwareCapture(active_only=True, include_description=False)
        self.monitors = _make_monitors()

    def test_active_window_on_left_monitor(self):
        win = WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0)
        assert self.mac.detect_active_monitor(self.monitors, win) == 0

    def test_active_window_on_right_monitor(self):
        win = WindowInfo(x=2000, y=100, width=800, height=600, monitor_index=1)
        assert self.mac.detect_active_monitor(self.monitors, win) == 1

    def test_active_window_geometry_fallback(self):
        """When monitor_index doesn't match any monitor, fall back to geometry."""
        win = WindowInfo(x=2000, y=100, width=800, height=600, monitor_index=99)
        result = self.mac.detect_active_monitor(self.monitors, win)
        assert result == 1  # center of window is on monitor 1

    def test_no_active_window_falls_to_primary(self):
        """Without active window or mouse, should return primary monitor."""
        with patch.object(self.mac, '_get_mouse_monitor', return_value=None):
            result = self.mac.detect_active_monitor(self.monitors, None)
            assert result == 0  # DP-1 is primary

    def test_empty_monitors_returns_zero(self):
        assert self.mac.detect_active_monitor([], None) == 0

    def test_mouse_fallback(self):
        """When no active window, use mouse position."""
        with patch.object(self.mac, '_get_mouse_monitor', return_value=1):
            result = self.mac.detect_active_monitor(self.monitors, None)
            assert result == 1


# ── Snapshot building ────────────────────────────────────────────────

class TestBuildSnapshot:
    def setup_method(self):
        self.mac = MonitorAwareCapture(active_only=True, include_description=True)

    def test_basic_snapshot(self):
        monitors = _make_monitors()
        windows = _make_windows()
        active = WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0)

        snap = self.mac.build_snapshot(monitors, windows, active)
        assert snap.total_monitors == 2
        assert snap.active_monitor_index == 0
        assert len(snap.monitors) == 2
        assert len(snap.prioritized_order) == 2

    def test_windows_mapped_to_monitors(self):
        monitors = _make_monitors()
        windows = _make_windows()
        active = WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0)

        snap = self.mac.build_snapshot(monitors, windows, active)
        left = snap.monitors[0]
        right = snap.monitors[1]

        # Left monitor: IDE + Terminal
        assert left.window_count == 2
        assert "ide" in left.category_summary
        assert "terminal" in left.category_summary

        # Right monitor: Browser
        assert right.window_count == 1
        assert "browser" in right.category_summary

    def test_active_monitor_has_highest_priority(self):
        monitors = _make_monitors()
        windows = _make_windows()
        active = WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0)

        snap = self.mac.build_snapshot(monitors, windows, active)
        assert snap.prioritized_order[0] == 0  # active monitor first

    def test_no_windows_empty_snapshot(self):
        monitors = _make_monitors()
        snap = self.mac.build_snapshot(monitors, [], None)
        assert snap.total_monitors == 2
        for act in snap.monitors:
            assert act.window_count == 0

    def test_description_generated(self):
        monitors = _make_monitors()
        windows = _make_windows()
        active = WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0)

        snap = self.mac.build_snapshot(monitors, windows, active)
        assert "Monitory" in snap.description
        assert "ACTIVE" in snap.description

    def test_single_monitor_description(self):
        monitors = [MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True)]
        snap = self.mac.build_snapshot(monitors, [], None)
        assert "main" in snap.description


# ── Priority computation ─────────────────────────────────────────────

class TestPriority:
    def test_active_window_boosts_priority(self):
        mac = MonitorAwareCapture()
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True)
        act = MonitorActivity(monitor=mon, has_active_window=True, window_count=1)
        score = mac._compute_priority(act, mon)
        assert score >= mac.WEIGHT_ACTIVE_WINDOW

    def test_primary_gets_boost(self):
        mac = MonitorAwareCapture()
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True)
        act = MonitorActivity(monitor=mon, window_count=0)
        score = mac._compute_priority(act, mon)
        assert score >= mac.WEIGHT_PRIMARY

    def test_window_count_adds_to_priority(self):
        mac = MonitorAwareCapture()
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        act_few = MonitorActivity(monitor=mon, window_count=1)
        act_many = MonitorActivity(monitor=mon, window_count=5)
        assert mac._compute_priority(act_many, mon) > mac._compute_priority(act_few, mon)


# ── Monitors to analyze ──────────────────────────────────────────────

class TestMonitorsToAnalyze:
    def test_active_only_mode(self):
        mac = MonitorAwareCapture(active_only=True, include_description=False)
        monitors = _make_monitors()
        active = WindowInfo(x=2000, y=100, width=800, height=600, monitor_index=1)
        snap = mac.build_snapshot(monitors, _make_windows(), active)

        to_analyze = mac.get_monitors_to_analyze(snap)
        assert to_analyze == [1]  # only active monitor

    def test_all_monitors_mode(self):
        mac = MonitorAwareCapture(active_only=False, include_description=False)
        monitors = _make_monitors()
        active = WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0)
        snap = mac.build_snapshot(monitors, _make_windows(), active)

        to_analyze = mac.get_monitors_to_analyze(snap)
        assert len(to_analyze) == 2
        assert to_analyze[0] == 0  # active first

    def test_no_snapshot_returns_default(self):
        mac = MonitorAwareCapture()
        assert mac.get_monitors_to_analyze() == [0]


# ── ROI ──────────────────────────────────────────────────────────────

class TestCaptureROI:
    def test_roi_matches_monitor_geometry(self):
        mac = MonitorAwareCapture()
        mon = MonitorInfo(index=1, name="DP-2", x=1920, y=0, width=2560, height=1440)
        roi = mac.get_capture_roi_for_monitor(mon)
        assert roi == {"left": 1920, "top": 0, "width": 2560, "height": 1440}


# ── Window-on-monitor helper ─────────────────────────────────────────

class TestWindowOnMonitor:
    def test_window_center_inside(self):
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        w = WindowInfo(x=100, y=100, width=800, height=600)
        assert MonitorAwareCapture._window_on_monitor(mon, w) is True

    def test_window_center_outside(self):
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        w = WindowInfo(x=2000, y=100, width=800, height=600)
        assert MonitorAwareCapture._window_on_monitor(mon, w) is False

    def test_zero_size_window(self):
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        w = WindowInfo(x=100, y=100, width=0, height=0)
        assert MonitorAwareCapture._window_on_monitor(mon, w) is False


# ── Position labels ──────────────────────────────────────────────────

class TestPositionLabels:
    def test_single_monitor(self):
        mon = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        acts = [MonitorActivity(monitor=mon)]
        labels = MonitorAwareCapture._assign_position_labels(acts)
        assert labels[0] == "main"

    def test_two_monitors(self):
        m0 = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        m1 = MonitorInfo(index=1, name="DP-2", x=1920, y=0, width=2560, height=1440)
        acts = [MonitorActivity(monitor=m0), MonitorActivity(monitor=m1)]
        labels = MonitorAwareCapture._assign_position_labels(acts)
        assert labels[0] == "left"
        assert labels[1] == "right"

    def test_three_monitors(self):
        m0 = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080)
        m1 = MonitorInfo(index=1, name="DP-2", x=1920, y=0, width=1920, height=1080)
        m2 = MonitorInfo(index=2, name="DP-3", x=3840, y=0, width=1920, height=1080)
        acts = [MonitorActivity(monitor=m0), MonitorActivity(monitor=m1), MonitorActivity(monitor=m2)]
        labels = MonitorAwareCapture._assign_position_labels(acts)
        assert labels[0] == "left"
        assert labels[1] == "center"
        assert labels[2] == "right"


# ── Stats ────────────────────────────────────────────────────────────

class TestStats:
    def test_initial_stats(self):
        mac = MonitorAwareCapture()
        stats = mac.get_stats()
        assert stats["total_snapshots"] == 0
        assert stats["last_active_monitor"] is None

    def test_stats_after_snapshot(self):
        mac = MonitorAwareCapture(include_description=False)
        monitors = _make_monitors()
        mac.build_snapshot(monitors, _make_windows(),
                           WindowInfo(x=100, y=100, width=800, height=600, monitor_index=0))
        stats = mac.get_stats()
        assert stats["total_snapshots"] == 1
        assert stats["last_active_monitor"] == 0
        assert stats["total_monitors"] == 2
