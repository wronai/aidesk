"""Tests for window_aware.py — WindowInfo, MonitorInfo, rules, helpers, ROI, stats."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from window_aware import (
    AppCategory, WindowInfo, MonitorInfo, WindowManager,
    APP_RULES, PROCESS_RULES, CMDLINE_RULES, TITLE_FALLBACK_RULES,
    SERVICE_WINDOW_CLASS_PATTERNS, SERVICE_WINDOW_TITLES,
    _COMPILED_APP_RULES, _COMPILED_PROCESS_RULES,
    _COMPILED_CMDLINE_RULES, _COMPILED_TITLE_RULES,
)


# ── Data structures ──────────────────────────────────────────────────

class TestWindowInfo:
    def test_default_values(self):
        w = WindowInfo()
        assert w.window_id == 0
        assert w.category == AppCategory.UNKNOWN
        assert w.title == ""

    def test_to_dict_keys(self):
        w = WindowInfo(title="test", wm_class="cls", pid=123)
        d = w.to_dict()
        assert d["title"] == "test"
        assert d["pid"] == 123
        assert "geometry" in d
        assert d["git"] is None  # no git_repo

    def test_to_dict_with_git(self):
        w = WindowInfo(git_repo="myrepo", git_branch="main", git_status="clean")
        d = w.to_dict()
        assert d["git"]["repo"] == "myrepo"
        assert d["git"]["branch"] == "main"

    def test_to_context_string(self):
        w = WindowInfo(title="VSCode", wm_class_name="Code", category=AppCategory.IDE,
                       cwd="/home/user/project", git_repo="project", git_branch="main")
        ctx = w.to_context_string()
        assert "VSCode" in ctx
        assert "Code" in ctx
        assert "ide" in ctx
        assert "/home/user/project" in ctx
        assert "main" in ctx

    def test_to_context_string_minimal(self):
        w = WindowInfo(title="Untitled")
        ctx = w.to_context_string()
        assert "Untitled" in ctx


class TestMonitorInfo:
    def test_to_dict(self):
        m = MonitorInfo(index=0, name="DP-1", x=0, y=0, width=2560, height=1440, is_primary=True)
        d = m.to_dict()
        assert d["name"] == "DP-1"
        assert d["width"] == 2560
        assert d["is_primary"] is True


# ── Rule lists ────────────────────────────────────────────────────────

class TestRuleLists:
    def test_app_rules_cover_all_categories(self):
        """APP_RULES should cover most important categories."""
        categories = {cat for _, cat in APP_RULES}
        assert AppCategory.IDE in categories
        assert AppCategory.TERMINAL in categories
        assert AppCategory.BROWSER in categories
        assert AppCategory.EMAIL in categories
        assert AppCategory.CHAT in categories
        assert AppCategory.SYSTEM in categories

    def test_compiled_rules_match_raw_count(self):
        assert len(_COMPILED_APP_RULES) == len(APP_RULES)
        assert len(_COMPILED_PROCESS_RULES) == len(PROCESS_RULES)
        assert len(_COMPILED_CMDLINE_RULES) == len(CMDLINE_RULES)
        assert len(_COMPILED_TITLE_RULES) == len(TITLE_FALLBACK_RULES)

    def test_cmdline_rules_not_empty(self):
        assert len(CMDLINE_RULES) >= 5

    def test_title_fallback_rules_not_empty(self):
        assert len(TITLE_FALLBACK_RULES) >= 3

    def test_service_patterns_include_critical(self):
        """Service window patterns must include mutter and overlay."""
        combined = " ".join(SERVICE_WINDOW_CLASS_PATTERNS)
        assert "mutter" in combined
        assert "proxeen" in combined

    def test_service_titles_include_guard(self):
        assert "mutter guard window" in SERVICE_WINDOW_TITLES


# ── Classification (supplemental to test_units.py::TestAppClassification) ──

class TestClassifyAppDataDriven:
    """Test the data-driven classification through all 4 layers."""

    def test_cmdline_layer_ide(self):
        result = WindowManager._classify_app("", "", "", cmdline="/opt/jetbrains/pycharm")
        assert result == AppCategory.IDE

    def test_cmdline_layer_game(self):
        result = WindowManager._classify_app("", "", "", cmdline="/usr/bin/java -jar minecraft.jar")
        assert result == AppCategory.GAME

    def test_title_fallback_layer_vim(self):
        result = WindowManager._classify_app("", "", "editing file.py - vim")
        assert result == AppCategory.IDE

    def test_title_fallback_layer_bash(self):
        result = WindowManager._classify_app("", "", "user@host: bash")
        assert result == AppCategory.TERMINAL

    def test_title_fallback_layer_browser(self):
        result = WindowManager._classify_app("", "", "Google - Mozilla Firefox")
        assert result == AppCategory.BROWSER

    def test_wm_class_takes_priority_over_title(self):
        """WM_CLASS should win over title fallback."""
        result = WindowManager._classify_app("alacritty", "Alacritty", "vim server.py")
        assert result == AppCategory.TERMINAL

    def test_process_takes_priority_over_cmdline(self):
        """Process name layer should win over cmdline layer."""
        result = WindowManager._classify_app("", "", "", process_name="slack", cmdline="/usr/bin/electron")
        assert result == AppCategory.CHAT


# ── Service window detection ─────────────────────────────────────────

class TestServiceWindowDetection:
    def test_service_by_class(self):
        assert WindowManager._is_service_window_fields("mutter-x11-frames", "", "") is True
        assert WindowManager._is_service_window_fields("gnome-shell", "", "") is True

    def test_service_by_title(self):
        assert WindowManager._is_service_window_fields("", "", "mutter guard window") is True

    def test_normal_app_not_service(self):
        assert WindowManager._is_service_window_fields("firefox", "Firefox", "Google") is False

    def test_none_fields_handled(self):
        assert WindowManager._is_service_window_fields(None, None, None) is False


# ── Geometry parsing ─────────────────────────────────────────────────

class TestParseGeometry:
    def test_parse_xdotool_geometry(self):
        info = WindowInfo()
        geom = "X=100\nY=200\nWIDTH=800\nHEIGHT=600\nSCREEN=0"
        WindowManager._parse_geometry(info, geom)
        assert info.x == 100
        assert info.y == 200
        assert info.width == 800
        assert info.height == 600


# ── Path extraction from title ───────────────────────────────────────

class TestExtractPathFromTitle:
    def test_home_relative(self, tmp_path):
        # This test needs a real directory to match
        result = WindowManager._extract_path_from_title(str(tmp_path))
        assert result == str(tmp_path)

    def test_no_path(self):
        result = WindowManager._extract_path_from_title("Google Chrome - Tab")
        assert result == ""

    def test_empty_title(self):
        result = WindowManager._extract_path_from_title("")
        assert result == ""


# ── Window ROI ───────────────────────────────────────────────────────

class TestWindowROI:
    def test_roi_from_info(self):
        wm = WindowManager.__new__(WindowManager)
        wm._monitors = []
        wm._monitors_time = 0
        wm._monitors_cache_ttl = 30
        wm._has_xrandr = False
        info = WindowInfo(x=10, y=20, width=800, height=600, monitor_index=0)
        roi = wm.get_window_roi(info)
        assert roi == {"left": 10, "top": 20, "width": 800, "height": 600, "monitor": 0}

    def test_roi_zero_size_returns_none(self):
        wm = WindowManager.__new__(WindowManager)
        wm._monitors = []
        wm._monitors_time = 0
        wm._monitors_cache_ttl = 30
        wm._has_xrandr = False
        info = WindowInfo(width=0, height=0)
        assert wm.get_window_roi(info) is None


# ── Monitor mapping ──────────────────────────────────────────────────

class TestMonitorMapping:
    def test_window_on_primary(self):
        wm = WindowManager.__new__(WindowManager)
        wm._monitors = [
            MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True),
            MonitorInfo(index=1, name="DP-2", x=1920, y=0, width=1920, height=1080),
        ]
        wm._monitors_time = time.time()
        wm._monitors_cache_ttl = 30
        wm._has_xrandr = False

        info = WindowInfo(x=100, y=100, width=800, height=600)
        assert wm._get_monitor_for_window(info) == 0

    def test_window_on_secondary(self):
        wm = WindowManager.__new__(WindowManager)
        wm._monitors = [
            MonitorInfo(index=0, name="DP-1", x=0, y=0, width=1920, height=1080, is_primary=True),
            MonitorInfo(index=1, name="DP-2", x=1920, y=0, width=1920, height=1080),
        ]
        wm._monitors_time = time.time()
        wm._monitors_cache_ttl = 30
        wm._has_xrandr = False

        info = WindowInfo(x=2000, y=100, width=800, height=600)
        assert wm._get_monitor_for_window(info) == 1

    def test_no_monitors_returns_zero(self):
        wm = WindowManager.__new__(WindowManager)
        wm._monitors = []
        wm._monitors_time = 0
        wm._monitors_cache_ttl = 30
        wm._has_xrandr = False

        info = WindowInfo(x=100, y=100, width=800, height=600)
        assert wm._get_monitor_for_window(info) == 0


# ── Display server detection ─────────────────────────────────────────

class TestDisplayServer:
    def test_detect_display_server(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        assert WindowManager._detect_display_server() == "x11"

    def test_detect_wayland(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        assert WindowManager._detect_display_server() == "wayland"
