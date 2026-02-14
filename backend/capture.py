"""
Screen capture module with intelligent change detection.
"""
import mss
import imagehash
import numpy as np
from PIL import Image
from io import BytesIO
import base64
import time
from typing import Optional, Dict
import structlog
import os

logger = structlog.get_logger()


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
        screen_width: int = 1280,
        screen_height: int = 720,
        jpeg_quality: int = 60,
        captures_dir: str = "/tmp/aidesk_captures",
    ):
        """
        Initialize screen capture.

        Args:
            change_threshold: Hamming distance threshold for detecting changes (1-20)
            min_interval: Minimum seconds between captures
            idle_threshold: Number of unchanged frames before entering idle mode
            idle_interval: Capture interval when idle (seconds)
            screen_width: Target width for resized screenshots
            screen_height: Target height for resized screenshots
            jpeg_quality: JPEG compression quality (1-100)
            captures_dir: Directory to save debug screenshots
        """
        self.sct = mss.mss()
        self.last_hash = None
        self.last_capture_time = 0
        self.change_threshold = change_threshold
        self.min_interval = min_interval
        self.idle_threshold = idle_threshold
        self.idle_interval = idle_interval
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.jpeg_quality = jpeg_quality
        self.captures_dir = captures_dir
        self.consecutive_unchanged = 0
        self.total_captures = 0
        self.changes_detected = 0

        # Ensure captures directory exists
        if self.captures_dir:
            os.makedirs(self.captures_dir, exist_ok=True)

        logger.info(
            "Screen capture initialized",
            threshold=change_threshold,
            interval=min_interval,
            resolution=f"{screen_width}x{screen_height}",
            captures_dir=captures_dir,
        )

    def capture(self) -> Optional[Dict]:
        """
        Capture screen and return if meaningful change detected.

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
            # Capture primary monitor
            monitor = self.sct.monitors[1]
            raw = self.sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

            # Resize for cheaper AI processing
            img_resized = img.resize(
                (self.screen_width, self.screen_height), Image.Resampling.LANCZOS
            )

            # Perceptual hash for change detection
            current_hash = imagehash.phash(img_resized, hash_size=8)

            # Check for changes
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

            # Encode as JPEG
            buffer = BytesIO()
            img_resized.save(buffer, format="JPEG", quality=self.jpeg_quality, optimize=True)
            
            # Save to disk if directory is configured
            if self.captures_dir:
                filename = f"capture_{int(now)}.jpg"
                filepath = os.path.join(self.captures_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(buffer.getvalue())
                logger.debug("Screenshot saved to disk", path=filepath)

            b64 = base64.b64encode(buffer.getvalue()).decode()
            size_kb = len(buffer.getvalue()) / 1024

            logger.info(
                "Screen change detected",
                size_kb=round(size_kb, 1),
                resolution=f"{self.screen_width}x{self.screen_height}",
                idle_frames=prev_unchanged,
                detection_rate=f"{(self.changes_detected / self.total_captures * 100):.1f}%",
            )

            return {
                "image_b64": b64,
                "timestamp": now,
                "resolution": (self.screen_width, self.screen_height),
                "size_kb": size_kb,
                "hash_diff": 0 if self.last_hash is None else hash_diff,
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

    def get_stats(self) -> Dict:
        """Get capture statistics."""
        return {
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
        }


# Configuration from environment
def create_capture_from_env() -> SmartScreenCapture:
    """Create capture instance from environment variables."""
    from dotenv import load_dotenv

    load_dotenv()

    return SmartScreenCapture(
        change_threshold=int(os.getenv("CHANGE_THRESHOLD", "8")),
        min_interval=float(os.getenv("MIN_CAPTURE_INTERVAL", "1.0")),
        idle_threshold=int(os.getenv("IDLE_THRESHOLD", "30")),
        idle_interval=float(os.getenv("IDLE_INTERVAL", "10.0")),
        screen_width=int(os.getenv("SCREEN_WIDTH", "1280")),
        screen_height=int(os.getenv("SCREEN_HEIGHT", "720")),
        jpeg_quality=int(os.getenv("JPEG_QUALITY", "60")),
        captures_dir=os.getenv("CAPTURES_DIR", "/tmp/aidesk_captures"),
    )
