"""Tests for AnalysisLoop — testable single-tick extraction from screen_analysis_loop."""
import asyncio
import os
import sys
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_loop import AnalysisLoop
from pipeline import PipelineContext, PipelineProfile


# ===== Helpers =====

def _make_state():
    """Minimal app_state with mocked components."""
    pipeline_mock = MagicMock()
    pipeline_mock.get_step_names.return_value = ["scan", "analyze"]
    pipeline_mock.steps = [MagicMock(), MagicMock()]

    # Make pipeline.run return a completed context
    async def mock_run(ctx):
        ctx.steps_executed = ["scan", "analyze"]
        return ctx
    pipeline_mock.run = AsyncMock(side_effect=mock_run)

    selector_mock = MagicMock()
    selector_mock.select.return_value = PipelineProfile.NORMAL
    selector_mock.notify_active_window_changed = MagicMock()

    capture_mock = MagicMock()
    capture_mock.adaptive_interval = 1.0

    context_mock = MagicMock()
    context_mock.add = MagicMock()

    return {
        "pipeline": pipeline_mock,
        "profile_selector": selector_mock,
        "capture": capture_mock,
        "context": context_mock,
        "latest_window": None,
        "latest_organized_screen": None,
        "latest_analysis": "",
        "stats": {
            "start_time": time.time(),
            "total_screen_analyses": 0,
            "total_transcripts": 0,
            "total_errors": 0,
        },
    }


def _make_active_window(wid=100, title="Code", category_value="development"):
    win = MagicMock()
    win.window_id = wid
    win.title = title
    win.category = MagicMock(value=category_value)
    win.to_dict.return_value = {"title": title, "window_id": wid}
    return win


# ===== Tests =====

class TestAnalysisLoopInit:
    def test_constructor(self):
        state = _make_state()
        broadcast = AsyncMock()
        loop = AnalysisLoop(state, broadcast)
        assert loop.state is state
        assert loop.broadcast is broadcast
        assert loop._prev_active_wid == 0

    def test_properties(self):
        state = _make_state()
        loop = AnalysisLoop(state, AsyncMock())
        assert loop.pipeline is state["pipeline"]
        assert loop.profile_selector is state["profile_selector"]
        assert loop.capture is state["capture"]
        assert loop.context_mgr is state["context"]


class TestTick:
    @pytest.mark.asyncio
    async def test_basic_tick(self):
        state = _make_state()
        broadcast = AsyncMock()
        loop = AnalysisLoop(state, broadcast)

        ctx = await loop.tick()

        # Pipeline was called
        state["pipeline"].run.assert_called_once()
        # Profile was selected
        state["profile_selector"].select.assert_called_once()
        # Returns context
        assert isinstance(ctx, PipelineContext)
        assert ctx.profile == PipelineProfile.NORMAL.value

    @pytest.mark.asyncio
    async def test_tick_with_active_window(self):
        state = _make_state()
        broadcast = AsyncMock()
        win = _make_active_window(wid=42)

        async def mock_run(ctx):
            ctx.active_window = win
            ctx.steps_executed = ["scan"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        ctx = await loop.tick()

        # Window state updated
        assert state["latest_window"]["window_id"] == 42
        # Broadcast called with window data
        broadcast.assert_any_call("window", {"title": "Code", "window_id": 42})

    @pytest.mark.asyncio
    async def test_tick_with_analysis(self):
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            ctx.analysis_result = {"text": "Test analysis", "tokens": 100, "cost": 0.01}
            ctx.broadcast_data = {"text": "Test analysis"}
            ctx.steps_executed = ["analyze"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        ctx = await loop.tick()

        assert state["latest_analysis"] == "Test analysis"
        assert state["stats"]["total_screen_analyses"] == 1
        state["context"].add.assert_called_once()
        broadcast.assert_any_call("analysis", {"text": "Test analysis"})

    @pytest.mark.asyncio
    async def test_tick_with_organized_screen(self):
        state = _make_state()
        broadcast = AsyncMock()

        org = MagicMock()
        org.total_windows = 3
        org.screen_summary = "3 windows"
        org.active_app = None
        org.by_category = {"development": []}
        org.to_dict.return_value = {"total": 3}

        async def mock_run(ctx):
            ctx.organized_screen = org
            ctx.steps_executed = ["crop"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        assert state["latest_organized_screen"] == {"total": 3}
        broadcast.assert_any_call("organized_screen", {
            "total_windows": 3,
            "summary": "3 windows",
            "active_app": None,
            "categories": ["development"],
        })


    @pytest.mark.asyncio
    async def test_tick_with_clipboard_broadcast(self):
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            ctx.analysis_result = {"text": "some text"}
            ctx.clipboard_suggestions = [{"text": "suggestion"}]
            ctx.clipboard_auto_copies = [{"text": "auto_copy"}]
            ctx.steps_executed = ["clipboard"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        broadcast.assert_any_call("clipboard_suggestions", {
            "suggestions": [{"text": "suggestion"}],
            "auto_copies": [{"text": "auto_copy"}],
        })


class TestWindowTracking:
    @pytest.mark.asyncio
    async def test_window_change_notifies_selector(self):
        state = _make_state()
        broadcast = AsyncMock()
        win = _make_active_window(wid=10)

        async def mock_run(ctx):
            ctx.active_window = win
            ctx.steps_executed = ["scan"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        state["profile_selector"].notify_active_window_changed.assert_called_once_with(10)
        assert loop._prev_active_wid == 10

    @pytest.mark.asyncio
    async def test_same_window_no_notify(self):
        state = _make_state()
        broadcast = AsyncMock()
        win = _make_active_window(wid=10)

        async def mock_run(ctx):
            ctx.active_window = win
            ctx.steps_executed = ["scan"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        loop._prev_active_wid = 10  # same window
        await loop.tick()

        state["profile_selector"].notify_active_window_changed.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_change_second_tick(self):
        state = _make_state()
        broadcast = AsyncMock()

        call_count = 0
        async def mock_run(ctx):
            nonlocal call_count
            call_count += 1
            ctx.active_window = _make_active_window(wid=call_count * 10)
            ctx.steps_executed = ["scan"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()
        await loop.tick()

        assert state["profile_selector"].notify_active_window_changed.call_count == 2


class TestStoreAnalysis:
    @pytest.mark.asyncio
    async def test_no_analysis_no_store(self):
        state = _make_state()
        broadcast = AsyncMock()

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        state["context"].add.assert_not_called()
        assert state["stats"]["total_screen_analyses"] == 0

    @pytest.mark.asyncio
    async def test_analysis_stored_with_metadata(self):
        state = _make_state()
        broadcast = AsyncMock()
        win = _make_active_window(wid=5, title="Terminal")

        async def mock_run(ctx):
            ctx.analysis_result = {"text": "hello world", "tokens": 50}
            ctx.active_window = win
            ctx.steps_executed = ["analyze"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        state["context"].add.assert_called_once()
        call_kwargs = state["context"].add.call_args
        assert call_kwargs[1]["content"] == "hello world"
        assert call_kwargs[1]["context_type"] == "screen"
        meta = call_kwargs[1]["metadata"]
        assert meta["tokens"] == 50
        assert meta["window"] == "Terminal"


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_pipeline_step_errors_logged(self):
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            ctx.errors = [{"step": "analyze", "error": "LLM timeout"}]
            ctx.steps_executed = []
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        ctx = await loop.tick()

        assert len(ctx.errors) == 1

    @pytest.mark.asyncio
    async def test_agent_actions_broadcast(self):
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            ctx.analysis_result = {"text": "test"}
            ctx.agent_actions = [{"cmd": "git push"}]
            ctx.steps_executed = ["analyze"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        broadcast.assert_any_call("agent_actions", {"actions": [{"cmd": "git push"}]})


class TestClipboardBroadcastRegression:
    """P2a: Regression tests for clipboard fields in _broadcast_state.

    Ensures PipelineContext always has clipboard_suggestions and
    clipboard_auto_copies fields, and that _broadcast_state handles
    them correctly in all scenarios.
    """

    def test_pipeline_context_has_clipboard_fields(self):
        """PipelineContext must always have clipboard fields with defaults."""
        ctx = PipelineContext()
        assert hasattr(ctx, "clipboard_suggestions")
        assert hasattr(ctx, "clipboard_auto_copies")
        assert ctx.clipboard_suggestions == []
        assert ctx.clipboard_auto_copies == []

    @pytest.mark.asyncio
    async def test_broadcast_clipboard_when_present(self):
        """When clipboard data exists, it should be broadcast."""
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            ctx.clipboard_suggestions = [{"text": "paste me"}]
            ctx.clipboard_auto_copies = [{"text": "auto"}]
            ctx.steps_executed = ["clipboard"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        broadcast.assert_any_call("clipboard_suggestions", {
            "suggestions": [{"text": "paste me"}],
            "auto_copies": [{"text": "auto"}],
        })

    @pytest.mark.asyncio
    async def test_no_clipboard_broadcast_when_empty(self):
        """When clipboard_suggestions is empty, no clipboard broadcast should occur."""
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            # clipboard fields stay at default (empty lists)
            ctx.steps_executed = ["scan"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        # Should NOT have broadcast clipboard_suggestions
        for call in broadcast.call_args_list:
            assert call[0][0] != "clipboard_suggestions"

    @pytest.mark.asyncio
    async def test_clipboard_suggestions_without_auto_copies(self):
        """Suggestions present but auto_copies empty — should still broadcast."""
        state = _make_state()
        broadcast = AsyncMock()

        async def mock_run(ctx):
            ctx.clipboard_suggestions = [{"text": "suggestion"}]
            # clipboard_auto_copies stays empty
            ctx.steps_executed = ["clipboard"]
            return ctx
        state["pipeline"].run = AsyncMock(side_effect=mock_run)

        loop = AnalysisLoop(state, broadcast)
        await loop.tick()

        broadcast.assert_any_call("clipboard_suggestions", {
            "suggestions": [{"text": "suggestion"}],
            "auto_copies": [],
        })
