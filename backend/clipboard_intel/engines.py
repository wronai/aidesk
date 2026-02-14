"""Context-aware auto-copy engine and paste suggester."""
import re
import time
from typing import Dict, List, Optional

from window_aware import AppCategory
from .models import ClipSource, AutoCopyResult, PasteSuggestion
from .queue import ClipboardQueue


# ===== Auto-Copy Rules =====

AUTO_COPY_RULES: List[Dict] = [
    # Terminal: copy last error
    {
        "categories": [AppCategory.TERMINAL],
        "pattern": r"(?:error|Error|ERROR)[:\s]+(.+?)(?:\n|$)",
        "extract": 1,
        "label": "Błąd terminala",
        "source": ClipSource.AUTO,
    },
    # Terminal: command not found → suggest install
    {
        "categories": [AppCategory.TERMINAL],
        "pattern": r"(\S+): command not found",
        "extract": 0,
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


# ===== Paste Suggestions =====

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
