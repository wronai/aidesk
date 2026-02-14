"""Tests for clipboard_intel.py — ClipboardQueue, ContextCopyEngine, PasteSuggester, ClipboardManager."""
import os
import sys
import time

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipboard_intel import (
    ClipboardItem, ClipboardQueue, ClipSource,
    ContextCopyEngine, AutoCopyResult, AUTO_COPY_RULES,
    PasteSuggester, PasteSuggestion,
    SnippetStore, Snippet,
    ClipboardManager, create_clipboard_manager_from_env,
)
from window_aware import AppCategory


# ===== ClipboardItem =====

class TestClipboardItem:
    def test_defaults(self):
        item = ClipboardItem(text="hello")
        assert item.source == ClipSource.USER
        assert item.timestamp > 0
        assert item.label == "hello"
        assert item.used_count == 0
        assert item.pinned is False

    def test_label_truncated(self):
        item = ClipboardItem(text="x" * 100)
        assert len(item.label) <= 60

    def test_label_newlines_stripped(self):
        item = ClipboardItem(text="line1\nline2\nline3")
        assert "\n" not in item.label

    def test_to_dict(self):
        item = ClipboardItem(text="test", source=ClipSource.AGENT, category="terminal")
        d = item.to_dict()
        assert d["text"] == "test"
        assert d["source"] == "agent"
        assert d["category"] == "terminal"


# ===== ClipboardQueue =====

class TestClipboardQueue:
    def test_push_and_get(self):
        q = ClipboardQueue(max_items=5)
        q.push("a")
        q.push("b")
        assert len(q) == 2
        assert q.get_recent(1)[0].text == "b"

    def test_dedup_promotes(self):
        q = ClipboardQueue()
        q.push("a")
        q.push("b")
        q.push("a")  # promote
        assert len(q) == 2
        assert q.get_recent(1)[0].text == "a"

    def test_dedup_increments_used_count(self):
        q = ClipboardQueue()
        q.push("a")
        q.push("a")
        assert q.get_recent(1)[0].used_count == 1

    def test_empty_text_ignored(self):
        q = ClipboardQueue()
        result = q.push("")
        assert result is None
        assert len(q) == 0

    def test_whitespace_only_ignored(self):
        q = ClipboardQueue()
        result = q.push("   ")
        assert result is None

    def test_eviction(self):
        q = ClipboardQueue(max_items=3)
        q.push("a")
        q.push("b")
        q.push("c")
        q.push("d")  # evicts "a"
        assert len(q) == 3
        assert "a" not in q
        assert "d" in q

    def test_pinned_survives_eviction(self):
        q = ClipboardQueue(max_items=2)
        q.push("a")
        q.pin("a")
        q.push("b")
        q.push("c")  # should evict "b", not "a"
        assert "a" in q
        assert "b" not in q
        assert "c" in q

    def test_pin_unpin(self):
        q = ClipboardQueue()
        q.push("x")
        assert q.pin("x") is True
        assert q.pin("missing") is False
        assert q.unpin("x") is True

    def test_remove(self):
        q = ClipboardQueue()
        q.push("a")
        q.push("b")
        assert q.remove("a") is True
        assert len(q) == 1
        assert q.remove("missing") is False

    def test_clear_keeps_pinned(self):
        q = ClipboardQueue()
        q.push("a")
        q.push("b")
        q.pin("b")
        q.clear()
        assert len(q) == 1
        assert "b" in q

    def test_get_by_source(self):
        q = ClipboardQueue()
        q.push("a", source=ClipSource.USER)
        q.push("b", source=ClipSource.AGENT)
        q.push("c", source=ClipSource.AGENT)
        assert len(q.get_by_source(ClipSource.AGENT)) == 2
        assert len(q.get_by_source(ClipSource.USER)) == 1

    def test_contains(self):
        q = ClipboardQueue()
        q.push("hello")
        assert "hello" in q
        assert "missing" not in q


# ===== ContextCopyEngine =====

class TestContextCopyEngine:
    def test_terminal_error(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "Error: file not found\nSome other text",
            AppCategory.TERMINAL,
        )
        assert len(results) >= 1
        assert any("file not found" in r.text for r in results)

    def test_python_module_not_found(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "ModuleNotFoundError: No module named 'requests'",
            AppCategory.TERMINAL,
        )
        assert any("pip install requests" in r.text for r in results)

    def test_node_module_not_found(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "Cannot find module 'express'",
            AppCategory.IDE,
        )
        assert any("npm install express" in r.text for r in results)

    def test_url_extraction(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "Check https://example.com/docs for details",
            AppCategory.BROWSER,
        )
        assert any("https://example.com/docs" in r.text for r in results)

    def test_email_extraction(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "Contact user@example.com for info",
            AppCategory.EMAIL,
        )
        assert any("user@example.com" in r.text for r in results)

    def test_git_push_rejected(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "error: failed to push some refs to remote\nnon-fast-forward",
            AppCategory.TERMINAL,
        )
        assert any("git pull --rebase" in r.text for r in results)

    def test_category_filter(self):
        engine = ContextCopyEngine()
        # Terminal error should not match in BROWSER category
        results = engine.scan(
            "Error: file not found",
            AppCategory.BROWSER,
        )
        # Should not have terminal-specific error (but might get URL/email)
        assert not any("file not found" in r.text for r in results)

    def test_empty_text(self):
        engine = ContextCopyEngine()
        assert engine.scan("", AppCategory.TERMINAL) == []

    def test_max_results(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "Error: a\nError: b\nError: c\nError: d\nError: e\nError: f",
            AppCategory.TERMINAL,
            max_results=2,
        )
        assert len(results) <= 2

    def test_dedup_in_scan(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "https://example.com and again https://example.com",
            AppCategory.BROWSER,
        )
        texts = [r.text for r in results]
        assert len(set(texts)) == len(texts)

    def test_file_path_extraction(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "File: /home/user/project/main.py",
            AppCategory.TERMINAL,
        )
        assert any("/home/user/project/main.py" in r.text for r in results)

    def test_ip_extraction(self):
        engine = ContextCopyEngine()
        results = engine.scan(
            "Connected to 192.168.1.100:8080",
            AppCategory.TERMINAL,
        )
        assert any("192.168.1.100:8080" in r.text for r in results)


# ===== PasteSuggester =====

class TestPasteSuggester:
    def test_empty_queue(self):
        s = PasteSuggester()
        q = ClipboardQueue()
        assert s.suggest(q, AppCategory.TERMINAL) == []

    def test_agent_items_score_higher(self):
        s = PasteSuggester()
        q = ClipboardQueue()
        q.push("user text", source=ClipSource.USER, category="terminal")
        q.push("agent cmd", source=ClipSource.AGENT, category="terminal")
        suggestions = s.suggest(q, AppCategory.TERMINAL)
        assert len(suggestions) >= 2
        assert suggestions[0].source == ClipSource.AGENT

    def test_category_match_bonus(self):
        s = PasteSuggester()
        q = ClipboardQueue()
        q.push("terminal text", source=ClipSource.USER, category="terminal")
        q.push("browser text", source=ClipSource.USER, category="browser")
        suggestions = s.suggest(q, AppCategory.TERMINAL)
        # Terminal item should score higher in terminal context
        terminal_idx = next(i for i, s in enumerate(suggestions) if "terminal" in s.text)
        browser_idx = next(i for i, s in enumerate(suggestions) if "browser" in s.text)
        assert terminal_idx < browser_idx

    def test_max_suggestions(self):
        s = PasteSuggester()
        q = ClipboardQueue()
        for i in range(10):
            q.push(f"item {i}")
        suggestions = s.suggest(q, AppCategory.TERMINAL, max_suggestions=3)
        assert len(suggestions) <= 3

    def test_pinned_bonus(self):
        s = PasteSuggester()
        q = ClipboardQueue()
        q.push("unpinned", source=ClipSource.USER, category="terminal")
        q.push("pinned", source=ClipSource.USER, category="terminal")
        q.pin("pinned")
        suggestions = s.suggest(q, AppCategory.TERMINAL)
        pinned_score = next(s.score for s in suggestions if s.text == "pinned")
        unpinned_score = next(s.score for s in suggestions if s.text == "unpinned")
        assert pinned_score > unpinned_score

    def test_keyword_match(self):
        s = PasteSuggester()
        q = ClipboardQueue()
        q.push("git push origin main", source=ClipSource.USER, category="terminal")
        q.push("random text", source=ClipSource.USER, category="terminal")
        suggestions = s.suggest(q, AppCategory.TERMINAL)
        # git keyword should boost terminal item
        assert suggestions[0].text == "git push origin main"


# ===== SnippetStore =====

class TestSnippetStore:
    def test_add_and_expand(self):
        store = SnippetStore()
        store.add(";;email", "user@example.com")
        assert store.expand(";;email") == "user@example.com"

    def test_expand_missing(self):
        store = SnippetStore()
        assert store.expand(";;missing") is None

    def test_category_filter(self):
        store = SnippetStore()
        store.add(";;cmd", "docker ps", category="terminal")
        assert store.expand(";;cmd", category="terminal") == "docker ps"
        assert store.expand(";;cmd", category="browser") is None

    def test_remove(self):
        store = SnippetStore()
        store.add(";;x", "value")
        assert store.remove(";;x") is True
        assert store.remove(";;x") is False
        assert store.expand(";;x") is None

    def test_get_all(self):
        store = SnippetStore()
        store.add(";;a", "1")
        store.add(";;b", "2")
        assert len(store.get_all()) == 2

    def test_len(self):
        store = SnippetStore()
        assert len(store) == 0
        store.add(";;x", "y")
        assert len(store) == 1


# ===== ClipboardManager =====

class TestClipboardManager:
    def test_push(self):
        mgr = ClipboardManager(max_items=5)
        item = mgr.push("hello")
        assert item.text == "hello"
        assert len(mgr.queue) == 1

    def test_scan_and_copy(self):
        mgr = ClipboardManager()
        results = mgr.scan_and_copy(
            "Error: connection refused\nhttps://example.com",
            AppCategory.TERMINAL,
        )
        assert len(results) >= 1
        assert len(mgr.queue) >= 1
        assert mgr._total_auto_copies >= 1

    def test_suggest_paste(self):
        mgr = ClipboardManager()
        mgr.push("git pull", source=ClipSource.AGENT, category="terminal")
        suggestions = mgr.suggest_paste(AppCategory.TERMINAL)
        assert len(suggestions) >= 1

    def test_mark_pasted(self):
        mgr = ClipboardManager()
        mgr.push("test")
        mgr.mark_pasted("test")
        assert mgr._total_pastes == 1
        item = mgr.queue.get_recent(1)[0]
        assert item.used_count == 1

    def test_get_stats(self):
        mgr = ClipboardManager()
        mgr.push("a")
        stats = mgr.get_stats()
        assert stats["queue_size"] == 1
        assert "total_auto_copies" in stats
        assert "sources" in stats

    def test_factory(self, monkeypatch):
        monkeypatch.setenv("CLIPBOARD_MAX_ITEMS", "10")
        mgr = create_clipboard_manager_from_env()
        assert mgr.queue.max_items == 10


# ===== ClipboardStep (pipeline integration) =====

class TestClipboardStep:
    @pytest.mark.asyncio
    async def test_step_runs(self):
        from unittest.mock import MagicMock, AsyncMock
        from pipeline import ClipboardStep, PipelineContext, PipelineProfile
        from event_bus import EventBus

        mgr = ClipboardManager()
        step = ClipboardStep(mgr)

        ctx = PipelineContext()
        ctx.profile = PipelineProfile.NORMAL.value
        ctx.analysis_result = {"text": "Error: file not found"}
        ctx.active_window = MagicMock()
        ctx.active_window.category = AppCategory.TERMINAL

        bus = EventBus(enable_store=False)

        assert step.can_run(ctx) is True
        ctx = await step.execute(ctx, bus)

        # Should have auto-copied the error
        assert len(mgr.queue) >= 1

    @pytest.mark.asyncio
    async def test_step_skipped_on_fast(self):
        from pipeline import ClipboardStep, PipelineContext, PipelineProfile

        mgr = ClipboardManager()
        step = ClipboardStep(mgr)

        ctx = PipelineContext()
        ctx.profile = PipelineProfile.FAST.value
        assert step.can_run(ctx) is False

    @pytest.mark.asyncio
    async def test_step_pushes_agent_actions(self):
        from unittest.mock import MagicMock
        from pipeline import ClipboardStep, PipelineContext, PipelineProfile
        from event_bus import EventBus

        mgr = ClipboardManager()
        step = ClipboardStep(mgr)

        ctx = PipelineContext()
        ctx.profile = PipelineProfile.NORMAL.value
        ctx.analysis_result = {"text": "test"}
        ctx.active_window = MagicMock()
        ctx.active_window.category = AppCategory.TERMINAL
        ctx.agent_actions = [{"command": "Error: connection refused", "description": "Show error"}]

        bus = EventBus(enable_store=False)
        ctx = await step.execute(ctx, bus)

        # Agent action text matching terminal error pattern should be in clipboard queue
        assert any("connection refused" in i.text for i in mgr.queue.get_all())


# ===== SelectionAnalyzer =====

class TestSelectionAnalyzer:
    def _analyzer(self):
        from clipboard_intel import SelectionAnalyzer
        return SelectionAnalyzer()

    def test_empty_text(self):
        result = self._analyzer().analyze("")
        assert result.rule_matched is False
        assert "Pusty" in result.label

    def test_python_module_error(self):
        result = self._analyzer().analyze("ModuleNotFoundError: No module named 'flask'")
        assert result.label == "Brakujący moduł Python"
        assert "pip install flask" in result.response
        assert result.clipboard_text == "pip install flask"

    def test_node_module_error(self):
        result = self._analyzer().analyze("Cannot find module 'express'")
        assert "npm install express" in result.response
        assert result.clipboard_text == "npm install express"

    def test_python_exception(self):
        result = self._analyzer().analyze("TypeError: unsupported operand type(s)")
        assert "TypeError" in result.response
        assert result.rule_matched is True

    def test_git_error(self):
        result = self._analyzer().analyze("fatal: unable to push to remote")
        assert "Git" in result.label
        assert result.clipboard_text == "git status"

    def test_merge_conflict(self):
        result = self._analyzer().analyze("CONFLICT (content): Merge conflict in src/main.py")
        assert "Konflikt" in result.label
        assert "src/main.py" in result.response

    def test_url_detection(self):
        result = self._analyzer().analyze("Check https://example.com/docs")
        assert result.label == "URL"
        assert result.clipboard_text == "https://example.com/docs"

    def test_ip_detection(self):
        result = self._analyzer().analyze("Server at 10.0.0.1:3000")
        assert "10.0.0.1:3000" in result.response

    def test_file_path(self):
        result = self._analyzer().analyze("/home/user/project/main.py")
        assert "Ścieżka" in result.label
        assert result.clipboard_text == "/home/user/project/main.py"

    def test_shell_command(self):
        result = self._analyzer().analyze("docker compose up -d")
        assert "Komenda" in result.label
        assert result.clipboard_text == "docker compose up -d"

    def test_email_detection(self):
        result = self._analyzer().analyze("user@example.com")
        assert result.label == "Adres email"
        assert result.clipboard_text == "user@example.com"

    def test_generic_text(self):
        result = self._analyzer().analyze("just some random text here")
        assert result.rule_matched is False
        assert "Zaznaczony tekst" in result.response
        assert result.clipboard_text == "just some random text here"

    def test_to_dict(self):
        result = self._analyzer().analyze("pip install flask")
        d = result.to_dict()
        assert "text" in d
        assert "label" in d
        assert "response" in d
        assert "clipboard_text" in d
        assert "rule_matched" in d

    def test_traceback(self):
        tb = """Traceback (most recent call last):
  File "main.py", line 10, in <module>
    foo()
NameError: name 'foo' is not defined"""
        result = self._analyzer().analyze(tb)
        assert "Traceback" in result.label
        assert "foo" in result.response
