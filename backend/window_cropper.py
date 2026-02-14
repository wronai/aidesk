"""
Window Cropper - crop individual application regions from fullscreen screenshots.

Provides:
- Crop each visible window from a fullscreen screenshot
- Associate cropped regions with specific applications/processes
- Organize crops by category and priority
- Save individual per-app screenshots
- nfo-instrumented for automatic logging validation
"""
import base64
import os
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import imagehash
import structlog
from PIL import Image
import nfo

from process_scanner import VisibleWindow, ProcessScanner
from window_aware import AppCategory

logger = structlog.get_logger()


@dataclass
class CroppedWindow:
    """A cropped screenshot region associated with a specific application."""
    window: VisibleWindow
    image_b64: str = ""
    width: int = 0
    height: int = 0
    size_kb: float = 0.0
    crop_timestamp: float = 0.0
    filepath: str = ""
    change_score: float = 0.0   # 0 = no change, higher = more change vs previous
    is_focus: bool = False       # True if this is the detected work-focus window

    def to_dict(self) -> Dict:
        return {
            "window": self.window.to_dict(),
            "image_b64_length": len(self.image_b64),
            "width": self.width,
            "height": self.height,
            "size_kb": round(self.size_kb, 1),
            "crop_timestamp": self.crop_timestamp,
            "filepath": self.filepath,
            "change_score": round(self.change_score, 1),
            "is_focus": self.is_focus,
        }


@dataclass
class OrganizedScreenData:
    """
    Organized collection of all visible applications on screen.
    Priority: active window first, then by category importance.
    """
    timestamp: float = 0.0
    total_windows: int = 0
    active_app: Optional[CroppedWindow] = None
    focus_window: Optional[CroppedWindow] = None  # detected work area (most change)
    changed_windows: List[CroppedWindow] = field(default_factory=list)
    crops: List[CroppedWindow] = field(default_factory=list)
    by_category: Dict[str, List[CroppedWindow]] = field(default_factory=dict)
    screen_summary: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "total_windows": self.total_windows,
            "active_app": self.active_app.to_dict() if self.active_app else None,
            "focus_window": self.focus_window.to_dict() if self.focus_window else None,
            "changed_windows": [c.to_dict() for c in self.changed_windows],
            "crops": [c.to_dict() for c in self.crops],
            "by_category": {
                k: [c.to_dict() for c in v]
                for k, v in self.by_category.items()
            },
            "screen_summary": self.screen_summary,
        }

    def get_summary_for_tts(self) -> str:
        """
        Generate a structured text summary for TTS response.
        Organized: first describe what's visible, then provide context.
        """
        parts = []

        # Focus window (where user is actively working, based on diffs)
        if self.focus_window:
            w = self.focus_window.window
            parts.append(
                f"🎯 Fokus pracy: {w.wm_class_name or w.title} "
                f"({w.category.value}, zmiana: {self.focus_window.change_score:.0f})"
            )

        # Active app (may differ from focus)
        if self.active_app and (
            not self.focus_window
            or self.active_app.window.window_id != self.focus_window.window.window_id
        ):
            w = self.active_app.window
            parts.append(
                f"Aktywna aplikacja: {w.wm_class_name or w.title} "
                f"({w.category.value})"
            )

        # Changed windows
        if self.changed_windows:
            changed_names = [
                f"{c.window.wm_class_name or c.window.title} (Δ{c.change_score:.0f})"
                for c in self.changed_windows
                if not c.is_focus  # already reported
            ]
            if changed_names:
                parts.append(f"Zmienione: {', '.join(changed_names)}")

        # Other apps grouped by category
        category_priority = [
            AppCategory.IDE, AppCategory.TERMINAL, AppCategory.BROWSER,
            AppCategory.CHAT, AppCategory.EMAIL, AppCategory.OFFICE,
            AppCategory.MEDIA, AppCategory.FILE_MANAGER, AppCategory.SYSTEM,
            AppCategory.UNKNOWN,
        ]

        for cat in category_priority:
            cat_crops = self.by_category.get(cat.value, [])
            cat_crops = [c for c in cat_crops if not c.window.is_active and not c.is_focus]
            if cat_crops:
                names = [c.window.wm_class_name or c.window.title for c in cat_crops]
                parts.append(f"{cat.value}: {', '.join(names)}")

        return ". ".join(parts) if parts else "Brak widocznych okien"


class WindowCropper:
    """
    Crops individual application windows from fullscreen screenshots.
    """

    # Category priority for analysis ordering
    CATEGORY_PRIORITY = {
        AppCategory.IDE: 10,
        AppCategory.TERMINAL: 9,
        AppCategory.BROWSER: 7,
        AppCategory.EMAIL: 6,
        AppCategory.CHAT: 5,
        AppCategory.OFFICE: 4,
        AppCategory.MEDIA: 3,
        AppCategory.FILE_MANAGER: 2,
        AppCategory.SYSTEM: 1,
        AppCategory.UNKNOWN: 0,
    }

    # Titles that indicate compositor/WM guard windows (not real apps)
    _GUARD_TITLES = {"", "mutter guard window", "gnome-shell", "Desktop"}

    def __init__(
        self,
        process_scanner: ProcessScanner,
        crops_dir: str = "/tmp/aidesk_crops",
        jpeg_quality: int = 70,
        min_window_size: int = 100,
        max_crop_dimension: int = 1280,
        change_threshold: float = 3.0,
    ):
        self.scanner = process_scanner
        self.crops_dir = crops_dir
        self.jpeg_quality = jpeg_quality
        self.min_window_size = min_window_size
        self.max_crop_dimension = max_crop_dimension
        self.change_threshold = change_threshold

        # Per-window change detection: geo_key → previous phash
        self._prev_hashes: Dict[Tuple[int, int, int, int], imagehash.ImageHash] = {}

        # Stats
        self.total_crops = 0
        self.total_organizes = 0
        self.total_focus_detections = 0

        # Ensure crops directory
        if self.crops_dir:
            os.makedirs(self.crops_dir, exist_ok=True)

        logger.info(
            "WindowCropper initialized",
            crops_dir=crops_dir,
            jpeg_quality=jpeg_quality,
            change_threshold=change_threshold,
        )

    @nfo.log_call(level="INFO")
    def crop_all_windows(
        self,
        fullscreen_image: Image.Image,
        windows: Optional[List[VisibleWindow]] = None,
    ) -> List[CroppedWindow]:
        """
        Crop each visible window from the fullscreen screenshot.

        Args:
            fullscreen_image: PIL Image of the full screen
            windows: Pre-scanned windows (if None, scans now)

        Returns:
            List of CroppedWindow with base64 image data
        """
        if windows is None:
            windows = self.scanner.scan_all_windows()

        crops = []
        screen_w, screen_h = fullscreen_image.size
        seen_geometries = set()
        new_hashes: Dict[Tuple[int, int, int, int], imagehash.ImageHash] = {}

        for win in windows:
            # Skip tiny windows
            if win.width < self.min_window_size or win.height < self.min_window_size:
                continue

            # Skip compositor/guard windows: no WM class + fills entire screen
            if win.title in self._GUARD_TITLES and not win.wm_class_name:
                continue

            # Deduplicate windows with identical geometry (e.g. PyCharm sub-windows)
            geo_key = (win.x, win.y, win.width, win.height)
            if geo_key in seen_geometries:
                continue
            seen_geometries.add(geo_key)

            # Clamp to screen/image bounds
            x1 = max(0, win.x)
            y1 = max(0, win.y)
            x2 = min(screen_w, win.x + win.width)
            y2 = min(screen_h, win.y + win.height)

            if x2 <= x1 or y2 <= y1:
                continue

            try:
                # Crop the region
                cropped_img = fullscreen_image.crop((x1, y1, x2, y2))

                # Resize preserving aspect ratio (longest side → max_crop_dimension)
                crop_w, crop_h = cropped_img.size
                longest = max(crop_w, crop_h)
                if longest > self.max_crop_dimension:
                    ratio = self.max_crop_dimension / longest
                    new_w = max(2, int(crop_w * ratio))
                    new_h = max(2, int(crop_h * ratio))
                    cropped_img = cropped_img.resize(
                        (new_w, new_h), Image.Resampling.LANCZOS
                    )
                    crop_w, crop_h = new_w, new_h

                # Per-window change detection via perceptual hash
                current_hash = imagehash.phash(cropped_img, hash_size=8)
                new_hashes[geo_key] = current_hash
                change_score = 0.0
                prev = self._prev_hashes.get(geo_key)
                if prev is not None:
                    change_score = float(current_hash - prev)

                # Encode as JPEG base64
                buffer = BytesIO()
                cropped_img.save(
                    buffer, format="JPEG",
                    quality=self.jpeg_quality, optimize=True
                )
                b64 = base64.b64encode(buffer.getvalue()).decode()
                size_kb = len(buffer.getvalue()) / 1024

                # Save to disk
                filepath = ""
                if self.crops_dir:
                    safe_name = (win.wm_class_name or "unknown").replace("/", "_")
                    filename = f"crop_{safe_name}_{win.window_id}_{int(time.time())}.jpg"
                    filepath = os.path.join(self.crops_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(buffer.getvalue())

                crop = CroppedWindow(
                    window=win,
                    image_b64=b64,
                    width=crop_w,
                    height=crop_h,
                    size_kb=size_kb,
                    crop_timestamp=time.time(),
                    filepath=filepath,
                    change_score=change_score,
                )
                crops.append(crop)
                self.total_crops += 1

            except Exception as e:
                logger.warning(
                    "Failed to crop window",
                    window_id=win.window_id,
                    title=win.title,
                    error=str(e),
                )

        # Update stored hashes for next comparison
        self._prev_hashes = new_hashes

        logger.info(
            "Cropped windows",
            total=len(crops),
            categories=[c.window.category.value for c in crops],
            changes=[round(c.change_score, 1) for c in crops],
        )

        return crops

    @nfo.log_call(level="INFO")
    def organize_screen(
        self,
        fullscreen_image: Image.Image,
        windows: Optional[List[VisibleWindow]] = None,
    ) -> OrganizedScreenData:
        """
        Full pipeline: scan windows → crop each → organize by priority.

        This is the main entry point. It:
        1. Scans all visible windows and their processes
        2. Crops each application from the fullscreen screenshot
        3. Organizes crops by category and priority
        4. Generates a structured summary for TTS

        Args:
            fullscreen_image: PIL Image of the full screen
            windows: Pre-scanned windows (optional)

        Returns:
            OrganizedScreenData with all crops and metadata
        """
        self.total_organizes += 1

        # Step 1: Get all windows
        if windows is None:
            windows = self.scanner.scan_all_windows()

        # Step 2: Crop each window (includes per-window change detection)
        crops = self.crop_all_windows(fullscreen_image, windows)

        # Step 3: Organize
        organized = OrganizedScreenData(
            timestamp=time.time(),
            total_windows=len(crops),
        )

        # Group by category
        for crop in crops:
            cat = crop.window.category.value
            if cat not in organized.by_category:
                organized.by_category[cat] = []
            organized.by_category[cat].append(crop)

            if crop.window.is_active:
                organized.active_app = crop

        # Step 4: Detect focus — the window with the most change
        changed = [c for c in crops if c.change_score >= self.change_threshold]
        organized.changed_windows = sorted(
            changed, key=lambda c: c.change_score, reverse=True
        )

        if organized.changed_windows:
            focus = organized.changed_windows[0]
            focus.is_focus = True
            organized.focus_window = focus
            self.total_focus_detections += 1

        # Sort crops by priority (focus first, then active, then category importance)
        organized.crops = sorted(
            crops,
            key=lambda c: (
                not c.is_focus,
                not c.window.is_active,
                -c.change_score,
                -self.CATEGORY_PRIORITY.get(c.window.category, 0),
            ),
        )

        # Generate summary for TTS
        organized.screen_summary = organized.get_summary_for_tts()

        logger.info(
            "Screen organized",
            total_windows=organized.total_windows,
            active_app=organized.active_app.window.wm_class_name if organized.active_app else None,
            focus_app=organized.focus_window.window.wm_class_name if organized.focus_window else None,
            changed_count=len(organized.changed_windows),
            categories=list(organized.by_category.keys()),
            summary=organized.screen_summary,
        )

        return organized

    @nfo.log_call(level="INFO")
    def crop_single_window(
        self,
        fullscreen_image: Image.Image,
        window: VisibleWindow,
    ) -> Optional[CroppedWindow]:
        """Crop a single specific window from the fullscreen image."""
        crops = self.crop_all_windows(fullscreen_image, [window])
        return crops[0] if crops else None

    def get_stats(self) -> Dict:
        return {
            "total_crops": self.total_crops,
            "total_organizes": self.total_organizes,
            "total_focus_detections": self.total_focus_detections,
            "tracked_windows": len(self._prev_hashes),
            "change_threshold": self.change_threshold,
            "crops_dir": self.crops_dir,
            "scanner": self.scanner.get_stats(),
        }


def create_window_cropper(
    process_scanner: ProcessScanner,
    crops_dir: Optional[str] = None,
) -> WindowCropper:
    """Create WindowCropper from environment."""
    if crops_dir is None:
        crops_dir = os.getenv("CROPS_DIR", "/tmp/aidesk_crops")

    return WindowCropper(
        process_scanner=process_scanner,
        crops_dir=crops_dir,
        jpeg_quality=int(os.getenv("JPEG_QUALITY", "70")),
        max_crop_dimension=int(os.getenv("MAX_DIMENSION", "1280")),
        change_threshold=float(os.getenv("CROP_CHANGE_THRESHOLD", "3.0")),
    )


# Auto-log all functions in this module via nfo
nfo.auto_log()
