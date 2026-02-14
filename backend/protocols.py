"""
Component Protocols - Interface Segregation & Dependency Inversion.

Defines typed contracts for all pluggable components.
Modules depend on these Protocols, not on concrete implementations.
This enables:
- Interface Segregation: each protocol is minimal and focused
- Dependency Inversion: high-level modules depend on abstractions
- Liskov Substitution: any implementation satisfying the protocol is valid
- Open/Closed: new implementations don't require modifying consumers
"""
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ===== Screen Capture =====

@runtime_checkable
class ScreenCapture(Protocol):
    """Contract for screen capture modules."""

    def capture(self, monitor_index: int = 0, roi: Optional[Dict] = None) -> Optional[Dict]:
        """Capture screen. Returns dict with image_b64, timestamp, size_kb or None if no change."""
        ...

    @property
    def adaptive_interval(self) -> float:
        """Current adaptive polling interval in seconds."""
        ...

    def get_stats(self) -> Dict:
        ...

    def get_monitors(self) -> List[Dict]:
        ...


# ===== OCR =====

@runtime_checkable
class OCRExtractor(Protocol):
    """Contract for OCR text extraction."""

    def extract(self, image_b64: str) -> Any:
        """Extract text from base64 image. Returns OCRResult-like object."""
        ...

    def set_engine(self, name: str) -> bool:
        """Switch active OCR engine."""
        ...

    def benchmark(self, image_b64: str) -> Dict:
        """Run benchmark across all engines."""
        ...

    def get_available_engines(self) -> List[Dict]:
        ...

    def get_stats(self) -> Dict:
        ...


# ===== Analyzer =====

@runtime_checkable
class ScreenAnalyzer(Protocol):
    """Contract for screen analysis (VLM / hybrid)."""

    async def analyze(self, image_b64: str, context: str = "") -> Dict:
        """Analyze image with context. Returns dict with text, tokens, cost, etc."""
        ...

    def set_mode(self, mode: str) -> bool:
        """Switch analysis mode at runtime."""
        ...

    def get_stats(self) -> Dict:
        ...


# ===== Window Awareness =====

@runtime_checkable
class WindowDetector(Protocol):
    """Contract for active window detection."""

    def get_active_window(self) -> Any:
        """Get active window info. Returns WindowInfo-like object with to_dict(), to_context_string()."""
        ...

    def get_monitors(self) -> List[Any]:
        """Get connected monitors."""
        ...

    def get_window_roi(self, info: Any) -> Dict:
        """Get ROI dict for a window."""
        ...

    def get_stats(self) -> Dict:
        ...


# ===== Process Scanner =====

@runtime_checkable
class ProcessScanning(Protocol):
    """Contract for scanning visible windows and processes."""

    def scan_all_windows(self) -> List[Any]:
        """Scan all visible windows. Returns list of VisibleWindow-like objects."""
        ...

    def get_window_layout(self) -> Dict:
        """Get organized window layout grouped by category."""
        ...

    def get_stats(self) -> Dict:
        ...


# ===== Window Cropper =====

@runtime_checkable
class WindowCropping(Protocol):
    """Contract for cropping per-app regions from screenshots."""

    def organize_screen(self, fullscreen_image: Any, windows: List[Any]) -> Any:
        """Crop and organize screen. Returns OrganizedScreenData-like object."""
        ...

    def get_stats(self) -> Dict:
        ...


# ===== App Profiles =====

@runtime_checkable
class ProfileProvider(Protocol):
    """Contract for per-app analysis profiles."""

    def get_prompt_addon(self, category: Any) -> str:
        """Get system prompt addon for app category."""
        ...

    def get_all_profiles(self) -> List[Dict]:
        ...

    def get_stats(self) -> Dict:
        ...


# ===== Shell Agent =====

@runtime_checkable
class CommandAgent(Protocol):
    """Contract for shell command suggestion and execution."""

    def suggest_actions(self, detected_text: str, category: Any, cwd: Optional[str] = None) -> List[Any]:
        """Suggest actions based on detected text. Returns list of AgentAction-like objects."""
        ...

    def execute_action(self, action_id: str, cwd: Optional[str] = None, force: bool = False) -> Any:
        """Execute a previously suggested action."""
        ...

    def execute_safe(self, command: str, cwd: Optional[str] = None) -> Any:
        """Execute a safe (whitelisted) command directly."""
        ...

    def approve_action(self, action_id: str) -> bool:
        ...

    def get_pending_actions(self) -> List[Dict]:
        ...

    def get_history(self, n: int = 20) -> List[Dict]:
        ...

    def get_stats(self) -> Dict:
        ...


# ===== Context Manager =====

@runtime_checkable
class ContextStore(Protocol):
    """Contract for conversation context storage."""

    def add(self, content: str, context_type: str = "screen", metadata: Optional[Dict] = None):
        ...

    def get_context_string(self, n: int = 5, max_length: int = 500) -> str:
        ...

    def get_recent(self, n: int = 5, context_type: Optional[str] = None) -> List[Dict]:
        ...

    def get_stats(self) -> Dict:
        ...


# ===== STT =====

@runtime_checkable
class SpeechToText(Protocol):
    """Contract for speech-to-text service."""

    async def start(self, callback) -> None:
        ...

    async def stop(self) -> None:
        ...

    def get_stats(self) -> Dict:
        ...


# ===== SSE Broadcaster =====

@runtime_checkable
class EventBroadcaster(Protocol):
    """Contract for SSE event broadcasting to overlay clients."""

    async def broadcast(self, event_type: str, data: Dict) -> None:
        """Send event to all connected SSE clients."""
        ...
