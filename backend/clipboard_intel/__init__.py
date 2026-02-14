"""
Clipboard Intelligence package — smart clipboard queue, context-aware auto-copy, paste suggestions.

Re-exports all public symbols for backward compatibility with:
    from clipboard_intel import ClipboardManager, ClipSource, ...
"""
# Models
from .models import (
    ClipSource,
    ClipboardItem,
    AutoCopyResult,
    PasteSuggestion,
    Snippet,
    SelectionAnalysis,
)

# Queue
from .queue import ClipboardQueue

# Engines
from .engines import (
    AUTO_COPY_RULES,
    ContextCopyEngine,
    PasteSuggester,
)

# Selection
from .selection import (
    SELECTION_ANALYSIS_RULES,
    SelectionAnalyzer,
)

# Manager & factory
from .manager import (
    SnippetStore,
    ClipboardManager,
    create_clipboard_manager_from_env,
)

__all__ = [
    # Models
    "ClipSource",
    "ClipboardItem",
    "AutoCopyResult",
    "PasteSuggestion",
    "Snippet",
    "SelectionAnalysis",
    # Queue
    "ClipboardQueue",
    # Engines
    "AUTO_COPY_RULES",
    "ContextCopyEngine",
    "PasteSuggester",
    # Selection
    "SELECTION_ANALYSIS_RULES",
    "SelectionAnalyzer",
    # Manager
    "SnippetStore",
    "ClipboardManager",
    "create_clipboard_manager_from_env",
]
