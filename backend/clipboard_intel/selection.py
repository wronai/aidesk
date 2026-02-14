"""Selection analyzer — pattern-based analysis of user-selected text."""
import re
from typing import Dict, List, Optional

from .models import SelectionAnalysis


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
