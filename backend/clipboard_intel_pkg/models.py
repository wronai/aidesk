"""Clipboard data models: ClipSource, ClipboardItem, AutoCopyResult, PasteSuggestion, Snippet, SelectionAnalysis."""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict


class ClipSource(str, Enum):
    """Origin of a clipboard entry."""
    USER = "user"           # User copied manually
    AGENT = "agent"         # ShellAgent suggested command
    AUTO = "auto"           # Context-aware auto-copy
    SNIPPET = "snippet"     # Form filler / snippet expansion
    OCR = "ocr"             # Extracted from screen via OCR


@dataclass
class ClipboardItem:
    """A single item in the clipboard queue."""
    text: str
    source: ClipSource = ClipSource.USER
    category: str = ""          # AppCategory value when copied
    timestamp: float = 0.0
    label: str = ""             # Human-readable description
    used_count: int = 0         # How many times pasted/selected
    pinned: bool = False        # Pinned items survive eviction

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.label:
            self.label = self.text[:60].replace("\n", " ")

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "source": self.source.value,
            "category": self.category,
            "timestamp": self.timestamp,
            "label": self.label,
            "used_count": self.used_count,
            "pinned": self.pinned,
        }


@dataclass
class AutoCopyResult:
    """Result of context-aware auto-copy scan."""
    text: str
    label: str
    source: ClipSource
    rule_index: int
    confidence: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "label": self.label,
            "source": self.source.value,
            "confidence": self.confidence,
        }


@dataclass
class PasteSuggestion:
    """A suggested clipboard item to paste in the current context."""
    text: str
    label: str
    source: ClipSource
    score: float           # 0.0-1.0 relevance
    reason: str            # Why this is suggested

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "label": self.label,
            "source": self.source.value,
            "score": round(self.score, 2),
            "reason": self.reason,
        }


@dataclass
class Snippet:
    """A reusable text snippet triggered by abbreviation."""
    trigger: str        # e.g. ";;email"
    expansion: str      # e.g. "user@example.com"
    label: str = ""
    category: str = ""  # optional: only expand in certain categories

    def to_dict(self) -> Dict:
        return {
            "trigger": self.trigger,
            "expansion": self.expansion,
            "label": self.label or self.trigger,
            "category": self.category,
        }


@dataclass
class SelectionAnalysis:
    """Result of analyzing selected text."""
    text: str               # original selected text
    label: str              # what was detected
    response: str           # formatted response for the overlay
    clipboard_text: str = ""  # text to copy to clipboard (if any)
    rule_matched: bool = True

    def to_dict(self) -> Dict:
        return {
            "text": self.text[:200],
            "label": self.label,
            "response": self.response,
            "clipboard_text": self.clipboard_text,
            "rule_matched": self.rule_matched,
        }
