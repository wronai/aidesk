"""Smart clipboard queue with dedup, pinning, and ring buffer eviction."""
import time
from collections import deque
from typing import Dict, List

from .models import ClipboardItem, ClipSource


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
