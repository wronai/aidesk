"""Tests for AppBootstrap — testable startup/shutdown orchestration."""
import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import AppBootstrap, _init_optional


# ===== Helpers =====

def _make_state():
    """Create a minimal app_state dict for testing."""
    return {
        "context": MagicMock(),
        "subscribers": [],
        "stats": {"start_time": 1000000},
        "latest_analysis": "",
        "latest_transcript": "",
        "latest_window": None,
        "latest_organized_screen": None,
        "capture": None,
        "analyzer": None,
        "ocr_manager": None,
        "stt": None,
        "diagnostics": None,
        "window_manager": None,
        "profile_manager": None,
        "shell_agent": None,
        "process_scanner": None,
        "window_cropper": None,
        "event_bus": None,
        "pipeline": None,
        "profile_selector": None,
        "read_model": None,
        "command_handlers": None,
        "query_handlers": None,
        "multi_monitor": None,
        "semantic_memory": None,
        "action_library": None,
        "ocr_enhancer": None,
        "predictive_engine": None,
        "clipboard_manager": None,
    }


def _mock_settings(**kwargs):
    """Create a mock settings object."""
    settings = MagicMock()
    # Defaults
    settings.enable_window_aware = True
    settings.enable_shell_agent = True
    settings.enable_stt = True
    # Overrides
    for k, v in kwargs.items():
        setattr(settings, k, v)
    return settings


# ===== _init_optional =====

class TestInitOptional:
    def test_success(self):
        state = {}
        result = _init_optional(state, "comp", lambda: "value")
        assert result is True
        assert state["comp"] == "value"

    def test_failure(self):
        state = {}
        result = _init_optional(state, "comp", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        assert result is False
        assert "comp" not in state or state.get("comp") is None

    def test_with_kwargs(self):
        state = {}
        result = _init_optional(state, "comp", lambda x=1: x * 10, x=5)
        assert result is True
        assert state["comp"] == 50


# ===== AppBootstrap =====

class TestAppBootstrapInit:
    @patch("settings.get_settings")
    def test_constructor(self, mock_get_settings):
        mock_settings = _mock_settings()
        mock_get_settings.return_value = mock_settings
        
        state = _make_state()
        broadcast = AsyncMock()
        b = AppBootstrap(state, broadcast, version="1.0.0")
        
        assert b.state is state
        assert b.broadcast is broadcast
        assert b.version == "1.0.0"
        assert b.init_report == {}
        assert b.settings is mock_settings


class TestInitCore:
    @patch("settings.get_settings")
    @patch("bootstrap.create_capture_from_env")
    @patch("bootstrap.create_ocr_manager_from_env")
    @patch("bootstrap.create_analyzer_from_env")
    def test_initializes_core_components(self, mock_analyzer, mock_ocr, mock_capture, mock_get_settings):
        mock_settings = _mock_settings()
        mock_get_settings.return_value = mock_settings
        
        mock_capture.return_value = "capture_obj"
        mock_ocr.return_value = "ocr_obj"
        mock_analyzer.return_value = "analyzer_obj"

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_core()

        assert state["capture"] == "capture_obj"
        assert state["ocr_manager"] == "ocr_obj"
        assert state["analyzer"] == "analyzer_obj"
        
        # Verify settings passed to factories
        mock_capture.assert_called_with(settings=mock_settings)
        mock_ocr.assert_called_with(settings=mock_settings)
        mock_analyzer.assert_called_with(ocr_manager="ocr_obj", settings=mock_settings)
        
        assert b.init_report["capture"] is True
        assert b.init_report["ocr_manager"] is True
        assert b.init_report["analyzer"] is True


class TestInitWindow:
    @patch("settings.get_settings")
    @patch("bootstrap.create_shell_agent_from_env")
    @patch("bootstrap.create_profile_manager")
    @patch("bootstrap.create_window_manager_from_env")
    def test_all_enabled(self, mock_wm, mock_pm, mock_sa, mock_get_settings):
        mock_settings = _mock_settings(enable_window_aware=True, enable_shell_agent=True)
        mock_get_settings.return_value = mock_settings
        
        mock_wm.return_value = "wm"
        mock_pm.return_value = "pm"
        mock_sa.return_value = "sa"

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_window()

        assert state["window_manager"] == "wm"
        assert state["profile_manager"] == "pm"
        assert state["shell_agent"] == "sa"
        
        mock_wm.assert_called_with(settings=mock_settings)
        mock_pm.assert_called_with(settings=mock_settings)
        mock_sa.assert_called_with(settings=mock_settings)

    @patch("settings.get_settings")
    @patch("bootstrap.create_profile_manager")
    @patch("bootstrap.create_window_manager_from_env")
    def test_window_disabled(self, mock_wm, mock_pm, mock_get_settings):
        mock_settings = _mock_settings(enable_window_aware=False, enable_shell_agent=False)
        mock_get_settings.return_value = mock_settings
        
        mock_pm.return_value = "pm"

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_window()

        mock_wm.assert_not_called()
        assert state["profile_manager"] == "pm"

    @patch("settings.get_settings")
    @patch("bootstrap.create_profile_manager")
    @patch("bootstrap.create_window_manager_from_env")
    def test_window_manager_failure_graceful(self, mock_wm, mock_pm, mock_get_settings):
        mock_settings = _mock_settings(enable_window_aware=True)
        mock_get_settings.return_value = mock_settings
        
        mock_wm.side_effect = RuntimeError("no display")
        mock_pm.return_value = "pm"

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_window()

        assert b.init_report["window_manager"] is False
        assert state["profile_manager"] == "pm"


class TestInitScanners:
    @patch("settings.get_settings")
    @patch("bootstrap.create_window_cropper")
    @patch("bootstrap.create_process_scanner")
    def test_success(self, mock_ps, mock_wc, mock_get_settings):
        mock_settings = _mock_settings()
        mock_get_settings.return_value = mock_settings
        
        mock_ps.return_value = "ps"
        mock_wc.return_value = "wc"

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_scanners()

        assert state["process_scanner"] == "ps"
        assert state["window_cropper"] == "wc"
        
        mock_ps.assert_called_with(window_manager=None, settings=mock_settings)
        mock_wc.assert_called_with(process_scanner="ps", settings=mock_settings)
        
        assert b.init_report["process_scanner"] is True

    @patch("settings.get_settings")
    @patch("bootstrap.create_process_scanner")
    def test_failure_graceful(self, mock_ps, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        mock_ps.side_effect = RuntimeError("no xdotool")

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_scanners()

        assert b.init_report["process_scanner"] is False
        assert b.init_report["window_cropper"] is False


class TestInitTier1:
    @patch("settings.get_settings")
    @patch("bootstrap.create_clipboard_manager_from_env")
    @patch("bootstrap.create_predictive_engine_from_env")
    @patch("bootstrap.create_ocr_enhancer_from_env")
    @patch("bootstrap.create_action_library_from_env")
    @patch("bootstrap.create_semantic_memory_from_env")
    @patch("bootstrap.create_multi_monitor_from_env")
    def test_all_succeed(self, mock_mm, mock_sm, mock_al, mock_oe, mock_pe, mock_cm, mock_get_settings):
        mock_settings = _mock_settings()
        mock_get_settings.return_value = mock_settings
        
        for m in (mock_mm, mock_sm, mock_al, mock_oe, mock_pe, mock_cm):
            m.return_value = MagicMock()

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_tier1()

        assert b.init_report["multi_monitor"] is True
        assert b.init_report["semantic_memory"] is True
        assert b.init_report["action_library"] is True
        assert b.init_report["ocr_enhancer"] is True
        assert b.init_report["predictive_engine"] is True
        assert b.init_report["clipboard_manager"] is True
        
        mock_mm.assert_called_with(settings=mock_settings)

    @patch("settings.get_settings")
    @patch("bootstrap.create_clipboard_manager_from_env")
    @patch("bootstrap.create_predictive_engine_from_env")
    @patch("bootstrap.create_ocr_enhancer_from_env")
    @patch("bootstrap.create_action_library_from_env")
    @patch("bootstrap.create_semantic_memory_from_env")
    @patch("bootstrap.create_multi_monitor_from_env")
    def test_partial_failure(self, mock_mm, mock_sm, mock_al, mock_oe, mock_pe, mock_cm, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        
        mock_mm.return_value = MagicMock()
        mock_sm.side_effect = RuntimeError("no model")
        mock_al.return_value = MagicMock()
        mock_oe.side_effect = RuntimeError("no symspell")
        mock_pe.return_value = MagicMock()
        mock_cm.return_value = MagicMock()

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        b.init_tier1()

        assert b.init_report["multi_monitor"] is True
        assert b.init_report["semantic_memory"] is False
        assert b.init_report["action_library"] is True
        assert b.init_report["ocr_enhancer"] is False
        assert b.init_report["predictive_engine"] is True
        assert b.init_report["clipboard_manager"] is True


class TestInitPipeline:
    @patch("settings.get_settings")
    @patch("bootstrap.QueryHandlers")
    @patch("bootstrap.CommandHandlers")
    @patch("bootstrap.ReadModel")
    @patch("bootstrap.create_profile_selector")
    @patch("bootstrap.create_pipeline")
    @patch("bootstrap.create_event_bus")
    def test_pipeline_initialized(self, mock_bus, mock_pipe, mock_ps, mock_rm, mock_ch, mock_qh, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        
        mock_bus.return_value = MagicMock()
        mock_pipe.return_value = MagicMock()
        mock_ps.return_value = MagicMock()
        
        mock_rm_inst = MagicMock()
        mock_rm.return_value = mock_rm_inst
        
        mock_ch_inst = MagicMock()
        mock_ch.return_value = mock_ch_inst
        mock_qh_inst = MagicMock()
        mock_qh.return_value = mock_qh_inst

        state = _make_state()
        state["capture"] = MagicMock()
        state["analyzer"] = MagicMock()
        broadcast = AsyncMock()
        b = AppBootstrap(state, broadcast)
        b.init_pipeline()

        assert state["event_bus"] is not None
        assert state["pipeline"] is not None
        assert state["read_model"] is not None
        assert state["command_handlers"] is not None
        assert state["query_handlers"] is not None
        mock_ch_inst.set_broadcast.assert_called_once_with(broadcast)
        mock_ch_inst.register_all.assert_called_once()
        mock_qh_inst.register_all.assert_called_once()
        assert b.init_report["pipeline"] is True
        
        # Verify ReadModel snapshot loaded
        mock_rm_inst.load_snapshot.assert_called_once()


class TestShutdown:
    @patch("settings.get_settings")
    def test_cancels_tasks(self, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        state = _make_state()
        state["event_bus"] = None  # no bus to publish to
        b = AppBootstrap(state, AsyncMock())

        task1 = MagicMock()
        task2 = MagicMock()
        b._tasks = [task1, task2]

        asyncio.get_event_loop().run_until_complete(b.shutdown())

        task1.cancel.assert_called_once()
        task2.cancel.assert_called_once()

    @patch("settings.get_settings")
    def test_stops_stt(self, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        state = _make_state()
        state["event_bus"] = None
        stt_mock = MagicMock()
        stt_mock.stop = AsyncMock()
        state["stt"] = stt_mock

        b = AppBootstrap(state, AsyncMock())
        asyncio.get_event_loop().run_until_complete(b.shutdown())

        stt_mock.stop.assert_called_once()

    @patch("settings.get_settings")
    def test_emits_shutdown_event(self, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        state = _make_state()
        bus_mock = MagicMock()
        bus_mock.publish = AsyncMock()
        state["event_bus"] = bus_mock

        b = AppBootstrap(state, AsyncMock())
        asyncio.get_event_loop().run_until_complete(b.shutdown())

        bus_mock.publish.assert_called_once()
        # With typed_event, we check if publish was called with an Event object of correct type
        args, _ = bus_mock.publish.call_args
        event = args[0]
        assert event.type == "system.shutdown"
        
    @patch("settings.get_settings")
    def test_saves_readmodel_snapshot(self, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        state = _make_state()
        state["event_bus"] = None
        rm_mock = MagicMock()
        state["read_model"] = rm_mock
        
        b = AppBootstrap(state, AsyncMock())
        asyncio.get_event_loop().run_until_complete(b.shutdown())
        
        rm_mock.save_snapshot.assert_called_once()


class TestInitReport:
    @patch("settings.get_settings")
    @patch("bootstrap.create_capture_from_env")
    @patch("bootstrap.create_ocr_manager_from_env")
    @patch("bootstrap.create_analyzer_from_env")
    def test_report_accumulates(self, mock_a, mock_o, mock_c, mock_get_settings):
        mock_get_settings.return_value = _mock_settings()
        for m in (mock_a, mock_o, mock_c):
            m.return_value = MagicMock()

        state = _make_state()
        b = AppBootstrap(state, AsyncMock())
        assert len(b.init_report) == 0

        b.init_core()
        assert len(b.init_report) == 3

        with patch("bootstrap.create_profile_manager", return_value=MagicMock()):
            b.init_window()

        assert len(b.init_report) > 3
