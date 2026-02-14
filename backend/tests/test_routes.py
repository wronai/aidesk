"""Unit tests for route modules using FastAPI TestClient with mocked state."""
import asyncio
import json
import os
import sys
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===== Helpers =====

def _make_app_state():
    """Minimal app_state for route testing."""
    return {
        "latest_analysis": "Test analysis",
        "latest_transcript": "Test transcript",
        "context": MagicMock(history=["a", "b"], get_stats=MagicMock(return_value={"items": 2})),
        "subscribers": [],
        "capture": MagicMock(get_stats=MagicMock(return_value={"total": 10})),
        "analyzer": MagicMock(
            analysis_mode="hybrid",
            get_stats=MagicMock(return_value={"calls": 5}),
            set_mode=MagicMock(return_value=True),
        ),
        "ocr_manager": MagicMock(
            get_available_engines=MagicMock(return_value=["paddleocr", "tesseract"]),
            active_engine_name="paddleocr",
            get_stats=MagicMock(return_value={"ocr_calls": 100}),
            set_engine=MagicMock(return_value=True),
            engines={"paddleocr": True, "tesseract": True},
        ),
        "stt": None,
        "diagnostics": None,
        "window_manager": MagicMock(
            get_active_window=MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={"title": "test"}))),
            get_monitors=MagicMock(return_value=[]),
            get_stats=MagicMock(return_value={"calls": 3}),
        ),
        "profile_manager": MagicMock(
            get_all_profiles=MagicMock(return_value=[]),
            active_category=MagicMock(value="development"),
            get_stats=MagicMock(return_value={}),
        ),
        "shell_agent": MagicMock(
            get_pending_actions=MagicMock(return_value=[]),
            get_stats=MagicMock(return_value={"total": 0}),
            get_history=MagicMock(return_value=[]),
            approve_action=MagicMock(return_value=True),
            execute_action=MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={"ok": True}))),
            execute_safe=MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={"output": "hi"}))),
        ),
        "process_scanner": MagicMock(
            get_window_layout=MagicMock(return_value={"windows": []}),
            scan_all_windows=MagicMock(return_value=[]),
            last_windows=[],
            get_stats=MagicMock(return_value={}),
        ),
        "window_cropper": MagicMock(get_stats=MagicMock(return_value={})),
        "event_bus": MagicMock(
            store=MagicMock(query=MagicMock(return_value=[])),
            get_stats=MagicMock(return_value={"published": 50}),
        ),
        "pipeline": MagicMock(
            get_step_names=MagicMock(return_value=["scan", "analyze"]),
            get_stats=MagicMock(return_value={"runs": 10}),
        ),
        "profile_selector": MagicMock(get_stats=MagicMock(return_value={"fast": 5})),
        "read_model": MagicMock(),
        "command_handlers": None,
        "query_handlers": MagicMock(
            read_model=MagicMock(
                get_pipeline_view=MagicMock(return_value={}),
                get_analysis_view=MagicMock(return_value={}),
                get_event_counts=MagicMock(return_value={}),
            ),
            query_pipeline=MagicMock(return_value={}),
            query_stats=MagicMock(return_value={}),
        ),
        "multi_monitor": MagicMock(
            build_snapshot=MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={"monitors": []}))),
        ),
        "semantic_memory": MagicMock(
            recall_relevant=MagicMock(return_value=[]),
            recall_recent=MagicMock(return_value=[]),
            get_stats=MagicMock(return_value={"total": 100}),
            compress_old_context=MagicMock(return_value=5),
            total_memories=95,
        ),
        "action_library": MagicMock(
            _templates={"t1": MagicMock(to_dict=MagicMock(return_value={"id": "t1"}))},
            get_stats=MagicMock(return_value={"templates": 1}),
            import_templates=MagicMock(return_value=2),
            export_templates=MagicMock(return_value='[{"id":"t1"}]'),
            enabled=True,
        ),
        "ocr_enhancer": MagicMock(
            get_stats=MagicMock(return_value={"corrections": 42}),
            enabled=True,
        ),
        "predictive_engine": MagicMock(
            get_stats=MagicMock(return_value={"transitions": 10}),
            get_transition_matrix=MagicMock(return_value={}),
            get_top_patterns=MagicMock(return_value=[]),
            enabled=True,
        ),
        "latest_window": {"title": "cached_window"},
        "latest_organized_screen": {"total_windows": 3, "summary": "test"},
        "stats": {
            "start_time": time.time() - 60,
            "total_screen_analyses": 10,
            "total_transcripts": 5,
            "total_errors": 1,
        },
    }


def _build_client(*route_modules):
    """Build a TestClient with the given route modules registered."""
    app = FastAPI()
    state = _make_app_state()
    broadcast = AsyncMock()

    for mod in route_modules:
        mod.init(state, broadcast)
        app.include_router(mod.router)

    return TestClient(app), state, broadcast


# ===== Core Routes =====

class TestCoreRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import core
        self.client, self.state, self.broadcast = _build_client(core)

    def test_status(self):
        r = self.client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert data["latest_analysis"] == "Test analysis"
        assert data["latest_transcript"] == "Test transcript"
        assert data["context_items"] == 2

    def test_stats(self):
        r = self.client.get("/stats")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_seconds" in data
        assert data["total_screen_analyses"] == 10

    def test_health_healthy(self):
        r = self.client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_health_unhealthy(self):
        self.state["capture"] = None
        r = self.client.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "unhealthy"

    def test_diagnostics_not_ready(self):
        r = self.client.get("/diagnostics")
        assert r.status_code == 503

    def test_diagnostics_history_empty(self):
        r = self.client.get("/diagnostics/history")
        assert r.status_code == 200

    def test_profiles(self):
        r = self.client.get("/profiles")
        assert r.status_code == 200
        assert "profiles" in r.json()

    def test_profiles_not_initialized(self):
        self.state["profile_manager"] = None
        r = self.client.get("/profiles")
        assert r.status_code == 503


# ===== Agent Routes =====

class TestAgentRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import agent
        self.client, self.state, self.broadcast = _build_client(agent)

    def test_get_actions(self):
        r = self.client.get("/agent/actions")
        assert r.status_code == 200
        assert "pending" in r.json()
        assert "stats" in r.json()

    def test_get_actions_no_agent(self):
        self.state["shell_agent"] = None
        r = self.client.get("/agent/actions")
        assert r.status_code == 503

    def test_approve_action(self):
        r = self.client.post("/agent/approve/abc123")
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_approve_action_not_found(self):
        self.state["shell_agent"].approve_action.return_value = False
        r = self.client.post("/agent/approve/missing")
        assert r.status_code == 404

    def test_execute_action(self):
        r = self.client.post("/agent/execute/abc123")
        assert r.status_code == 200
        self.broadcast.assert_called()

    def test_execute_action_no_agent(self):
        self.state["shell_agent"] = None
        r = self.client.post("/agent/execute/abc123")
        assert r.status_code == 503

    def test_history(self):
        r = self.client.get("/agent/history")
        assert r.status_code == 200
        assert "history" in r.json()

    def test_run_safe(self):
        r = self.client.post("/agent/run", json={"command": "uptime"})
        assert r.status_code == 200
        assert "output" in r.json()

    def test_run_safe_missing_command(self):
        r = self.client.post("/agent/run", json={"command": ""})
        assert r.status_code == 400

    def test_run_safe_no_agent(self):
        self.state["shell_agent"] = None
        r = self.client.post("/agent/run", json={"command": "uptime"})
        assert r.status_code == 503


# ===== OCR Routes =====

class TestOCRRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import ocr
        self.client, self.state, self.broadcast = _build_client(ocr)

    def test_ocr_engines(self):
        r = self.client.get("/ocr/engines")
        assert r.status_code == 200
        assert r.json()["active"] == "paddleocr"
        assert "paddleocr" in r.json()["engines"]

    def test_ocr_engines_not_initialized(self):
        self.state["ocr_manager"] = None
        r = self.client.get("/ocr/engines")
        assert r.status_code == 404

    def test_set_ocr_engine_success(self):
        r = self.client.post("/ocr/engine/tesseract")
        assert r.status_code == 200
        assert r.json()["engine"] == "tesseract"

    def test_set_ocr_engine_invalid(self):
        self.state["ocr_manager"].set_engine.return_value = False
        r = self.client.post("/ocr/engine/invalid")
        assert r.status_code == 400

    def test_ocr_stats(self):
        r = self.client.get("/ocr/stats")
        assert r.status_code == 200
        assert r.json()["ocr_calls"] == 100

    def test_ocr_post_process_stats(self):
        r = self.client.get("/ocr/post-process/stats")
        assert r.status_code == 200
        assert r.json()["corrections"] == 42

    def test_ocr_post_process_stats_not_initialized(self):
        self.state["ocr_enhancer"] = None
        r = self.client.get("/ocr/post-process/stats")
        assert r.status_code == 503

    def test_get_analysis_mode(self):
        r = self.client.get("/mode")
        assert r.status_code == 200
        assert r.json()["mode"] == "hybrid"

    def test_set_analysis_mode_success(self):
        r = self.client.post("/mode/ocr_only")
        assert r.status_code == 200
        assert r.json()["mode"] == "ocr_only"

    def test_set_analysis_mode_invalid(self):
        self.state["analyzer"].set_mode.return_value = False
        r = self.client.post("/mode/invalid")
        assert r.status_code == 400


# ===== Windows Routes =====

class TestWindowsRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import windows
        self.client, self.state, self.broadcast = _build_client(windows)

    def test_get_active_window(self):
        r = self.client.get("/window")
        assert r.status_code == 200
        assert r.json()["title"] == "test"

    def test_get_active_window_not_enabled(self):
        self.state["window_manager"] = None
        r = self.client.get("/window")
        assert r.status_code == 503

    def test_get_latest_window(self):
        r = self.client.get("/window/latest")
        assert r.status_code == 200
        assert r.json()["title"] == "cached_window"

    def test_get_latest_window_no_data(self):
        self.state["latest_window"] = None
        r = self.client.get("/window/latest")
        assert r.status_code == 404

    def test_monitors(self):
        r = self.client.get("/monitors")
        assert r.status_code == 200
        assert "monitors" in r.json()

    def test_window_stats(self):
        r = self.client.get("/window/stats")
        assert r.status_code == 200

    def test_window_stats_not_enabled(self):
        self.state["window_manager"] = None
        r = self.client.get("/window/stats")
        assert r.status_code == 503

    def test_processes(self):
        r = self.client.get("/processes")
        assert r.status_code == 200

    def test_processes_not_initialized(self):
        self.state["process_scanner"] = None
        r = self.client.get("/processes")
        assert r.status_code == 503

    def test_windows_all(self):
        r = self.client.get("/windows/all")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_multi_monitor(self):
        r = self.client.get("/multi-monitor")
        assert r.status_code == 200
        assert "monitors" in r.json()

    def test_multi_monitor_not_initialized(self):
        self.state["multi_monitor"] = None
        r = self.client.get("/multi-monitor")
        assert r.status_code == 503


# ===== Memory Routes =====

class TestMemoryRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import memory
        self.client, self.state, self.broadcast = _build_client(memory)

    def test_search_with_query(self):
        r = self.client.get("/memory/search?q=test&k=3")
        assert r.status_code == 200
        data = r.json()
        assert data["query"] == "test"
        self.state["semantic_memory"].recall_relevant.assert_called()

    def test_search_without_query(self):
        r = self.client.get("/memory/search")
        assert r.status_code == 200
        self.state["semantic_memory"].recall_recent.assert_called()

    def test_search_not_initialized(self):
        self.state["semantic_memory"] = None
        r = self.client.get("/memory/search?q=test")
        assert r.status_code == 503

    def test_memory_stats(self):
        r = self.client.get("/memory/stats")
        assert r.status_code == 200
        assert r.json()["total"] == 100

    def test_memory_stats_not_initialized(self):
        self.state["semantic_memory"] = None
        r = self.client.get("/memory/stats")
        assert r.status_code == 503

    def test_compress(self):
        r = self.client.post("/memory/compress")
        assert r.status_code == 200
        data = r.json()
        assert data["compressed"] == 5
        assert data["remaining"] == 95

    def test_compress_not_initialized(self):
        self.state["semantic_memory"] = None
        r = self.client.post("/memory/compress")
        assert r.status_code == 503


# ===== Templates Routes =====

class TestTemplatesRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import templates
        self.client, self.state, self.broadcast = _build_client(templates)

    def test_get_templates(self):
        r = self.client.get("/templates")
        assert r.status_code == 200
        assert "templates" in r.json()
        assert "stats" in r.json()

    def test_get_templates_not_initialized(self):
        self.state["action_library"] = None
        r = self.client.get("/templates")
        assert r.status_code == 503

    def test_import_templates(self):
        r = self.client.post("/templates/import", content='[{"id":"new"}]')
        assert r.status_code == 200
        assert r.json()["imported"] == 2

    def test_import_templates_not_initialized(self):
        self.state["action_library"] = None
        r = self.client.post("/templates/import", content='[]')
        assert r.status_code == 503

    def test_export_templates(self):
        r = self.client.get("/templates/export")
        assert r.status_code == 200

    def test_export_not_initialized(self):
        self.state["action_library"] = None
        r = self.client.get("/templates/export")
        assert r.status_code == 503


# ===== Events Routes =====

class TestEventsRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import events
        self.client, self.state, self.broadcast = _build_client(events)

    def test_query_events(self):
        r = self.client.get("/events")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_query_events_not_enabled(self):
        self.state["event_bus"] = None
        r = self.client.get("/events")
        assert r.status_code == 503

    def test_event_bus_stats(self):
        r = self.client.get("/events/stats")
        assert r.status_code == 200
        assert r.json()["published"] == 50

    def test_event_bus_stats_not_initialized(self):
        self.state["event_bus"] = None
        r = self.client.get("/events/stats")
        assert r.status_code == 503

    def test_pipeline_info(self):
        r = self.client.get("/pipeline")
        assert r.status_code == 200
        data = r.json()
        assert data["steps"] == ["scan", "analyze"]
        assert "profile_selector" in data

    def test_pipeline_not_initialized(self):
        self.state["pipeline"] = None
        r = self.client.get("/pipeline")
        assert r.status_code == 503

    def test_read_model(self):
        r = self.client.get("/read-model")
        assert r.status_code == 200
        assert "pipeline" in r.json()

    def test_read_model_not_initialized(self):
        self.state["query_handlers"] = None
        r = self.client.get("/read-model")
        assert r.status_code == 503

    def test_read_model_pipeline(self):
        r = self.client.get("/read-model/pipeline")
        assert r.status_code == 200

    def test_read_model_stats(self):
        r = self.client.get("/read-model/stats")
        assert r.status_code == 200

    def test_traces(self):
        r = self.client.get("/traces")
        assert r.status_code == 200
        assert "stats" in r.json()
        assert "recent_spans" in r.json()

    def test_predictive(self):
        r = self.client.get("/predictive")
        assert r.status_code == 200
        assert "stats" in r.json()

    def test_predictive_not_initialized(self):
        self.state["predictive_engine"] = None
        r = self.client.get("/predictive")
        assert r.status_code == 503


# ===== Screen Routes =====

class TestScreenRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import screen
        self.client, self.state, self.broadcast = _build_client(screen)

    def test_organized_screen(self):
        r = self.client.get("/screen/organized")
        assert r.status_code == 200
        assert r.json()["total_windows"] == 3

    def test_organized_screen_no_data(self):
        self.state["latest_organized_screen"] = None
        r = self.client.get("/screen/organized")
        assert r.status_code == 404

    def test_screen_stats(self):
        r = self.client.get("/screen/stats")
        assert r.status_code == 200

    def test_screenshots_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAPTURES_DIR", str(tmp_path))
        r = self.client.get("/screenshots")
        assert r.status_code == 200
        assert r.json() == []

    def test_screenshots_with_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAPTURES_DIR", str(tmp_path))
        (tmp_path / "test.jpg").write_bytes(b"\xff\xd8test")
        r = self.client.get("/screenshots")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["name"] == "test.jpg"

    def test_screenshot_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAPTURES_DIR", str(tmp_path))
        r = self.client.get("/screenshots/missing.jpg")
        assert r.status_code == 404

    def test_screenshot_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAPTURES_DIR", str(tmp_path))
        r = self.client.get("/screenshots/../../etc/passwd")
        assert r.status_code in (403, 404)


# ===== Config Routes =====

class TestConfigRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from routes import config
        self.client, self.state, self.broadcast = _build_client(config)

    @patch("routes.config.get_config_with_schema")
    def test_get_config(self, mock_config):
        mock_config.return_value = {"values": {}, "schema": {}}
        r = self.client.get("/config")
        assert r.status_code == 200

    @patch("routes.config.update_env")
    def test_post_config(self, mock_update):
        mock_update.return_value = {"KEY": "value"}
        r = self.client.post("/config", json={"KEY": "value"})
        assert r.status_code == 200
        assert "updated_keys" in r.json()
        self.broadcast.assert_called()

    def test_post_config_empty(self):
        r = self.client.post("/config", json={})
        assert r.status_code == 400

    def test_post_config_invalid_types(self):
        r = self.client.post("/config", json={"key": 123})
        assert r.status_code == 400

    @patch("routes.config.discover_audio_devices")
    def test_audio_devices(self, mock_devices):
        mock_devices.return_value = {"devices": []}
        r = self.client.get("/audio/devices")
        assert r.status_code == 200


# ===== Init function tests =====

class TestRouteInit:
    def test_all_modules_have_init(self):
        from routes import core, ocr, windows, agent, events, screen, memory, templates, config
        for mod in (core, ocr, windows, agent, events, screen, memory, templates, config):
            assert hasattr(mod, "init"), f"{mod.__name__} missing init()"
            assert hasattr(mod, "router"), f"{mod.__name__} missing router"

    def test_init_sets_state(self):
        from routes import memory
        state = {"test": True}
        broadcast = AsyncMock()
        memory.init(state, broadcast)
        assert memory._state is state
        assert memory._broadcast is broadcast
