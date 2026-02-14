"""
Window Awareness module - X11/Wayland active window detection for Linux.

Provides:
- Active window title, class, PID
- Window geometry (position, size) for ROI capture
- Application classification (IDE, Terminal, Browser, Email, etc.)
- Git repository detection when in terminal/IDE
- Multi-monitor awareness
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import nfo
import structlog

logger = structlog.get_logger()


class AppCategory(str, Enum):
    """Application categories for per-app analysis modes."""
    IDE = "ide"
    TERMINAL = "terminal"
    BROWSER = "browser"
    EMAIL = "email"
    CHAT = "chat"
    OFFICE = "office"
    MEDIA = "media"
    GRAPHICS = "graphics"
    GAME = "game"
    FILE_MANAGER = "file_manager"
    SYSTEM = "system"
    UTILITY = "utility"
    UNKNOWN = "unknown"


# Application classification rules: (window_class_pattern, category)
APP_RULES: List[Tuple[str, AppCategory]] = [
    # Service/compositor/helper windows (must be before app-specific rules)
    (r"mutter-x11-frames|gnome-shell|xwaylandvideobridge", AppCategory.SYSTEM),
    (r"jetbrains-toolbox|com-jetbrains-toolbox-entry", AppCategory.SYSTEM),
    (r"proxeen-assistant-overlay", AppCategory.SYSTEM),
    (r"kwin_x11|kwin_wayland|plasmashell", AppCategory.SYSTEM),
    (r"dunst|notify-osd|xfce4-notifyd", AppCategory.SYSTEM),

    # IDEs & Editors
    (r"code|vscodium|codium", AppCategory.IDE),
    (r"jetbrains|idea|pycharm|webstorm|clion|goland|rider|datagrip|rubymine|phpstorm", AppCategory.IDE),
    (r"android-studio", AppCategory.IDE),
    (r"sublime_text|subl", AppCategory.IDE),
    (r"atom", AppCategory.IDE),
    (r"neovide|gvim", AppCategory.IDE),
    (r"emacs", AppCategory.IDE),
    (r"eclipse|netbeans|arduino", AppCategory.IDE),
    (r"kate|kwrite|geany|mousepad|leafpad|gedit|pluma|xed", AppCategory.IDE),
    (r"zed", AppCategory.IDE),
    (r"cursor", AppCategory.IDE),
    (r"windsurf", AppCategory.IDE),
    (r"godot|unity|unreal", AppCategory.IDE),
    (r"qtcreator", AppCategory.IDE),
    (r"postman|insomnia|dbeaver|pgadmin|mysql-workbench", AppCategory.IDE),

    # Terminals
    (r"gnome-terminal|konsole|xfce4-terminal|terminator|alacritty|kitty|wezterm|foot|tilix|\bst\b|urxvt|xterm|sakura|guake|yakuake|tilda|lxterminal|mate-terminal", AppCategory.TERMINAL),
    (r"cool-retro-term|hyper|blackbox", AppCategory.TERMINAL),

    # Browsers
    (r"firefox|navigator|chromium|chrome|google-chrome|brave|vivaldi|opera|edge|epiphany|midori|qutebrowser|\bmin\b", AppCategory.BROWSER),
    (r"tor-browser|waterfox|pale moon|librewolf|falkon", AppCategory.BROWSER),

    # Email
    (r"thunderbird|evolution|geary|kmail|\bmutt\b|\bneomutt\b|mailspring|bluemail|sylpheed|claws-mail", AppCategory.EMAIL),

    # Chat / Communication
    (r"slack|discord|telegram|signal|element|teams|zoom|skype|matrix|hexchat|weechat|irssi", AppCategory.CHAT),
    (r"whatsapp|viber|messenger|caprine|ferdi|rambox|franchise", AppCategory.CHAT),
    (r"mattermost|rocketchat", AppCategory.CHAT),

    # Office / Productivity
    (r"libreoffice|soffice|abiword|gnumeric|calligra|okular|evince|zathura|xreader|atril|mupdf", AppCategory.OFFICE),
    (r"obsidian|joplin|notion|evernote|standard notes|cherrytree|zim", AppCategory.OFFICE),
    (r"focuswriter|typora|marktext", AppCategory.OFFICE),

    # Graphics / Design
    (r"gimp|inkscape|krita|blender|darktable|rawtherapee|aseprite|mypaint", AppCategory.GRAPHICS),
    (r"figma|penpot", AppCategory.GRAPHICS),
    (r"freecad|kicad|librecad|openscad", AppCategory.GRAPHICS),

    # Media (Video/Audio)
    (r"vlc|mpv|totem|celluloid|rhythmbox|spotify|audacious|clementine|obs|kdenlive|shotcut|openshot|pitivi|audacity|ardour", AppCategory.MEDIA),
    (r"plex|kodi|stremio", AppCategory.MEDIA),

    # Games
    (r"steam|heroic|lutris|minigalaxy|itch", AppCategory.GAME),
    (r"minecraft|factorio|stardew|terraria|dota|csgo|tf2", AppCategory.GAME),
    (r"retroarch|dolphin-emu|pcsx2|rpcs3|yuzu|ryujinx", AppCategory.GAME),

    # File managers
    (r"nautilus|dolphin|thunar|pcmanfm|nemo|caja|ranger|nnn|mc|midnight commander|krusader", AppCategory.FILE_MANAGER),

    # System
    (r"systemsettings|gnome-control|xfce4-settings|lxappearance|pavucontrol|nm-connection-editor", AppCategory.SYSTEM),
    (r"gnome-system-monitor|ksysguard|htop|btop", AppCategory.SYSTEM),
    (r"virtualbox|vmware|virt-manager|qemu", AppCategory.SYSTEM),
    (r"gparted|disks|baobab", AppCategory.SYSTEM),
    (r"synaptic|pamac|discover|gnome-software", AppCategory.SYSTEM),

    # Utilities
    (r"calculator|gnome-calculator|kcalc|galculator", AppCategory.UTILITY),
    (r"flameshot|shutter|scrot", AppCategory.UTILITY),
    (r"keepass|bitwarden|1password|secrets", AppCategory.UTILITY),
]


SERVICE_WINDOW_CLASS_PATTERNS: Tuple[str, ...] = (
    r"mutter-x11-frames",
    r"gnome-shell",
    r"xwaylandvideobridge",
    r"jetbrains-toolbox",
    r"com-jetbrains-toolbox-entry",
    r"proxeen-assistant-overlay",
    r"kwin_x11",
    r"kwin_wayland",
    r"plasmashell",
    r"dunst",
    r"notify-osd",
    r"xfce4-notifyd",
)

SERVICE_WINDOW_TITLES = {
    "mutter guard window",
    "sun-awt-x11-xcanvaspeer",
    "content window",
}


# Process-based classification rules: (process_name_pattern, category)
PROCESS_RULES: List[Tuple[str, AppCategory]] = [
    # Electron wrappers often have generic window classes but specific process names
    (r"electron|electron\d+", AppCategory.UNKNOWN),  # Skip generic electron
    (r"slack", AppCategory.CHAT),
    (r"discord", AppCategory.CHAT),
    (r"code|vscode", AppCategory.IDE),
    (r"windsurf", AppCategory.IDE),
    (r"obsidian", AppCategory.OFFICE),
    (r"signal", AppCategory.CHAT),
    (r"steam", AppCategory.GAME),
    
    # Java apps
    (r"java", AppCategory.UNKNOWN),  # Generic java
    
    # Python apps
    (r"python|python3", AppCategory.UNKNOWN),
]


@dataclass
class WindowInfo:
    """Information about the currently active window."""
    window_id: int = 0
    title: str = ""
    wm_class: str = ""
    wm_class_name: str = ""
    pid: int = 0
    process_name: str = ""
    cmdline: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    monitor_index: int = 0
    category: AppCategory = AppCategory.UNKNOWN
    git_repo: str = ""
    git_branch: str = ""
    git_status: str = ""
    cwd: str = ""
    timestamp: float = 0.0
    extra: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "window_id": self.window_id,
            "title": self.title,
            "wm_class": self.wm_class,
            "wm_class_name": self.wm_class_name,
            "pid": self.pid,
            "process_name": self.process_name,
            "cmdline": self.cmdline,
            "geometry": {"x": self.x, "y": self.y, "w": self.width, "h": self.height},
            "monitor_index": self.monitor_index,
            "category": self.category.value,
            "git": {
                "repo": self.git_repo,
                "branch": self.git_branch,
                "status": self.git_status,
            } if self.git_repo else None,
            "cwd": self.cwd,
            "timestamp": self.timestamp,
            "extra": self.extra if self.extra else None,
        }

    def to_context_string(self) -> str:
        """Format window info as context string for LLM prompt injection."""
        parts = [f"🪟 Aktywne okno: {self.title}"]
        app_name = self.wm_class_name or self.process_name or "Unknown"
        parts.append(f"📂 Aplikacja: {app_name} ({self.category.value})")
        if self.cwd:
            parts.append(f"📁 CWD: {self.cwd}")
        if self.git_repo:
            parts.append(f"🔀 Git: {self.git_branch} @ {self.git_repo}")
            if self.git_status:
                parts.append(f"📋 Git status: {self.git_status}")
        return "\n".join(parts)


@dataclass
class MonitorInfo:
    """Information about a connected monitor."""
    index: int
    name: str
    x: int
    y: int
    width: int
    height: int
    is_primary: bool = False

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "is_primary": self.is_primary,
        }


class WindowManager:
    """
    Linux window manager integration via xdotool, xprop, xrandr.
    Falls back gracefully if tools are unavailable.
    """

    def __init__(
        self,
        enable_git: bool = True,
        git_timeout: float = 2.0,
        cache_ttl: float = 0.5,
    ):
        self.enable_git = enable_git
        self.git_timeout = git_timeout
        self.cache_ttl = cache_ttl

        # Tool availability
        self._has_xdotool = self._check_tool("xdotool")
        self._has_xprop = self._check_tool("xprop")
        self._has_xrandr = self._check_tool("xrandr")
        self._has_wmctrl = self._check_tool("wmctrl")

        # Cache
        self._last_window: Optional[WindowInfo] = None
        self._last_window_time: float = 0
        self._monitors: List[MonitorInfo] = []
        self._monitors_time: float = 0
        self._monitors_cache_ttl: float = 30.0  # monitors change rarely

        # Stats
        self.total_queries = 0
        self.cache_hits = 0
        self.errors = 0

        # Display server type
        self._display_server = self._detect_display_server()

        logger.info(
            "WindowManager initialized",
            display_server=self._display_server,
            xdotool=self._has_xdotool,
            xprop=self._has_xprop,
            xrandr=self._has_xrandr,
            wmctrl=self._has_wmctrl,
            git=enable_git,
        )

    @staticmethod
    def _check_tool(name: str) -> bool:
        """Check if a CLI tool is available."""
        try:
            result = subprocess.run(
                ["which", name],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _detect_display_server() -> str:
        """Detect X11 vs Wayland."""
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session_type == "wayland":
            return "wayland"
        if session_type == "x11":
            return "x11"
        # Fallback: check WAYLAND_DISPLAY
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"
        if os.environ.get("DISPLAY"):
            return "x11"
        return "unknown"

    @staticmethod
    def _run(cmd: List[str], timeout: float = 3.0) -> Optional[str]:
        """Run a command and return stdout, or None on error."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:
            return None

    def get_active_window(self) -> WindowInfo:
        """
        Get comprehensive info about the currently active window.
        Uses caching to avoid excessive subprocess calls.
        """
        now = time.time()
        self.total_queries += 1

        # Return cached if fresh enough
        if self._last_window and (now - self._last_window_time) < self.cache_ttl:
            self.cache_hits += 1
            return self._last_window

        info = WindowInfo(timestamp=now)

        try:
            info.window_id = self._query_window_id()
            if info.window_id == 0:
                return self._cache_and_return(info, now)

            self._query_window_props(info)
            self._enrich_window(info)
        except Exception as e:
            self.errors += 1
            logger.error("Window detection failed", error=str(e))

        return self._cache_and_return(info, now)

    def _query_window_id(self) -> int:
        """Get user work window ID (cursor window preferred, focus as fallback)."""
        if not self._has_xdotool:
            return 0

        cursor_wid = self._query_mouse_window_id()
        focused_wid = self._query_active_window_id()
        cursor_is_service = self._is_service_window_id(cursor_wid) if cursor_wid else False
        focused_is_service = self._is_service_window_id(focused_wid) if focused_wid else False

        if cursor_wid and cursor_wid != focused_wid and not cursor_is_service:
            return cursor_wid
        if focused_wid and not focused_is_service:
            return focused_wid
        if cursor_wid and not cursor_is_service:
            return cursor_wid

        return 0

    def _query_active_window_id(self) -> int:
        """Get focused window ID via xdotool."""
        return self._parse_window_id(self._run(["xdotool", "getactivewindow"]))

    def _query_mouse_window_id(self) -> int:
        """Get window ID currently under mouse cursor via xdotool."""
        output = self._run(["xdotool", "getmouselocation", "--shell"])
        if not output:
            return 0

        for line in output.splitlines():
            if line.startswith("WINDOW="):
                return self._parse_window_id(line.split("=", 1)[1])

        return 0

    @staticmethod
    def _parse_window_id(value: Optional[str]) -> int:
        """Parse decimal/hex window id string safely."""
        if not value:
            return 0

        try:
            return int(str(value).strip(), 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _is_service_window_fields(cls, wm_class: str, wm_class_name: str, title: str) -> bool:
        """Guard for compositor/helper windows that should not be treated as user work windows."""
        combined = f"{wm_class or ''} {wm_class_name or ''}".lower()
        title_lower = (title or "").strip().lower()

        if title_lower in SERVICE_WINDOW_TITLES:
            return True

        return any(re.search(pattern, combined) for pattern in SERVICE_WINDOW_CLASS_PATTERNS)

    def _is_service_window_id(self, window_id: int) -> bool:
        """Check whether a window id corresponds to compositor/helper overlay windows."""
        if window_id <= 0:
            return False

        title = self._run(["xdotool", "getwindowname", str(window_id)]) or ""
        wm_class = ""
        wm_class_name = ""

        if self._has_xprop:
            xprop_out = self._run(["xprop", "-id", str(window_id), "WM_CLASS"])
            if xprop_out and "=" in xprop_out:
                match = re.search(r'"([^"]*)",\s*"([^"]*)"', xprop_out)
                if match:
                    wm_class = match.group(1)
                    wm_class_name = match.group(2)

        return self._is_service_window_fields(wm_class, wm_class_name, title)

    def _query_window_props(self, info: WindowInfo):
        """Query title, geometry, PID, and WM_CLASS for a window."""
        wid = str(info.window_id)

        # Title
        title = self._run(["xdotool", "getwindowname", wid])
        if title:
            info.title = title

        # Geometry
        geom = self._run(["xdotool", "getwindowgeometry", "--shell", wid])
        if geom:
            self._parse_geometry(info, geom)

        # PID
        pid_str = self._run(["xdotool", "getwindowpid", wid])
        if pid_str:
            info.pid = int(pid_str)

        # WM_CLASS
        if self._has_xprop:
            self._query_wm_class(info)

    @staticmethod
    def _parse_geometry(info: WindowInfo, geom: str):
        """Parse xdotool --shell geometry output into WindowInfo fields."""
        for line in geom.splitlines():
            if line.startswith("X="):
                info.x = int(line.split("=")[1])
            elif line.startswith("Y="):
                info.y = int(line.split("=")[1])
            elif line.startswith("WIDTH="):
                info.width = int(line.split("=")[1])
            elif line.startswith("HEIGHT="):
                info.height = int(line.split("=")[1])

    def _query_wm_class(self, info: WindowInfo):
        """Get WM_CLASS instance and class name via xprop."""
        xprop_out = self._run(["xprop", "-id", str(info.window_id), "WM_CLASS"])
        if xprop_out and "=" in xprop_out:
            match = re.search(r'"([^"]*)",\s*"([^"]*)"', xprop_out)
            if match:
                info.wm_class = match.group(1)
                info.wm_class_name = match.group(2)

    def _enrich_window(self, info: WindowInfo):
        """Classify app, resolve CWD, add git context, detect monitor."""
        if info.pid > 0:
            info.cwd = self._get_cwd(info.pid)
            info.process_name, info.cmdline = self._get_process_details(info.pid)

        info.category = self._classify_app(
            info.wm_class, 
            info.wm_class_name, 
            info.title,
            info.process_name,
            info.cmdline
        )

        if self.enable_git and info.category in (AppCategory.IDE, AppCategory.TERMINAL):
            self._enrich_git(info)

        info.monitor_index = self._get_monitor_for_window(info)

    def _get_process_details(self, pid: int) -> Tuple[str, str]:
        """Get process name and cmdline from /proc."""
        name = ""
        cmdline = ""
        try:
            comm_path = f"/proc/{pid}/comm"
            if os.path.exists(comm_path):
                with open(comm_path, "r") as f:
                    name = f.read().strip()
            
            cmdline_path = f"/proc/{pid}/cmdline"
            if os.path.exists(cmdline_path):
                with open(cmdline_path, "rb") as f:
                    # cmdline is null-separated, replace with spaces
                    raw = f.read()
                    cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except (OSError, FileNotFoundError, PermissionError):
            pass
        return name, cmdline

    def _cache_and_return(self, info: WindowInfo, now: float) -> WindowInfo:
        self._last_window = info
        self._last_window_time = now
        return info

    @staticmethod
    def _classify_app(
        wm_class: str, 
        wm_class_name: str, 
        title: str,
        process_name: str = "",
        cmdline: str = ""
    ) -> AppCategory:
        """Classify application based on WM_CLASS, title, and process info."""
        # Combine class fields for matching
        combined = f"{wm_class or ''} {wm_class_name or ''}".lower()
        title_lower = (title or "").lower()
        process_lower = (process_name or "").lower()

        # Helper/compositor titles that should never be treated as user apps
        if title_lower in SERVICE_WINDOW_TITLES:
            return AppCategory.SYSTEM

        # 1. Check WM_CLASS rules (most reliable for native apps)
        for pattern, category in APP_RULES:
            if re.search(pattern, combined):
                return category

        # 2. Check Process Name rules (reliable for known binaries)
        if process_lower:
            for pattern, category in PROCESS_RULES:
                if re.search(pattern, process_lower):
                    if category != AppCategory.UNKNOWN:
                        return category
                    # If UNKNOWN (e.g. generic electron), we might find more info in cmdline
        
        # 3. Check Command Line (useful for java -jar, python scripts, electron apps)
        if cmdline:
            cmd_lower = cmdline.lower()
            # Detect specific java apps
            if any(kw in cmd_lower for kw in ["jetbrains", "idea", "pycharm", "webstorm", "clion", "goland", "rider", "datagrip", "rubymine", "phpstorm"]):
                return AppCategory.IDE
            if "minecraft" in cmd_lower:
                return AppCategory.GAME
            # Detect electron apps via path
            if "discord" in cmd_lower:
                return AppCategory.CHAT
            if "slack" in cmd_lower:
                return AppCategory.CHAT
            if "obsidian" in cmd_lower:
                return AppCategory.OFFICE
            if "cursor" in cmd_lower:
                return AppCategory.IDE
            if "windsurf" in cmd_lower:
                return AppCategory.IDE

        # 4. Fallback: check title for clues
        if any(kw in title_lower for kw in ["vim", "nvim", "nano", "helix", "windsurf", "cursor"]):
            return AppCategory.IDE
        if any(kw in title_lower for kw in ["bash", "zsh", "fish", "sh -"]):
            return AppCategory.TERMINAL
        if "mozilla firefox" in title_lower or "google chrome" in title_lower:
            return AppCategory.BROWSER

        return AppCategory.UNKNOWN

    @staticmethod
    def _get_cwd(pid: int) -> str:
        """Get current working directory of a process via /proc."""
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            return cwd
        except (OSError, FileNotFoundError):
            return ""

    def _enrich_git(self, info: WindowInfo):
        """Add git repo info if the window's CWD is inside a git repo."""
        cwd = info.cwd
        if not cwd:
            # Try to infer CWD from window title (many terminals show it)
            cwd = self._extract_path_from_title(info.title)
            if not cwd:
                return

        # Check if inside git repo
        git_dir = self._run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            timeout=self.git_timeout,
        )
        if not git_dir:
            return

        info.git_repo = os.path.basename(git_dir)

        # Get branch
        branch = self._run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            timeout=self.git_timeout,
        )
        if branch:
            info.git_branch = branch

        # Get short status
        status = self._run(
            ["git", "-C", cwd, "status", "--short", "--branch", "--porcelain=v2"],
            timeout=self.git_timeout,
        )
        if status:
            # Summarize: count modified, added, deleted
            lines = status.strip().splitlines()
            changed = sum(1 for l in lines if l.startswith("1 ") or l.startswith("2 "))
            untracked = sum(1 for l in lines if l.startswith("? "))
            parts = []
            if changed:
                parts.append(f"{changed} changed")
            if untracked:
                parts.append(f"{untracked} untracked")
            info.git_status = ", ".join(parts) if parts else "clean"

    @staticmethod
    def _extract_path_from_title(title: str) -> str:
        """Try to extract a file path from window title."""
        # Common patterns: "~/projects/foo", "/home/user/foo", "user@host:~/foo"
        patterns = [
            r"(~[/\\]\S+)",                      # ~/path
            r"(/home/\S+)",                       # /home/user/path
            r"(/[a-zA-Z][a-zA-Z0-9_/.-]+)",      # /absolute/path
            r"\w+@\w+:(~?/?\S+)",                 # user@host:path
        ]
        for pattern in patterns:
            match = re.search(pattern, title)
            if match:
                path = match.group(1)
                path = os.path.expanduser(path)
                if os.path.isdir(path):
                    return path
                # Try parent directory
                parent = os.path.dirname(path)
                if os.path.isdir(parent):
                    return parent
        return ""

    def get_monitors(self) -> List[MonitorInfo]:
        """Get list of connected monitors via xrandr."""
        now = time.time()
        if self._monitors and (now - self._monitors_time) < self._monitors_cache_ttl:
            return self._monitors

        monitors = []

        if not self._has_xrandr:
            return monitors

        output = self._run(["xrandr", "--query"])
        if not output:
            return monitors

        # Parse xrandr output
        idx = 0
        for line in output.splitlines():
            # Match: "DP-1 connected primary 2560x1440+0+0 ..."
            match = re.match(
                r"(\S+)\s+connected\s+(primary\s+)?(\d+)x(\d+)\+(\d+)\+(\d+)",
                line,
            )
            if match:
                monitors.append(MonitorInfo(
                    index=idx,
                    name=match.group(1),
                    width=int(match.group(3)),
                    height=int(match.group(4)),
                    x=int(match.group(5)),
                    y=int(match.group(6)),
                    is_primary=bool(match.group(2)),
                ))
                idx += 1

        self._monitors = monitors
        self._monitors_time = now

        logger.debug("Monitors detected", count=len(monitors),
                     names=[m.name for m in monitors])
        return monitors

    def _get_monitor_for_window(self, info: WindowInfo) -> int:
        """Determine which monitor a window center falls on."""
        monitors = self.get_monitors()
        if not monitors:
            return 0

        cx = info.x + info.width // 2
        cy = info.y + info.height // 2

        for mon in monitors:
            if (mon.x <= cx < mon.x + mon.width and
                    mon.y <= cy < mon.y + mon.height):
                return mon.index

        return 0

    def get_window_roi(self, info: Optional[WindowInfo] = None) -> Optional[Dict]:
        """
        Get Region of Interest based on active window geometry.
        Returns bounding box dict for capture cropping.
        """
        if info is None:
            info = self.get_active_window()

        if info.width == 0 or info.height == 0:
            return None

        return {
            "left": info.x,
            "top": info.y,
            "width": info.width,
            "height": info.height,
            "monitor": info.monitor_index,
        }

    def get_stats(self) -> Dict:
        """Get WindowManager statistics."""
        return {
            "display_server": self._display_server,
            "tools": {
                "xdotool": self._has_xdotool,
                "xprop": self._has_xprop,
                "xrandr": self._has_xrandr,
                "wmctrl": self._has_wmctrl,
            },
            "monitors": len(self.get_monitors()),
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": (
                f"{(self.cache_hits / self.total_queries * 100):.1f}%"
                if self.total_queries > 0 else "0%"
            ),
            "errors": self.errors,
            "git_enabled": self.enable_git,
            "last_window": self._last_window.to_dict() if self._last_window else None,
        }


@nfo.log_call(level="INFO")
def create_window_manager_from_env(settings=None) -> WindowManager:
    """Create WindowManager from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return WindowManager(
        enable_git=settings.enable_git_context,
        git_timeout=settings.git_timeout,
        cache_ttl=settings.window_cache_ttl,
    )
