"""
Process Scanner - analyze running processes, window positions, and map to screenshots.

Provides:
- List all visible windows with geometry and process info
- Map each window to its application/process
- Provide structured data for per-window screenshot cropping
- nfo-instrumented for automatic logging validation
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import structlog
import nfo

from window_aware import AppCategory, WindowInfo, WindowManager

logger = structlog.get_logger()


@dataclass
class ProcessInfo:
    """Information about a running process with a visible window."""
    pid: int
    name: str
    cmdline: str = ""
    cpu_percent: float = 0.0
    mem_rss_kb: int = 0
    user: str = ""

    def to_dict(self) -> Dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "cmdline": self.cmdline,
            "cpu_percent": self.cpu_percent,
            "mem_rss_kb": self.mem_rss_kb,
            "user": self.user,
        }


@dataclass
class VisibleWindow:
    """A visible window on screen with its process and geometry."""
    window_id: int = 0
    title: str = ""
    wm_class: str = ""
    wm_class_name: str = ""
    pid: int = 0
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    is_active: bool = False
    category: AppCategory = AppCategory.UNKNOWN
    process: Optional[ProcessInfo] = None
    stacking_order: int = 0

    def to_dict(self) -> Dict:
        return {
            "window_id": self.window_id,
            "title": self.title,
            "wm_class": self.wm_class,
            "wm_class_name": self.wm_class_name,
            "pid": self.pid,
            "geometry": {"x": self.x, "y": self.y, "w": self.width, "h": self.height},
            "is_active": self.is_active,
            "category": self.category.value,
            "process": self.process.to_dict() if self.process else None,
            "stacking_order": self.stacking_order,
        }

    @property
    def roi(self) -> Dict:
        """Region of interest for screenshot cropping."""
        return {
            "left": self.x,
            "top": self.y,
            "width": self.width,
            "height": self.height,
        }


class ProcessScanner:
    """
    Scans running processes and visible windows on the desktop.
    Uses wmctrl for window listing + /proc for process details.
    """

    # Compositor/service/helper windows that should not be treated as user work windows.
    _SERVICE_CLASS_PATTERNS = (
        r"mutter-x11-frames",
        r"gnome-shell",
        r"xwaylandvideobridge",
        r"jetbrains-toolbox",
        r"com-jetbrains-toolbox-entry",
        r"ai-desktop-assistant-overlay",
    )
    _SERVICE_TITLES = {
        "mutter guard window",
        "sun-awt-x11-xcanvaspeer",
        "content window",
    }

    def __init__(self, window_manager: Optional[WindowManager] = None):
        self.window_manager = window_manager
        self._has_wmctrl = self._check_tool("wmctrl")
        self._has_xdotool = self._check_tool("xdotool")
        self._has_xprop = self._check_tool("xprop")
        self._has_xwininfo = self._check_tool("xwininfo")

        # Stats
        self.total_scans = 0
        self.last_scan_time = 0.0
        self.last_windows: List[VisibleWindow] = []

        logger.info(
            "ProcessScanner initialized",
            wmctrl=self._has_wmctrl,
            xdotool=self._has_xdotool,
            xprop=self._has_xprop,
        )

    @staticmethod
    def _check_tool(name: str) -> bool:
        try:
            result = subprocess.run(["which", name], capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _run(cmd: List[str], timeout: float = 3.0) -> Optional[str]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:
            return None

    @nfo.log_call(level="INFO")
    def scan_all_windows(self) -> List[VisibleWindow]:
        """
        Scan all visible windows on the desktop.
        Returns list of VisibleWindow sorted by stacking order (topmost first).
        """
        self.total_scans += 1
        self.last_scan_time = time.time()

        windows = []

        # Get active window ID for marking
        active_wid = 0
        if self._has_xdotool:
            wid_str = self._run(["xdotool", "getactivewindow"])
            if wid_str:
                try:
                    active_wid = int(wid_str)
                except ValueError:
                    pass

        if self._has_wmctrl:
            windows = self._scan_via_wmctrl(active_wid)
        elif self._has_xdotool:
            windows = self._scan_via_xdotool(active_wid)

        # Drop compositor/helper/service windows before enrichment/cropping.
        before_filter = len(windows)
        windows = [w for w in windows if not self._is_service_window(w)]
        filtered_service_windows = before_filter - len(windows)

        # Enrich with process info
        for win in windows:
            if win.pid > 0:
                win.process = self._get_process_info(win.pid)
                # Re-classify with process info if available (improves detection for Electron apps, java, etc.)
                if win.process:
                    new_cat = WindowManager._classify_app(
                        win.wm_class, 
                        win.wm_class_name, 
                        win.title, 
                        win.process.name, 
                        win.process.cmdline
                    )
                    # If we got a better classification, update it
                    # (Don't overwrite if we had a specific one and got UNKNOWN, though _classify_app handles that well)
                    if new_cat != AppCategory.UNKNOWN:
                        win.category = new_cat
                    elif win.category == AppCategory.UNKNOWN:
                        # Even if new_cat is UNKNOWN, maybe we want to keep it? 
                        # But _classify_app is the source of truth now.
                        pass

        # Sort: active first, then by stacking order
        windows.sort(key=lambda w: (not w.is_active, w.stacking_order))

        self.last_windows = windows

        logger.info(
            "Window scan complete",
            total_windows=len(windows),
            active_wid=active_wid,
            filtered_service_windows=filtered_service_windows,
        )

        return windows

    @classmethod
    def _is_service_window_fields(cls, wm_class: str, wm_class_name: str, title: str) -> bool:
        """Heuristic guard for compositor/helper windows across monitors."""
        combined = f"{wm_class} {wm_class_name}".lower()
        title_lower = (title or "").strip().lower()

        if title_lower in cls._SERVICE_TITLES:
            return True

        return any(re.search(pattern, combined) for pattern in cls._SERVICE_CLASS_PATTERNS)

    @classmethod
    def _is_service_window(cls, win: VisibleWindow) -> bool:
        """Check if a VisibleWindow is a non-user service/compositor window."""
        return cls._is_service_window_fields(win.wm_class, win.wm_class_name, win.title)

    def _scan_via_wmctrl(self, active_wid: int) -> List[VisibleWindow]:
        """List windows using wmctrl -lGpx."""
        output = self._run(["wmctrl", "-lGpx"])
        if not output:
            return []

        windows = []
        for idx, line in enumerate(output.splitlines()):
            # wmctrl -lGpx format:
            # 0x04000003 -1 6432   0    0    1920 1080 Navigator.firefox  hostname Firefox
            parts = line.split(None, 9)
            if len(parts) < 10:
                continue

            try:
                wid = int(parts[0], 16)
                desktop = int(parts[1])
                pid = int(parts[2])
                x = int(parts[3])
                y = int(parts[4])
                w = int(parts[5])
                h = int(parts[6])
                wm_class_full = parts[7]
                # parts[8] = hostname
                title = parts[9] if len(parts) > 9 else ""
            except (ValueError, IndexError):
                continue

            # Skip desktop/panel windows
            if desktop == -1 and wm_class_full.lower() in ("n/a", "desktop_window"):
                continue

            # Parse WM_CLASS
            wm_class = ""
            wm_class_name = ""
            if "." in wm_class_full:
                wm_class, wm_class_name = wm_class_full.split(".", 1)
            else:
                wm_class_name = wm_class_full

            # Classify
            category = WindowManager._classify_app(wm_class, wm_class_name, title)

            win = VisibleWindow(
                window_id=wid,
                title=title,
                wm_class=wm_class,
                wm_class_name=wm_class_name,
                pid=pid,
                x=x,
                y=y,
                width=w,
                height=h,
                is_active=(wid == active_wid),
                category=category,
                stacking_order=idx,
            )
            windows.append(win)

        return windows

    def _scan_via_xdotool(self, active_wid: int) -> List[VisibleWindow]:
        """Fallback: list windows using xdotool search."""
        output = self._run(["xdotool", "search", "--onlyvisible", "--name", ""])
        if not output:
            return []

        windows = []
        for idx, wid_str in enumerate(output.splitlines()):
            try:
                wid = int(wid_str)
            except ValueError:
                continue

            win = self._build_xdotool_window(wid, idx, active_wid)
            if win.width > 50 and win.height > 50:
                windows.append(win)

        return windows

    def _build_xdotool_window(self, wid: int, idx: int, active_wid: int) -> VisibleWindow:
        """Query all properties for a single window via xdotool/xprop."""
        win = VisibleWindow(window_id=wid, stacking_order=idx)

        title = self._run(["xdotool", "getwindowname", str(wid)])
        if title:
            win.title = title

        self._parse_xdotool_geometry(win)
        self._parse_xdotool_pid(win)
        self._parse_xprop_wm_class(win)

        win.category = WindowManager._classify_app(win.wm_class, win.wm_class_name, win.title)
        win.is_active = (wid == active_wid)
        return win

    def _parse_xdotool_geometry(self, win: VisibleWindow):
        """Parse geometry from xdotool getwindowgeometry --shell."""
        geom = self._run(["xdotool", "getwindowgeometry", "--shell", str(win.window_id)])
        if not geom:
            return
        for gline in geom.splitlines():
            if gline.startswith("X="):
                win.x = int(gline.split("=")[1])
            elif gline.startswith("Y="):
                win.y = int(gline.split("=")[1])
            elif gline.startswith("WIDTH="):
                win.width = int(gline.split("=")[1])
            elif gline.startswith("HEIGHT="):
                win.height = int(gline.split("=")[1])

    def _parse_xdotool_pid(self, win: VisibleWindow):
        """Parse PID from xdotool getwindowpid."""
        pid_str = self._run(["xdotool", "getwindowpid", str(win.window_id)])
        if pid_str:
            try:
                win.pid = int(pid_str)
            except ValueError:
                pass

    def _parse_xprop_wm_class(self, win: VisibleWindow):
        """Parse WM_CLASS from xprop."""
        if not self._has_xprop:
            return
        xprop_out = self._run(["xprop", "-id", str(win.window_id), "WM_CLASS"])
        if xprop_out and "=" in xprop_out:
            match = re.search(r'"([^"]*)",\s*"([^"]*)"', xprop_out)
            if match:
                win.wm_class = match.group(1)
                win.wm_class_name = match.group(2)

    @staticmethod
    def _get_process_info(pid: int) -> Optional[ProcessInfo]:
        """Get process information from /proc."""
        try:
            # Read comm (process name)
            comm_path = f"/proc/{pid}/comm"
            name = ""
            if os.path.exists(comm_path):
                with open(comm_path) as f:
                    name = f.read().strip()

            # Read cmdline
            cmdline_path = f"/proc/{pid}/cmdline"
            cmdline = ""
            if os.path.exists(cmdline_path):
                with open(cmdline_path, "rb") as f:
                    raw = f.read()
                    cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()

            # Read status for memory
            status_path = f"/proc/{pid}/status"
            mem_rss_kb = 0
            if os.path.exists(status_path):
                with open(status_path) as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            parts = line.split()
                            if len(parts) >= 2:
                                mem_rss_kb = int(parts[1])
                            break

            # Read stat for user
            stat_path = f"/proc/{pid}/stat"
            user = ""
            try:
                import pwd
                uid = os.stat(f"/proc/{pid}").st_uid
                user = pwd.getpwuid(uid).pw_name
            except Exception:
                pass

            return ProcessInfo(
                pid=pid,
                name=name,
                cmdline=cmdline[:200],
                mem_rss_kb=mem_rss_kb,
                user=user,
            )
        except (OSError, FileNotFoundError, PermissionError):
            return None

    @nfo.log_call(level="INFO")
    def get_window_layout(self) -> Dict:
        """
        Get organized layout of all visible windows.
        Groups windows by category and provides spatial relationships.
        """
        windows = self.scan_all_windows()

        # Group by category
        by_category: Dict[str, List[Dict]] = {}
        for win in windows:
            cat = win.category.value
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(win.to_dict())

        # Compute screen bounds
        if windows:
            max_right = max(w.x + w.width for w in windows)
            max_bottom = max(w.y + w.height for w in windows)
        else:
            max_right = 0
            max_bottom = 0

        return {
            "timestamp": time.time(),
            "total_windows": len(windows),
            "active_window": next((w.to_dict() for w in windows if w.is_active), None),
            "by_category": by_category,
            "all_windows": [w.to_dict() for w in windows],
            "screen_bounds": {"width": max_right, "height": max_bottom},
        }

    def get_stats(self) -> Dict:
        return {
            "total_scans": self.total_scans,
            "last_scan_time": self.last_scan_time,
            "last_window_count": len(self.last_windows),
            "tools": {
                "wmctrl": self._has_wmctrl,
                "xdotool": self._has_xdotool,
                "xprop": self._has_xprop,
            },
        }


def create_process_scanner(window_manager: Optional[WindowManager] = None, settings=None) -> ProcessScanner:
    """Create ProcessScanner instance."""
    return ProcessScanner(window_manager=window_manager)


# Auto-log all functions in this module via nfo
nfo.auto_log()
