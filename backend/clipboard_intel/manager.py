"""ClipboardManager orchestrator, SnippetStore, and factory."""
from typing import Dict, List, Optional

import nfo
import structlog

from window_aware import AppCategory
from .models import ClipSource, ClipboardItem, AutoCopyResult, PasteSuggestion, Snippet
from .queue import ClipboardQueue
from .engines import ContextCopyEngine, PasteSuggester

logger = structlog.get_logger()


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


# ===== Factory =====

@nfo.log_call(level="INFO")
def create_clipboard_manager_from_env(settings=None) -> ClipboardManager:
    """Create ClipboardManager from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return ClipboardManager(max_items=settings.clipboard_max_items)
