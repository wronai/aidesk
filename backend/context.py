"""
Context manager for maintaining conversation history.

.. deprecated::
    ContextManager is superseded by SemanticMemory (vector-based recall)
    and EventStore (CQRS event log). It will be removed in a future version.
    New code should use SemanticMemory for context enrichment and EventStore
    for audit/history. BuildContextStep already integrates both.
"""
import time
import warnings
from collections import deque
from typing import Dict, List, Literal
import nfo
import structlog

logger = structlog.get_logger()

ContextType = Literal["screen", "speech", "system"]


class ContextManager:
    """
    Manages conversation context with sliding window and summarization.

    .. deprecated::
        Use SemanticMemory for context recall and EventStore for history.
        This class will be removed in a future version.
    """

    _deprecation_warned = False

    def __init__(self, max_items: int = 20):
        """
        Initialize context manager.

        Args:
            max_items: Maximum number of context items to keep
        """
        if not ContextManager._deprecation_warned:
            warnings.warn(
                "ContextManager is deprecated — use SemanticMemory + EventStore instead. "
                "It will be removed in a future version.",
                DeprecationWarning,
                stacklevel=2,
            )
            ContextManager._deprecation_warned = True

        self.max_items = max_items
        self.history: deque = deque(maxlen=max_items)
        self.total_items = 0

        logger.info("Context manager initialized", max_items=max_items)

    def add(
        self,
        content: str,
        context_type: ContextType = "screen",
        metadata: Dict = None,
    ):
        """
        Add item to context.

        Args:
            content: Context content
            context_type: Type of context (screen, speech, system)
            metadata: Optional metadata dict
        """
        item = {
            "type": context_type,
            "content": content,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }

        self.history.append(item)
        self.total_items += 1

        logger.debug(
            "Context added",
            type=context_type,
            length=len(content),
            total_items=len(self.history),
        )

    def get_recent(self, n: int = 5, context_type: ContextType = None) -> List[Dict]:
        """
        Get recent context items.

        Args:
            n: Number of items to retrieve
            context_type: Filter by type (None = all types)

        Returns:
            List of context items
        """
        items = list(self.history)

        if context_type:
            items = [item for item in items if item["type"] == context_type]

        return items[-n:]

    def get_context_string(self, n: int = 5, max_length: int = 500) -> str:
        """
        Get recent context as formatted string.

        Args:
            n: Number of recent items
            max_length: Maximum total length

        Returns:
            Formatted context string
        """
        recent = self.get_recent(n)
        if not recent:
            return ""

        lines = []
        total_length = 0

        for item in reversed(recent):
            # Format timestamp
            ts = time.strftime("%H:%M:%S", time.localtime(item["timestamp"]))

            # Format type emoji
            type_emoji = {
                "screen": "🖥️",
                "speech": "🎤",
                "system": "⚙️",
            }.get(item["type"], "📝")

            # Truncate content if needed
            content = item["content"]
            if len(content) > 200:
                content = content[:197] + "..."

            line = f"{type_emoji} [{ts}] {content}"

            if total_length + len(line) > max_length:
                break

            lines.insert(0, line)
            total_length += len(line)

        return "\n".join(lines)

    def clear(self):
        """Clear all context."""
        self.history.clear()
        logger.info("Context cleared")

    def get_stats(self) -> Dict:
        """Get context statistics."""
        type_counts = {}
        for item in self.history:
            item_type = item["type"]
            type_counts[item_type] = type_counts.get(item_type, 0) + 1

        return {
            "current_items": len(self.history),
            "total_items": self.total_items,
            "max_items": self.max_items,
            "type_distribution": type_counts,
            "oldest_timestamp": (
                self.history[0]["timestamp"] if self.history else None
            ),
            "newest_timestamp": (
                self.history[-1]["timestamp"] if self.history else None
            ),
            "deprecated": True,
        }
