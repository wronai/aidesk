"""Tests for ProfileSelector decision emissions via nfo."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.context import PipelineContext, PipelineProfile, ProfileSelector


class TestProfileSelectorDecisions:

    def _make_selector(self, **kwargs):
        return ProfileSelector(**kwargs)

    def test_default_emits_normal(self):
        sel = self._make_selector(full_interval=9999)
        sel._last_full_time = 9999999999  # skip periodic full
        ctx = PipelineContext()

        with patch("pipeline.context._emit_profile_decision") as mock_emit:
            result = sel.select(ctx)

        assert result == PipelineProfile.NORMAL
        mock_emit.assert_called_once_with("normal", "default")

    def test_forced_profile_emits_forced(self):
        mock_logger = MagicMock()
        sel = self._make_selector(force_profile=PipelineProfile.FULL)
        ctx = PipelineContext()

        with patch("pipeline.context._emit_profile_decision") as mock_emit:
            result = sel.select(ctx)

        assert result == PipelineProfile.FULL
        mock_emit.assert_called_once_with("full", "forced")

    def test_periodic_full_scan_emits_periodic(self):
        sel = self._make_selector(full_interval=0)  # always trigger full
        ctx = PipelineContext()

        with patch("pipeline.context._emit_profile_decision") as mock_emit:
            result = sel.select(ctx)

        assert result == PipelineProfile.FULL
        mock_emit.assert_called_once()
        args = mock_emit.call_args
        assert args[0][0] == "full"
        assert args[0][1] == "periodic_scan"
        assert args[1]["interval"] == 0

    def test_idle_emits_fast(self):
        sel = self._make_selector(full_interval=9999)
        sel._last_full_time = 9999999999  # far future, skip full
        ctx = PipelineContext()

        capture = MagicMock()
        capture.consecutive_unchanged = 100
        capture.idle_threshold = 30

        with patch("pipeline.context._emit_profile_decision") as mock_emit:
            result = sel.select(ctx, capture=capture)

        assert result == PipelineProfile.FAST
        mock_emit.assert_called_once()
        args = mock_emit.call_args
        assert args[0][0] == "fast"
        assert args[0][1] == "idle"
        assert "consecutive_fast" in args[1]

    def test_idle_with_vlm_ocr_emits_normal(self):
        ocr_mgr = MagicMock()
        ocr_mgr.active_engine_name = "vlm_ocr"
        sel = self._make_selector(full_interval=9999, ocr_manager=ocr_mgr)
        sel._last_full_time = 9999999999
        ctx = PipelineContext()

        capture = MagicMock()
        capture.consecutive_unchanged = 100
        capture.idle_threshold = 30

        with patch("pipeline.context._emit_profile_decision") as mock_emit:
            result = sel.select(ctx, capture=capture)

        assert result == PipelineProfile.NORMAL
        mock_emit.assert_called_once_with("normal", "idle_but_vlm_ocr_active")

    def test_decision_emitted_every_call(self):
        """Every select() call emits exactly one decision."""
        sel = self._make_selector(full_interval=9999)
        sel._last_full_time = 9999999999
        ctx = PipelineContext()

        with patch("pipeline.context._emit_profile_decision") as mock_emit:
            sel.select(ctx)
            sel.select(ctx)
            sel.select(ctx)

        assert mock_emit.call_count == 3
