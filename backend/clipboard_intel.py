"""
Clipboard Intelligence — Smart clipboard queue, context-aware auto-copy, paste suggestions.

Three integrated subsystems:

1. ClipboardQueue — ring buffer of last N clipboard items with dedup and metadata.
2. ContextCopyEngine — rules engine that extracts actionable text from screen/analysis
   based on active window category (terminal errors, SO answers, IDE fix commands).
3. PasteSuggester — matches current window context against clipboard queue + agent
   actions to suggest the best item to paste.

Zero LLM — pure pattern matching, window context, and ActionTemplates integration.

Usage:
    mgr = ClipboardManager(max_items=20)
    mgr.push("git pull --rebase", source="agent", category="terminal")
    suggestions = mgr.suggest_paste(window_category=AppCategory.TERMINAL, screen_text="...")
"""
import re
import shlex
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import nfo
import structlog

from window_aware import AppCategory

logger = structlog.get_logger()


# ===== Clipboard Item =====

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


# ===== 1. Smart Clipboard Queue =====

class ClipboardQueue:
    """
    Ring buffer of last N clipboard items with dedup, pinning, and metadata.

    Items are deduplicated by text content — re-copying moves item to front.
    Pinned items are never evicted. Oldest unpinned items are evicted first.
    """

    def __init__(self, max_items: int = 20):
        self.max_items = max_items
        self._items: deque[ClipboardItem] = deque()
        self._text_index: Dict[str, ClipboardItem] = {}  # fast lookup by text

    def push(
        self,
        text: str,
        source: ClipSource = ClipSource.USER,
        category: str = "",
        label: str = "",
    ) -> ClipboardItem:
        """Add or promote an item. Returns the ClipboardItem."""
        text = text.strip()
        if not text:
            return None

        # Dedup: if already in queue, promote to front
        if text in self._text_index:
            existing = self._text_index[text]
            self._items.remove(existing)
            existing.timestamp = time.time()
            existing.used_count += 1
            if source != ClipSource.USER:
                existing.source = source
            if label:
                existing.label = label
            self._items.appendleft(existing)
            return existing

        # New item
        item = ClipboardItem(
            text=text,
            source=source,
            category=category,
            label=label,
        )
        self._items.appendleft(item)
        self._text_index[text] = item

        # Evict oldest unpinned if over limit
        self._evict()
        return item

    def get_all(self) -> List[ClipboardItem]:
        """Get all items, newest first."""
        return list(self._items)

    def get_recent(self, n: int = 10) -> List[ClipboardItem]:
        """Get N most recent items."""
        return list(self._items)[:n]

    def get_by_source(self, source: ClipSource) -> List[ClipboardItem]:
        """Filter items by source type."""
        return [i for i in self._items if i.source == source]

    def pin(self, text: str) -> bool:
        """Pin an item so it's never evicted."""
        if text in self._text_index:
            self._text_index[text].pinned = True
            return True
        return False

    def unpin(self, text: str) -> bool:
        """Unpin an item."""
        if text in self._text_index:
            self._text_index[text].pinned = False
            return True
        return False

    def remove(self, text: str) -> bool:
        """Remove a specific item."""
        if text in self._text_index:
            item = self._text_index.pop(text)
            self._items.remove(item)
            return True
        return False

    def clear(self):
        """Clear all non-pinned items."""
        pinned = [i for i in self._items if i.pinned]
        self._items.clear()
        self._text_index.clear()
        for item in pinned:
            self._items.append(item)
            self._text_index[item.text] = item

    def _evict(self):
        """Remove oldest unpinned items if over max_items."""
        while len(self._items) > self.max_items:
            # Find oldest unpinned
            for i in range(len(self._items) - 1, -1, -1):
                if not self._items[i].pinned:
                    removed = self._items[i]
                    del self._items[i]
                    self._text_index.pop(removed.text, None)
                    break
            else:
                break  # All pinned, can't evict

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, text: str) -> bool:
        return text in self._text_index


# ===== 2. Context-Aware Auto-Copy Engine =====

# Rules: (category, pattern_on_screen_text, extract_group_or_full, label_template)
AUTO_COPY_RULES: List[Dict] = [
    # Terminal: copy last error
    {
        "categories": [AppCategory.TERMINAL],
        "pattern": r"(?:error|Error|ERROR)[:\s]+(.+?)(?:\n|$)",
        "extract": 1,  # capture group index
        "label": "Błąd terminala",
        "source": ClipSource.AUTO,
    },
    # Terminal: command not found → suggest install
    {
        "categories": [AppCategory.TERMINAL],
        "pattern": r"(\S+): command not found",
        "extract": 0,  # full match
        "label": "Komenda nie znaleziona: {match}",
        "source": ClipSource.AUTO,
    },
    # IDE/Terminal: Python module not found → pip install
    {
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
        "pattern": r"ModuleNotFoundError:\s+No module named ['\"]?(\w+)['\"]?",
        "extract_command": "pip install {1}",
        "label": "Zainstaluj moduł: pip install {1}",
        "source": ClipSource.AGENT,
    },
    # IDE/Terminal: Node module not found → npm install
    {
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
        "pattern": r"Cannot find module ['\"]([^'\"]+)['\"]",
        "extract_command": "npm install {1}",
        "label": "Zainstaluj moduł: npm install {1}",
        "source": ClipSource.AGENT,
    },
    # Browser: detect URLs
    {
        "categories": [AppCategory.BROWSER, AppCategory.CHAT, AppCategory.EMAIL],
        "pattern": r"(https?://[^\s<>\"']+)",
        "extract": 1,
        "label": "URL: {match}",
        "source": ClipSource.OCR,
    },
    # Email/Chat: detect email addresses
    {
        "categories": [AppCategory.EMAIL, AppCategory.CHAT, AppCategory.BROWSER],
        "pattern": r"([\w.+-]+@[\w-]+\.[\w.-]+)",
        "extract": 1,
        "label": "Email: {match}",
        "source": ClipSource.OCR,
    },
    # Terminal: git push rejected → suggest fix
    {
        "categories": [AppCategory.TERMINAL, AppCategory.IDE],
        "pattern": r"(?:rejected|failed to push|non-fast-forward)",
        "extract_command": "git pull --rebase",
        "label": "Git push odrzucony → git pull --rebase",
        "source": ClipSource.AGENT,
    },
    # Terminal: file paths (absolute)
    {
        "categories": [AppCategory.TERMINAL, AppCategory.IDE],
        "pattern": r"(/(?:home|tmp|var|etc|opt|usr)/[\w/.@-]+)",
        "extract": 1,
        "label": "Ścieżka: {match}",
        "source": ClipSource.OCR,
    },
    # Any: IP addresses
    {
        "categories": [AppCategory.TERMINAL, AppCategory.BROWSER, AppCategory.IDE],
        "pattern": r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)",
        "extract": 1,
        "label": "IP: {match}",
        "source": ClipSource.OCR,
    },
]


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


class ContextCopyEngine:
    """
    Scans screen text for actionable content based on window category.

    Uses pattern matching rules (no LLM) to extract:
    - Error messages, commands, URLs, emails, file paths
    - Per-category priorities (terminal errors > browser URLs)
    """

    def __init__(self, rules: Optional[List[Dict]] = None):
        self._rules = rules or AUTO_COPY_RULES
        self._compiled = [
            (r, re.compile(r["pattern"], re.IGNORECASE | re.MULTILINE))
            for r in self._rules
        ]

    def scan(
        self,
        screen_text: str,
        category: AppCategory,
        max_results: int = 5,
    ) -> List[AutoCopyResult]:
        """
        Scan screen text for auto-copyable content.

        Returns up to max_results items, ordered by relevance.
        """
        if not screen_text:
            return []

        results: List[AutoCopyResult] = []
        seen_texts = set()

        for idx, (rule, compiled) in enumerate(self._compiled):
            # Category filter
            if category not in rule.get("categories", []):
                continue

            for match in compiled.finditer(screen_text):
                # Extract text based on rule type
                if "extract_command" in rule:
                    # Build command from capture groups
                    text = rule["extract_command"]
                    for i, g in enumerate(match.groups(), 1):
                        if g:
                            text = text.replace(f"{{{i}}}", g)
                    label = rule["label"]
                    for i, g in enumerate(match.groups(), 1):
                        if g:
                            label = label.replace(f"{{{i}}}", g)
                elif "extract" in rule:
                    group_idx = rule["extract"]
                    if group_idx == 0:
                        text = match.group(0)
                    else:
                        text = match.group(group_idx) if match.lastindex >= group_idx else match.group(0)
                    label = rule["label"].replace("{match}", text[:40])
                else:
                    text = match.group(0)
                    label = rule.get("label", text[:40])

                text = text.strip()
                if not text or text in seen_texts:
                    continue
                seen_texts.add(text)

                results.append(AutoCopyResult(
                    text=text,
                    label=label,
                    source=rule.get("source", ClipSource.AUTO),
                    rule_index=idx,
                ))

                if len(results) >= max_results:
                    return results

        return results


# ===== 3. Paste Suggestions =====

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


# Per-category keywords that boost paste relevance
_PASTE_CONTEXT_KEYWORDS: Dict[AppCategory, List[str]] = {
    AppCategory.TERMINAL: [
        "error", "command", "git", "pip", "npm", "docker", "make",
        "install", "build", "run", "test", "deploy",
    ],
    AppCategory.IDE: [
        "import", "module", "error", "fix", "install", "class", "def",
        "function", "variable", "type", "syntax",
    ],
    AppCategory.BROWSER: [
        "url", "http", "link", "search", "stackoverflow", "github",
        "docs", "api", "documentation",
    ],
    AppCategory.EMAIL: [
        "email", "address", "phone", "meeting", "schedule", "reply",
    ],
    AppCategory.CHAT: [
        "link", "url", "command", "code", "snippet",
    ],
}


class PasteSuggester:
    """
    Suggests the best clipboard item to paste based on:
    - Current window category
    - Screen text content
    - Item source (agent suggestions score higher than user copies)
    - Recency (newer items score higher)
    - Usage count (frequently used items score higher)
    """

    def suggest(
        self,
        queue: ClipboardQueue,
        category: AppCategory,
        screen_text: str = "",
        max_suggestions: int = 3,
    ) -> List[PasteSuggestion]:
        """Generate paste suggestions ranked by relevance."""
        if len(queue) == 0:
            return []

        suggestions: List[PasteSuggestion] = []
        now = time.time()
        keywords = _PASTE_CONTEXT_KEYWORDS.get(category, [])

        for item in queue.get_recent(20):
            score = 0.0
            reasons = []

            # Source bonus: agent/auto suggestions are more relevant
            source_scores = {
                ClipSource.AGENT: 0.4,
                ClipSource.AUTO: 0.3,
                ClipSource.SNIPPET: 0.25,
                ClipSource.OCR: 0.15,
                ClipSource.USER: 0.1,
            }
            score += source_scores.get(item.source, 0.1)
            if item.source == ClipSource.AGENT:
                reasons.append("sugerowane przez agenta")

            # Category match bonus
            if item.category == category.value:
                score += 0.2
                reasons.append(f"skopiowane w {category.value}")

            # Recency bonus (decays over 5 minutes)
            age_seconds = now - item.timestamp
            recency = max(0, 1.0 - age_seconds / 300)
            score += recency * 0.2
            if age_seconds < 60:
                reasons.append("niedawno skopiowane")

            # Keyword match bonus
            item_lower = item.text.lower()
            keyword_hits = sum(1 for kw in keywords if kw in item_lower)
            if keyword_hits > 0:
                score += min(keyword_hits * 0.05, 0.2)
                reasons.append(f"pasuje do kontekstu ({keyword_hits} słów)")

            # Screen text relevance (words from item appear in screen)
            if screen_text:
                item_words = set(item.text.lower().split())
                screen_words = set(screen_text.lower().split())
                overlap = len(item_words & screen_words)
                if overlap > 0:
                    score += min(overlap * 0.03, 0.15)
                    reasons.append(f"widoczne na ekranie")

            # Pinned bonus
            if item.pinned:
                score += 0.1
                reasons.append("przypięte")

            # Usage bonus (frequently pasted)
            if item.used_count > 0:
                score += min(item.used_count * 0.02, 0.1)

            suggestions.append(PasteSuggestion(
                text=item.text,
                label=item.label,
                source=item.source,
                score=min(score, 1.0),
                reason=", ".join(reasons) if reasons else "w schowku",
            ))

        # Sort by score descending, take top N
        suggestions.sort(key=lambda s: s.score, reverse=True)
        return suggestions[:max_suggestions]


# ===== 4. Form Filler Snippets =====

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


class SnippetStore:
    """Simple key→value snippet store for form filling."""

    def __init__(self):
        self._snippets: Dict[str, Snippet] = {}

    def add(self, trigger: str, expansion: str, label: str = "", category: str = ""):
        self._snippets[trigger] = Snippet(
            trigger=trigger,
            expansion=expansion,
            label=label,
            category=category,
        )

    def remove(self, trigger: str) -> bool:
        return self._snippets.pop(trigger, None) is not None

    def expand(self, text: str, category: str = "") -> Optional[str]:
        """Try to expand a trigger. Returns expansion or None."""
        snippet = self._snippets.get(text)
        if not snippet:
            return None
        if snippet.category and snippet.category != category:
            return None
        return snippet.expansion

    def get_all(self) -> List[Snippet]:
        return list(self._snippets.values())

    def __len__(self) -> int:
        return len(self._snippets)


# ===== 5. Clipboard Manager (Orchestrator) =====

class ClipboardManager:
    """
    Orchestrates clipboard intelligence: queue, auto-copy, paste suggestions, snippets.

    Single entry point for the pipeline step and API routes.
    """

    def __init__(self, max_items: int = 20):
        self.queue = ClipboardQueue(max_items=max_items)
        self.copy_engine = ContextCopyEngine()
        self.suggester = PasteSuggester()
        self.snippets = SnippetStore()

        # Stats
        self._total_auto_copies: int = 0
        self._total_suggestions: int = 0
        self._total_pastes: int = 0

        logger.info("ClipboardManager initialized", max_items=max_items)

    def push(
        self,
        text: str,
        source: ClipSource = ClipSource.USER,
        category: str = "",
        label: str = "",
    ) -> Optional[ClipboardItem]:
        """Add item to clipboard queue."""
        return self.queue.push(text, source=source, category=category, label=label)

    def scan_and_copy(
        self,
        screen_text: str,
        category: AppCategory,
        max_items: int = 3,
    ) -> List[AutoCopyResult]:
        """
        Scan screen text and auto-add detected items to queue.

        Returns the list of auto-copied items.
        """
        results = self.copy_engine.scan(screen_text, category, max_results=max_items)
        for r in results:
            self.queue.push(
                r.text,
                source=r.source,
                category=category.value,
                label=r.label,
            )
            self._total_auto_copies += 1
        return results

    def suggest_paste(
        self,
        category: AppCategory,
        screen_text: str = "",
        max_suggestions: int = 3,
    ) -> List[PasteSuggestion]:
        """Get paste suggestions for current context."""
        suggestions = self.suggester.suggest(
            self.queue, category, screen_text, max_suggestions
        )
        self._total_suggestions += len(suggestions)
        return suggestions

    def mark_pasted(self, text: str):
        """Record that an item was pasted (boost future relevance)."""
        if text in self.queue:
            item = self.queue._text_index[text]
            item.used_count += 1
            self._total_pastes += 1

    def get_stats(self) -> Dict:
        return {
            "queue_size": len(self.queue),
            "pinned_count": sum(1 for i in self.queue.get_all() if i.pinned),
            "total_auto_copies": self._total_auto_copies,
            "total_suggestions": self._total_suggestions,
            "total_pastes": self._total_pastes,
            "snippet_count": len(self.snippets),
            "sources": {
                s.value: len(self.queue.get_by_source(s))
                for s in ClipSource
            },
        }


# ===== 6. Selection Analyzer =====

# Rules: (pattern, category_filter_or_None, response_template)
SELECTION_ANALYSIS_RULES: List[Dict] = [
    # Error messages
    {
        "pattern": r"(Traceback \(most recent call last\):.*?)(?:\n\n|\Z)",
        "flags": re.DOTALL,
        "label": "Python Traceback",
        "response": "🐍 **Python Traceback**\nOstatni błąd: `{last_line}`\n\n💡 Sprawdź plik i linię wskazaną w traceback.",
    },
    {
        "pattern": r"ModuleNotFoundError:\s+No module named ['\"]?(\w+)['\"]?",
        "label": "Brakujący moduł Python",
        "response": "📦 **Brakujący moduł:** `{1}`\n\n```\npip install {1}\n```",
        "clipboard": "pip install {1}",
    },
    {
        "pattern": r"Cannot find module ['\"]([^'\"]+)['\"]",
        "label": "Brakujący moduł Node.js",
        "response": "📦 **Brakujący moduł:** `{1}`\n\n```\nnpm install {1}\n```",
        "clipboard": "npm install {1}",
    },
    {
        "pattern": r"(TypeError|ValueError|KeyError|AttributeError|NameError|IndexError):\s+(.+)",
        "label": "Wyjątek Python",
        "response": "🐛 **{1}:** `{2}`\n\n💡 Sprawdź typy danych i nazwy zmiennych.",
    },
    # Git
    {
        "pattern": r"(fatal|error):\s+(.*(?:push|pull|merge|rebase|checkout).*)",
        "label": "Błąd Git",
        "response": "🔀 **Git error:** `{2}`\n\n💡 Spróbuj: `git status` → `git stash` → ponów operację.",
        "clipboard": "git status",
    },
    {
        "pattern": r"CONFLICT.*?Merge conflict in (.+)",
        "label": "Konflikt merge",
        "response": "⚠️ **Konflikt merge** w: `{1}`\n\n```\ngit diff --name-only --diff-filter=U\n```",
        "clipboard": "git diff --name-only --diff-filter=U",
    },
    # URLs
    {
        "pattern": r"(https?://[^\s<>\"']+)",
        "label": "URL",
        "response": "🔗 **URL wykryty:**\n`{1}`\n\n💡 Kliknij aby otworzyć lub skopiuj.",
        "clipboard": "{1}",
    },
    # IP addresses / ports
    {
        "pattern": r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::(\d+))?)",
        "label": "Adres IP",
        "response": "🌐 **Adres IP:** `{1}`\n\n💡 Sprawdź połączenie: `ping {1}`",
        "clipboard": "{1}",
    },
    # File paths
    {
        "pattern": r"(/(?:home|tmp|var|etc|opt|usr|root)/[\w/.@+-]+)",
        "label": "Ścieżka pliku",
        "response": "📁 **Ścieżka:** `{1}`\n\n💡 `ls -la {1}` lub `cat {1}`",
        "clipboard": "{1}",
    },
    # JSON detection
    {
        "pattern": r"^\s*[\[{]",
        "label": "JSON",
        "response": "📋 **JSON wykryty** ({length} znaków)\n\n💡 Sformatuj: `echo '...' | python -m json.tool`",
    },
    # Shell commands
    {
        "pattern": r"^\s*(?:sudo\s+)?(?:apt|yum|dnf|pacman|brew|pip|npm|cargo|docker|git|make|systemctl)\s+.+",
        "label": "Komenda shell",
        "response": "🖥️ **Komenda:** `{0}`\n\n💡 Gotowa do wykonania w terminalu.",
        "clipboard": "{0}",
    },
    # Email addresses
    {
        "pattern": r"([\w.+-]+@[\w-]+\.[\w.-]+)",
        "label": "Adres email",
        "response": "📧 **Email:** `{1}`",
        "clipboard": "{1}",
    },
]


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


class SelectionAnalyzer:
    """
    Analyzes user-selected text using pattern matching rules.

    Zero LLM — pure regex + templates. Optionally passes to LLM
    if no rule matches and analyzer is available.
    """

    def __init__(self, rules: Optional[List[Dict]] = None):
        self._rules = rules or SELECTION_ANALYSIS_RULES
        self._compiled = []
        for r in self._rules:
            flags = r.get("flags", re.IGNORECASE | re.MULTILINE)
            self._compiled.append((r, re.compile(r["pattern"], flags)))

    def analyze(self, text: str) -> SelectionAnalysis:
        """Analyze selected text and return structured response."""
        text = text.strip()
        if not text:
            return SelectionAnalysis(
                text="", label="Pusty tekst", response="⚠️ Brak zaznaczonego tekstu.",
                rule_matched=False,
            )

        for rule, compiled in self._compiled:
            match = compiled.search(text)
            if not match:
                continue

            # Build response from template
            response = rule["response"]
            clipboard_text = rule.get("clipboard", "")

            # Replace {0} with full match
            response = response.replace("{0}", match.group(0)[:100])
            clipboard_text = clipboard_text.replace("{0}", match.group(0))

            # Replace {1}, {2}, ... with capture groups
            for i, g in enumerate(match.groups(), 1):
                if g:
                    response = response.replace(f"{{{i}}}", g[:100])
                    clipboard_text = clipboard_text.replace(f"{{{i}}}", g)

            # Special placeholders
            response = response.replace("{length}", str(len(text)))

            # For traceback: extract last line
            if "{last_line}" in response:
                lines = text.strip().split("\n")
                last_line = lines[-1] if lines else ""
                response = response.replace("{last_line}", last_line[:120])

            return SelectionAnalysis(
                text=text,
                label=rule["label"],
                response=response,
                clipboard_text=clipboard_text.strip(),
            )

        # No rule matched — generic response
        word_count = len(text.split())
        line_count = text.count("\n") + 1
        return SelectionAnalysis(
            text=text,
            label="Tekst",
            response=f"📝 **Zaznaczony tekst** ({word_count} słów, {line_count} linii)\n\n💡 Skopiowany do schowka.",
            clipboard_text=text,
            rule_matched=False,
        )


# ===== Factory =====

@nfo.log_call(level="INFO")
def create_clipboard_manager_from_env(settings=None) -> ClipboardManager:
    """Create ClipboardManager from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return ClipboardManager(max_items=settings.clipboard_max_items)
