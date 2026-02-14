"""
Screen capture module with intelligent change detection.
Supports X11 (mss), Wayland portal (PipeWire/GStreamer), and grim backends.
Preserves native aspect ratio regardless of screen orientation.
"""
import mss
import imagehash
import numpy as np
from PIL import Image
from io import BytesIO
import base64
import json
import time
import subprocess
import shutil
import tempfile
import signal as sig
import re
from typing import Optional, Dict, List
import structlog
import os

logger = structlog.get_logger()

# Path where wayland_screencast.py saves frames
WAYLAND_FRAME_PATH = "/tmp/aidesk_wayland_frame.jpg"
WAYLAND_STATUS_PATH = WAYLAND_FRAME_PATH + ".status"

# System python3 — must bypass conda/venv to reach system PyGObject (gi)
SYSTEM_PYTHON3 = "/usr/bin/python3"


def _detect_backend() -> str:
    """Detect the best screen capture backend for the current session."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")

    if session_type == "wayland" or wayland_display:
        # Option 1: grim (wlroots compositors like Sway)
        if shutil.which("grim"):
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
                    result = subprocess.run(
                        ["grim", tmp.name], capture_output=True, timeout=3
                    )
                    if result.returncode == 0:
                        return "grim"
            except Exception:
                pass

        # Option 2: Wayland portal (GNOME, KDE — uses PipeWire + GStreamer)
        try:
            result = subprocess.run(
                [SYSTEM_PYTHON3, "-c", "import gi; gi.require_version('Gst','1.0')"],
                capture_output=True, timeout=3,
            )
            if result.returncode == 0:
                return "wayland_portal"
        except Exception:
            pass

        logger.warning(
            "Wayland detected but no working capture backend found, falling back to mss"
        )
    return "mss"


def _resize_preserve_aspect(img: Image.Image, max_dim: int) -> Image.Image:
    """
    Resize image so longest side = max_dim, preserving aspect ratio.
    Works correctly for any orientation (landscape, portrait, square).
    """
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    if w >= h:
        new_w = max_dim
        new_h = int(h * max_dim / w)
    else:
        new_h = max_dim
        new_w = int(w * max_dim / h)
    # Ensure dimensions are at least 2 and even
    new_w = max(2, new_w - (new_w % 2))
    new_h = max(2, new_h - (new_h % 2))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


class SmartScreenCapture:
    """
    Captures screen with intelligent change detection to minimize API calls.
    """

    def __init__(
        self,
        change_threshold: int = 8,
        min_interval: float = 1.0,
        idle_threshold: int = 30,
        idle_interval: float = 10.0,
        max_dimension: int = 1280,
        jpeg_quality: int = 60,
        captures_dir: str = "/tmp/aidesk_captures",
        save_to_disk: bool = True,
    ):
        """
        Initialize screen capture.

        Args:
            change_threshold: Hamming distance threshold for detecting changes (1-20)
            min_interval: Minimum seconds between captures
            idle_threshold: Number of unchanged frames before entering idle mode
            idle_interval: Capture interval when idle (seconds)
            max_dimension: Max pixels for longest side (aspect ratio preserved)
            jpeg_quality: JPEG compression quality (1-100)
            captures_dir: Directory to save debug screenshots
            save_to_disk: Whether to save captures to disk (False = reduce I/O)
        """
        self.backend = _detect_backend()
        self.sct = mss.mss() if self.backend == "mss" else None
        self.last_hash = None
        self.last_capture_time = 0
        self.change_threshold = change_threshold
        self.min_interval = min_interval
        self.idle_threshold = idle_threshold
        self.idle_interval = idle_interval
        self.max_dimension = max_dimension
        self.jpeg_quality = jpeg_quality
        self.captures_dir = captures_dir
        self.save_to_disk = save_to_disk
        self.consecutive_unchanged = 0
        self.total_captures = 0
        self.changes_detected = 0
        self._screencast_proc = None
        self._native_size = (0, 0)
        self._last_resized_image = None  # PIL Image — avoids base64 re-decode downstream

        # Legacy compat
        self.screen_width = max_dimension
        self.screen_height = max_dimension

        # Ensure captures directory exists
        if self.captures_dir:
            os.makedirs(self.captures_dir, exist_ok=True)

        # Start wayland screencast daemon if needed
        if self.backend == "wayland_portal":
            self._start_screencast_daemon()

        logger.info(
            "Screen capture initialized",
            backend=self.backend,
            threshold=change_threshold,
            interval=min_interval,
            max_dimension=max_dimension,
            captures_dir=captures_dir,
        )

    def capture(
        self,
        monitor_index: Optional[int] = None,
        roi: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        Capture screen and return if meaningful change detected.

        Args:
            monitor_index: Monitor to capture (None = primary, 0 = all, 1+ = specific)
            roi: Region of Interest dict with keys: left, top, width, height

        Returns:
            Dict with image data if change detected, None otherwise
        """
        now = time.time()
        current_interval = self.adaptive_interval

        # Rate limiting
        if now - self.last_capture_time < current_interval:
            return None

        self.total_captures += 1

        try:
            img = self._grab_screen(monitor_index=monitor_index, roi=roi)

            # Track native resolution
            self._native_size = img.size

            # Resize preserving aspect ratio (works for any orientation)
            img_resized = _resize_preserve_aspect(img, self.max_dimension)

            # Perceptual hash for change detection
            current_hash = imagehash.phash(img_resized, hash_size=8)

            # Check for changes
            hash_diff = 0
            if self.last_hash is not None:
                hash_diff = current_hash - self.last_hash
                if hash_diff < self.change_threshold:
                    self.consecutive_unchanged += 1
                    logger.debug(
                        "No significant change detected",
                        hash_diff=hash_diff,
                        threshold=self.change_threshold,
                        consecutive_unchanged=self.consecutive_unchanged,
                    )
                    return None

            # Change detected or first capture
            self.last_hash = current_hash
            self.last_capture_time = now
            prev_unchanged = self.consecutive_unchanged
            self.consecutive_unchanged = 0
            self.changes_detected += 1

            # Store resized image for downstream pipeline steps (avoids base64 re-decode)
            self._last_resized_image = img_resized

            # Encode as JPEG
            buffer = BytesIO()
            img_resized.save(buffer, format="JPEG", quality=self.jpeg_quality, optimize=True)
            
            # Save to disk if configured (controlled by SAVE_CAPTURES env)
            if self.save_to_disk and self.captures_dir:
                filename = f"capture_{int(now)}.jpg"
                filepath = os.path.join(self.captures_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(buffer.getvalue())
                logger.debug("Screenshot saved to disk", path=filepath)

            b64 = base64.b64encode(buffer.getvalue()).decode()
            size_kb = len(buffer.getvalue()) / 1024
            resized_w, resized_h = img_resized.size

            logger.info(
                "Screen change detected",
                size_kb=round(size_kb, 1),
                native=f"{img.size[0]}x{img.size[1]}",
                resized=f"{resized_w}x{resized_h}",
                idle_frames=prev_unchanged,
                detection_rate=f"{(self.changes_detected / self.total_captures * 100):.1f}%",
            )

            # Path to full-resolution original frame (for high-quality cropping/OCR)
            fullscreen_path = None
            if self.backend == "wayland_portal" and os.path.exists(WAYLAND_FRAME_PATH):
                fullscreen_path = WAYLAND_FRAME_PATH

            return {
                "image_b64": b64,
                "timestamp": now,
                "resolution": (resized_w, resized_h),
                "native_resolution": img.size,
                "size_kb": size_kb,
                "hash_diff": hash_diff,
                "monitor_index": monitor_index,
                "roi": roi,
                "fullscreen_path": fullscreen_path,
            }

        except Exception as e:
            logger.error("Screen capture failed", error=str(e))
            return None

    @property
    def adaptive_interval(self) -> float:
        """
        Adaptive polling interval based on screen activity.
        
        Returns:
            Current polling interval in seconds
        """
        if self.consecutive_unchanged > self.idle_threshold:
            return self.idle_interval  # Idle mode
        return self.min_interval  # Active mode

    # ── Screencast daemon management ──────────────────────────────────

    def _start_screencast_daemon(self):
        """Start the wayland_screencast.py daemon (uses system python3 + gi)."""
        helper_path = os.path.join(os.path.dirname(__file__), "wayland_screencast.py")
        if not os.path.exists(helper_path):
            logger.error("wayland_screencast.py not found", path=helper_path)
            self.backend = "mss"
            self.sct = mss.mss()
            return

        env = os.environ.copy()
        env["AIDESK_FRAME_PATH"] = WAYLAND_FRAME_PATH
        env["AIDESK_FRAME_QUALITY"] = str(self.jpeg_quality)
        env["AIDESK_MAX_FPS"] = str(max(0.5, 1.0 / self.min_interval))

        self._screencast_proc = subprocess.Popen(
            [SYSTEM_PYTHON3, helper_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setpgrp,
        )
        logger.info(
            "Wayland screencast daemon started",
            pid=self._screencast_proc.pid,
            frame_path=WAYLAND_FRAME_PATH,
        )

        # Wait for first frame (user may need to grant permission via dialog)
        for _ in range(60):
            time.sleep(1)
            if os.path.exists(WAYLAND_FRAME_PATH):
                age = time.time() - os.path.getmtime(WAYLAND_FRAME_PATH)
                if age < 5:
                    logger.info("Wayland screencast producing frames")
                    return
            if self._screencast_proc.poll() is not None:
                stdout = self._screencast_proc.stdout.read().decode(errors="replace")[-500:]
                logger.error("Wayland screencast daemon died", output=stdout)
                self._screencast_proc = None
                self.backend = "mss"
                self.sct = mss.mss()
                return

        logger.warning("Wayland screencast timed out, falling back to mss")
        self._stop_screencast_daemon()
        self.backend = "mss"
        self.sct = mss.mss()

    def _stop_screencast_daemon(self):
        """Stop the wayland screencast daemon."""
        if self._screencast_proc and self._screencast_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._screencast_proc.pid), sig.SIGTERM)
                self._screencast_proc.wait(timeout=5)
            except Exception:
                try:
                    self._screencast_proc.kill()
                except Exception:
                    pass
            logger.info("Wayland screencast daemon stopped")
        self._screencast_proc = None

    # ── Backend-specific grab methods ─────────────────────────────────

    def _grab_screen(self, monitor_index: Optional[int] = None, roi: Optional[Dict] = None) -> Image.Image:
        """
        Grab screen using the detected backend.

        Returns:
            PIL Image of the captured screen
        """
        if self.backend == "wayland_portal":
            return self._grab_wayland_portal(roi=roi)
        elif self.backend == "grim":
            return self._grab_grim(roi=roi)
        else:
            return self._grab_mss(monitor_index=monitor_index, roi=roi)

    def _grab_mss(self, monitor_index: Optional[int] = None, roi: Optional[Dict] = None) -> Image.Image:
        """Capture using mss (X11)."""
        if roi:
            monitor = {
                "left": roi["left"],
                "top": roi["top"],
                "width": roi["width"],
                "height": roi["height"],
            }
        elif monitor_index is not None and 0 <= monitor_index < len(self.sct.monitors):
            monitor = self.sct.monitors[monitor_index]
        else:
            monitor = self.sct.monitors[1]

        raw = self.sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def _grab_grim(self, roi: Optional[Dict] = None) -> Image.Image:
        """Capture using grim (wlroots Wayland)."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = ["grim"]
            if roi:
                geometry = f"{roi['left']},{roi['top']} {roi['width']}x{roi['height']}"
                cmd += ["-g", geometry]
            cmd.append(tmp_path)

            subprocess.run(cmd, check=True, capture_output=True, timeout=5)
            img = Image.open(tmp_path).convert("RGB")
            return img
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _grab_wayland_portal(self, roi: Optional[Dict] = None) -> Image.Image:
        """Read latest frame from wayland_screencast.py daemon."""
        if not os.path.exists(WAYLAND_FRAME_PATH):
            raise RuntimeError("Wayland frame not available (screencast daemon not running?)")

        age = time.time() - os.path.getmtime(WAYLAND_FRAME_PATH)
        if age > 10:
            logger.warning("Wayland frame is stale", age_seconds=round(age, 1))

        img = Image.open(WAYLAND_FRAME_PATH).convert("RGB")

        # Apply ROI crop if specified
        if roi:
            box = (
                roi["left"], roi["top"],
                roi["left"] + roi["width"], roi["top"] + roi["height"],
            )
            img = img.crop(box)

        return img

    # ── Monitor detection ─────────────────────────────────────────────

    def get_monitors(self) -> List[Dict]:
        """Get list of available monitors."""
        if self.backend in ("grim", "wayland_portal"):
            return self._get_monitors_xrandr()
        if not self.sct:
            return []
        monitors = []
        for i, mon in enumerate(self.sct.monitors):
            monitors.append({
                "index": i,
                "left": mon["left"],
                "top": mon["top"],
                "width": mon["width"],
                "height": mon["height"],
                "is_combined": i == 0,
            })
        return monitors

    def _get_monitors_xrandr(self) -> List[Dict]:
        """Get monitors via xrandr (works on both X11 and Wayland)."""
        try:
            result = subprocess.run(
                ["xrandr", "--query"], capture_output=True, text=True, timeout=3
            )
            monitors = []
            for line in result.stdout.splitlines():
                m = re.match(r"(\S+) connected.*?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                if m:
                    monitors.append({
                        "index": len(monitors),
                        "name": m.group(1),
                        "width": int(m.group(2)),
                        "height": int(m.group(3)),
                        "left": int(m.group(4)),
                        "top": int(m.group(5)),
                        "is_combined": False,
                    })
            return monitors if monitors else [{"index": 0}]
        except Exception:
            return [{"index": 0}]

    # ── ROI capture ───────────────────────────────────────────────────

    def capture_roi_image(self, roi: Dict) -> Optional[str]:
        """
        Capture a specific screen region and return base64 JPEG.
        Useful for window-focused capture.

        Args:
            roi: Dict with left, top, width, height

        Returns:
            Base64-encoded JPEG string, or None on error
        """
        try:
            img = self._grab_screen(roi=roi)
            img_resized = _resize_preserve_aspect(img, self.max_dimension)
            buffer = BytesIO()
            img_resized.save(buffer, format="JPEG", quality=self.jpeg_quality, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode()
        except Exception as e:
            logger.error("ROI capture failed", error=str(e), roi=roi)
            return None

    # ── Stats & lifecycle ─────────────────────────────────────────────

    def get_screencast_status(self) -> Optional[Dict]:
        """Get wayland screencast daemon status (if running)."""
        if self.backend != "wayland_portal":
            return None
        try:
            if os.path.exists(WAYLAND_STATUS_PATH):
                with open(WAYLAND_STATUS_PATH) as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def get_stats(self) -> Dict:
        """Get capture statistics."""
        stats = {
            "total_captures": self.total_captures,
            "changes_detected": self.changes_detected,
            "detection_rate": (
                f"{(self.changes_detected / self.total_captures * 100):.1f}%"
                if self.total_captures > 0
                else "0%"
            ),
            "consecutive_unchanged": self.consecutive_unchanged,
            "current_interval": self.adaptive_interval,
            "is_idle": self.consecutive_unchanged > self.idle_threshold,
            "backend": self.backend,
            "native_resolution": f"{self._native_size[0]}x{self._native_size[1]}",
            "max_dimension": self.max_dimension,
            "monitors": len(self.sct.monitors) if self.sct else 0,
        }
        sc_status = self.get_screencast_status()
        if sc_status:
            stats["screencast"] = sc_status
        return stats

    def __del__(self):
        """Cleanup screencast daemon on garbage collection."""
        try:
            self._stop_screencast_daemon()
        except Exception:
            pass


# Configuration from environment
def create_capture_from_env(settings=None) -> SmartScreenCapture:
    """Create SmartScreenCapture from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return SmartScreenCapture(
        change_threshold=settings.change_threshold,
        min_interval=settings.min_capture_interval,
        idle_threshold=settings.idle_threshold,
        idle_interval=settings.idle_interval,
        max_dimension=settings.max_dimension,
        jpeg_quality=settings.jpeg_quality,
        captures_dir=settings.captures_dir,
        save_to_disk=settings.save_captures,
    )
