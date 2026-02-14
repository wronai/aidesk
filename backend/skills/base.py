"""
Skill System — Base classes for extensible text analysis actions.

Architecture:
- BaseSkill: abstract class that all skills implement
- SkillContext: context passed to skills (window, locale, transcript)
- SkillOption: a clickable action option shown in popup
- SkillResult: result of executing a skill option
- SkillMatch: a skill + its confidence + options for a given text

Adding a new skill:
1. Create skills/my_skill.py with class MySkill(BaseSkill)
2. Implement detect(), get_options(), execute()
3. Register in skills/__init__.py BUILTIN_SKILLS list

Zero LLM by default — skills use pattern matching.
Optional LLM pass-through for complex analysis.
"""
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


class SkillCategory(str, Enum):
    """Broad category for organizing skills."""
    COMMAND = "command"          # Shell commands, scripts
    ERROR_FIX = "error_fix"     # Error detection + fix suggestions
    LANGUAGE = "language"       # Translation, TTS, text processing
    NAVIGATION = "navigation"   # URLs, file paths, open actions
    CLIPBOARD = "clipboard"     # Copy, paste, format
    VOICE = "voice"             # Voice commands
    CUSTOM = "custom"           # User-defined / plugin skills


class OptionRisk(str, Enum):
    """Risk level for skill options (matches shell_agent.ActionRisk)."""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SkillContext:
    """
    Context passed to skills for detection and execution.

    Populated from app_state by SkillRouter before calling skills.
    """
    text: str = ""                          # Selected/analyzed text
    window_category: str = "unknown"        # AppCategory value
    window_title: str = ""
    window_class: str = ""
    cwd: str = ""                           # Current working directory
    locale: str = "pl"                      # User's native language
    latest_transcript: str = ""             # Latest STT transcript (for voice commands)
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class SkillOption:
    """
    A single actionable option shown in the overlay popup.

    Example: "▶ Uruchom w bieżącym terminalu" or "🌐 Przetłumacz na polski"
    """
    id: str                     # Unique within skill (e.g. "run_here", "translate_pl")
    label: str                  # Shown in popup (e.g. "▶ Uruchom tutaj")
    icon: str = ""              # Emoji icon
    description: str = ""       # Longer description (tooltip)
    risk: OptionRisk = OptionRisk.SAFE
    data: Dict[str, Any] = field(default_factory=dict)  # Extra data for execution

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "label": self.label,
            "icon": self.icon,
            "description": self.description,
            "risk": self.risk.value,
            "data": self.data,
        }


@dataclass
class SkillResult:
    """Result of executing a skill option."""
    success: bool = True
    message: str = ""           # Shown in overlay
    clipboard_text: str = ""    # Text to copy to clipboard
    output: str = ""            # Command output, translation result, etc.
    audio_file: str = ""        # Path to TTS audio file (if any)
    open_url: str = ""          # URL to open in browser
    error: str = ""

    def to_dict(self) -> Dict:
        d = {
            "success": self.success,
            "message": self.message,
        }
        if self.clipboard_text:
            d["clipboard_text"] = self.clipboard_text
        if self.output:
            d["output"] = self.output[:2000]
        if self.audio_file:
            d["audio_file"] = self.audio_file
        if self.open_url:
            d["open_url"] = self.open_url
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class SkillMatch:
    """A skill that matched the input text, with confidence and options."""
    skill_name: str
    category: SkillCategory
    confidence: float           # 0.0 - 1.0
    label: str                  # What was detected (e.g. "Komenda shell", "Tekst angielski")
    icon: str = ""
    options: List[SkillOption] = field(default_factory=list)
    extracted_text: str = ""    # Key text extracted from input (e.g. the command, the URL)

    def to_dict(self) -> Dict:
        return {
            "skill": self.skill_name,
            "category": self.category.value,
            "confidence": round(self.confidence, 2),
            "label": self.label,
            "icon": self.icon,
            "options": [o.to_dict() for o in self.options],
            "extracted_text": self.extracted_text[:200],
        }


class BaseSkill(ABC):
    """
    Abstract base class for all skills.

    Lifecycle:
    1. detect(text, ctx) → confidence (0.0 = no match, 1.0 = perfect)
    2. get_options(text, ctx) → list of SkillOption for popup
    3. execute(text, option_id, ctx) → SkillResult

    Skills should be stateless and fast (no blocking I/O in detect/get_options).
    """

    name: str = "base"
    category: SkillCategory = SkillCategory.CUSTOM
    icon: str = "🔧"
    priority: int = 50  # Higher = checked first (0-100)

    @abstractmethod
    def detect(self, text: str, ctx: SkillContext) -> float:
        """
        Detect if this skill applies to the given text.

        Returns confidence 0.0-1.0. Return 0.0 for no match.
        Should be fast (regex only, no I/O).
        """
        ...

    @abstractmethod
    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        """
        Get actionable options for the popup.

        Called only if detect() > 0. Should return 1-5 options.
        """
        ...

    @abstractmethod
    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        """
        Execute the chosen option.

        May do I/O (run command, call API, generate audio).
        """
        ...

    def get_match(self, text: str, ctx: SkillContext) -> Optional[SkillMatch]:
        """
        Convenience: detect + get_options in one call.

        Returns SkillMatch if confidence > 0, else None.
        """
        confidence = self.detect(text, ctx)
        if confidence <= 0:
            return None

        options = self.get_options(text, ctx)
        # Extract key text (first option's data or first 100 chars)
        extracted = ""
        if options and options[0].data.get("extracted"):
            extracted = options[0].data["extracted"]
        else:
            extracted = text[:100]

        return SkillMatch(
            skill_name=self.name,
            category=self.category,
            confidence=confidence,
            label=self._label(text, ctx),
            icon=self.icon,
            options=options,
            extracted_text=extracted,
        )

    def _label(self, text: str, ctx: SkillContext) -> str:
        """Override to provide a human-readable label for the match."""
        return self.name
