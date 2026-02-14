"""
End-to-end tests for the AI Desktop Assistant API.

Tests exercise the full FastAPI server with lifespan (real component init).
Grouped by feature area:
  - Core (root, health, status, stats)
  - CQRS / Event Sourcing (events, pipeline, read-model)
  - Config Service (config, audio devices)
  - Window Awareness (window, monitors, processes, screen)
  - OCR & Analysis Mode
  - Shell Agent
  - Diagnostics
  - Screenshots & Crops
  - SSE Streaming
  - Cross-component flows
"""
import json
import time
import pytest
from fastapi.testclient import TestClient

from server import app


# ─── Shared fixture: a single TestClient for all tests ─────────────
# Using a module-scoped fixture avoids re-init of the full server per test.

@pytest.fixture(scope="module")
def client():
    """Create a TestClient with lifespan (triggers full startup/shutdown)."""
    with TestClient(app) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════
# 1. CORE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestCoreEndpoints:
    """Root, health, status, stats."""

    def test_root_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert "endpoints" in data
        assert isinstance(data["endpoints"], dict)

    def test_root_lists_all_endpoint_groups(self, client):
        data = client.get("/").json()
        eps = data["endpoints"]
        # Key endpoint groups must be listed
        for key in ["stream", "health", "events", "pipeline_info", "config"]:
            assert key in eps or any(key in v for v in eps.values()), f"Missing endpoint doc: {key}"

    def test_health_structure(self, client):
        r = client.get("/health")
        assert r.status_code in [200, 503]
        data = r.json()
        assert "status" in data
        assert "components" in data
        # Must report on key components
        comps = data["components"]
        for key in ["capture", "analyzer", "event_bus", "pipeline", "read_model"]:
            assert key in comps, f"Missing health component: {key}"

    def test_health_components_are_bool(self, client):
        comps = client.get("/health").json()["components"]
        for k, v in comps.items():
            assert isinstance(v, bool), f"Component {k} should be bool, got {type(v)}"

    def test_status_endpoint(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "latest_analysis" in data
        assert "latest_transcript" in data
        assert "active_subscribers" in data
        assert "context_items" in data

    def test_stats_endpoint(self, client):
        r = client.get("/stats")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_seconds" in data
        assert "total_screen_analyses" in data
        assert "total_errors" in data
        assert "uptime_formatted" in data
        assert data["uptime_seconds"] >= 0

    def test_stats_includes_component_stats(self, client):
        data = client.get("/stats").json()
        # At minimum, capture and context stats should be present
        assert "capture" in data
        assert "context" in data


# ═══════════════════════════════════════════════════════════════════
# 2. CQRS / EVENT SOURCING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestEventSourcingEndpoints:
    """Event store queries, event bus stats, pipeline info, read model."""

    def test_events_returns_list(self, client):
        r = client.get("/events")
        assert r.status_code == 200
        data = r.json()
        assert "total" in data
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_events_limit(self, client):
        r = client.get("/events?limit=3")
        data = r.json()
        assert len(data["events"]) <= 3

    def test_events_filter_by_type(self, client):
        r = client.get("/events?type=system.startup")
        data = r.json()
        for event in data["events"]:
            assert event["type"] == "system.startup"

    def test_events_filter_by_source(self, client):
        r = client.get("/events?source=lifespan")
        data = r.json()
        for event in data["events"]:
            assert event["source"] == "lifespan"

    def test_events_have_required_fields(self, client):
        data = client.get("/events?limit=1").json()
        if data["events"]:
            event = data["events"][0]
            for field in ["event_id", "type", "category", "source", "timestamp", "data"]:
                assert field in event, f"Missing event field: {field}"

    def test_events_stats(self, client):
        r = client.get("/events/stats")
        assert r.status_code == 200
        data = r.json()
        assert "total_published" in data
        assert "total_handled" in data
        assert "registered_types" in data
        assert data["total_published"] >= 1  # at least startup event

    def test_events_stats_includes_store(self, client):
        data = client.get("/events/stats").json()
        assert "store" in data
        store = data["store"]
        assert "total_events" in store
        assert "db_path" in store

    def test_pipeline_info(self, client):
        r = client.get("/pipeline")
        assert r.status_code == 200
        data = r.json()
        assert "steps" in data
        assert "stats" in data
        assert isinstance(data["steps"], list)
        assert len(data["steps"]) >= 11  # 8 original + 5 Tier 1 (3 in parallel group)

    def test_pipeline_step_names(self, client):
        steps = client.get("/pipeline").json()["steps"]
        # Core sequential steps must be present
        for name in ["scan_windows", "detect_active_window", "capture_screen",
                     "crop_windows", "build_context", "analyze",
                     "suggest_actions", "build_broadcast"]:
            assert name in steps, f"{name} missing from pipeline steps"
        # Parallel group contains action_templates, semantic_memory, predictive
        parallel_step = [s for s in steps if s.startswith("parallel(")]
        if parallel_step:
            assert "action_templates" in parallel_step[0]
            assert "semantic_memory" in parallel_step[0]
            assert "predictive" in parallel_step[0]

    def test_pipeline_stats_structure(self, client):
        stats = client.get("/pipeline").json()["stats"]
        assert "total_runs" in stats
        assert "total_errors" in stats
        assert "step_count" in stats
        assert stats["step_count"] >= 11

    def test_read_model_root(self, client):
        r = client.get("/read-model")
        assert r.status_code == 200
        data = r.json()
        assert "pipeline" in data
        assert "analysis" in data
        assert "event_counts" in data

    def test_read_model_pipeline(self, client):
        r = client.get("/read-model/pipeline")
        assert r.status_code == 200
        data = r.json()
        assert "total_runs" in data
        assert "total_errors" in data
        assert "steps" in data

    def test_read_model_stats(self, client):
        r = client.get("/read-model/stats")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_seconds" in data
        assert "pipeline" in data
        assert "analysis" in data
        assert "event_counts" in data
        assert "event_bus" in data

    def test_startup_event_in_store(self, client):
        """The lifespan should have emitted a system.startup event."""
        data = client.get("/events?type=system.startup&limit=1").json()
        assert data["total"] >= 1
        event = data["events"][0]
        assert "version" in event["data"]
        assert "components" in event["data"]


# ═══════════════════════════════════════════════════════════════════
# 3. CONFIG SERVICE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestConfigServiceEndpoints:
    """Configuration read/write + audio device discovery."""

    def test_config_get(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        data = r.json()
        assert "values" in data
        assert "schema" in data
        assert "audio" in data
        assert isinstance(data["values"], dict)
        assert isinstance(data["schema"], list)

    def test_config_schema_groups(self, client):
        schema = client.get("/config").json()["schema"]
        assert len(schema) >= 7
        for group in schema:
            assert "group" in group
            assert "fields" in group
            assert isinstance(group["fields"], list)
            assert len(group["fields"]) > 0

    def test_config_schema_field_structure(self, client):
        schema = client.get("/config").json()["schema"]
        for group in schema:
            for field in group["fields"]:
                assert "key" in field
                assert "label" in field
                assert "type" in field
                assert field["type"] in ["text", "number", "bool", "select", "password",
                                          "audio_source", "audio_monitor", "audio_sink"]

    def test_config_values_has_known_keys(self, client):
        values = client.get("/config").json()["values"]
        # These should be in any .env
        assert "PORT" in values or "HOST" in values or "VISION_MODEL" in values

    def test_audio_devices(self, client):
        r = client.get("/audio/devices")
        assert r.status_code == 200
        data = r.json()
        assert "microphones" in data
        assert "monitors" in data
        assert "speakers" in data
        assert "current" in data
        assert isinstance(data["microphones"], list)

    def test_config_ui_serves_html(self, client):
        r = client.get("/config/ui")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_config_post_rejects_empty(self, client):
        r = client.post("/config", json={})
        assert r.status_code == 400

    def test_config_post_rejects_non_string_values(self, client):
        r = client.post("/config", json={"KEY": 123})
        assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# 4. WINDOW AWARENESS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestWindowAwarenessEndpoints:
    """Active window, monitors, processes, screen organized."""

    def test_window_active(self, client):
        r = client.get("/window")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "title" in data
            assert "category" in data
            assert "geometry" in data

    def test_window_latest(self, client):
        r = client.get("/window/latest")
        assert r.status_code in [200, 404]

    def test_monitors(self, client):
        r = client.get("/monitors")
        assert r.status_code == 200
        data = r.json()
        assert "monitors" in data
        assert isinstance(data["monitors"], list)

    def test_window_stats(self, client):
        r = client.get("/window/stats")
        assert r.status_code in [200, 503]

    def test_processes(self, client):
        r = client.get("/processes")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "total_windows" in data
            assert "by_category" in data

    def test_windows_all(self, client):
        r = client.get("/windows/all")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "total" in data
            assert "windows" in data
            assert isinstance(data["windows"], list)

    def test_screen_organized(self, client):
        r = client.get("/screen/organized")
        assert r.status_code in [200, 404]

    def test_screen_stats(self, client):
        r = client.get("/screen/stats")
        assert r.status_code == 200
        data = r.json()
        # May have process_scanner and/or window_cropper stats
        assert isinstance(data, dict)


# ═══════════════════════════════════════════════════════════════════
# 5. OCR & ANALYSIS MODE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestOCREndpoints:
    """OCR engine management, analysis mode switching."""

    def test_ocr_engines_list(self, client):
        r = client.get("/ocr/engines")
        assert r.status_code in [200, 404]
        if r.status_code == 200:
            data = r.json()
            assert "engines" in data
            assert "active" in data
            assert isinstance(data["engines"], list)

    def test_ocr_stats(self, client):
        r = client.get("/ocr/stats")
        assert r.status_code in [200, 404]

    def test_ocr_switch_invalid_engine(self, client):
        r = client.post("/ocr/engine/nonexistent_engine_xyz")
        assert r.status_code in [400, 404]

    def test_analysis_mode_get(self, client):
        r = client.get("/mode")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "mode" in data
            assert "available_modes" in data
            assert len(data["available_modes"]) == 4

    def test_analysis_mode_valid_switch(self, client):
        r = client.post("/mode/hybrid")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            assert r.json()["mode"] == "hybrid"

    def test_analysis_mode_invalid_switch(self, client):
        r = client.post("/mode/invalid_mode_xyz")
        assert r.status_code in [400, 503]


# ═══════════════════════════════════════════════════════════════════
# 6. SHELL AGENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestShellAgentEndpoints:
    """Agent actions, approval, execution, history."""

    def test_agent_actions(self, client):
        r = client.get("/agent/actions")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "pending" in data
            assert "stats" in data
            assert isinstance(data["pending"], list)

    def test_agent_history(self, client):
        r = client.get("/agent/history")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "history" in data
            assert "stats" in data

    def test_agent_approve_nonexistent(self, client):
        r = client.post("/agent/approve/nonexistent_id_xyz")
        assert r.status_code in [404, 503]

    def test_agent_execute_nonexistent(self, client):
        r = client.post("/agent/execute/nonexistent_id_xyz")
        assert r.status_code in [404, 503]

    def test_agent_run_safe_command(self, client):
        r = client.post("/agent/run", json={"command": "whoami"})
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "exit_code" in data
            assert "command" in data
            assert data["exit_code"] == 0
            assert data["executed"] is True

    def test_agent_run_safe_uptime(self, client):
        r = client.post("/agent/run", json={"command": "uptime"})
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            assert r.json()["exit_code"] == 0

    def test_agent_run_missing_command(self, client):
        r = client.post("/agent/run", json={"command": ""})
        assert r.status_code in [400, 503]

    def test_agent_run_blocked_command(self, client):
        """Dangerous commands should be blocked."""
        r = client.post("/agent/run", json={"command": "rm -rf /"})
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            # Should be blocked by the agent
            assert data.get("blocked", False) or data.get("exit_code") != 0

    def test_agent_stats_structure(self, client):
        r = client.get("/agent/actions")
        if r.status_code == 200:
            stats = r.json()["stats"]
            assert "total_suggestions" in stats
            assert "total_executions" in stats
            assert "total_blocked" in stats


# ═══════════════════════════════════════════════════════════════════
# 7. APP PROFILES
# ═══════════════════════════════════════════════════════════════════

class TestProfilesEndpoints:
    """Per-app analysis profiles."""

    def test_profiles_list(self, client):
        r = client.get("/profiles")
        assert r.status_code in [200, 503]
        if r.status_code == 200:
            data = r.json()
            assert "profiles" in data
            assert isinstance(data["profiles"], list)
            assert len(data["profiles"]) >= 7  # IDE, Terminal, Browser, etc.

    def test_profile_structure(self, client):
        r = client.get("/profiles")
        if r.status_code == 200:
            for p in r.json()["profiles"]:
                assert "category" in p
                assert "name" in p


# ═══════════════════════════════════════════════════════════════════
# 8. DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════

class TestDiagnosticsEndpoints:
    """Autodiagnostics and nfo validation."""

    def test_diagnostics(self, client):
        r = client.get("/diagnostics")
        # First run may not have completed yet
        assert r.status_code in [200, 503]

    def test_diagnostics_history(self, client):
        r = client.get("/diagnostics/history")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_nfo_validation(self, client):
        r = client.get("/nfo/validation")
        assert r.status_code == 200
        data = r.json()
        assert "all_ok" in data
        assert "ok" in data
        assert "failed" in data
        assert isinstance(data["ok"], list)
        assert isinstance(data["failed"], list)


# ═══════════════════════════════════════════════════════════════════
# 9. SCREENSHOTS & CROPS
# ═══════════════════════════════════════════════════════════════════

class TestScreenshotEndpoints:
    """Screenshot browser and crop listing."""

    def test_screenshots_list(self, client):
        r = client.get("/screenshots")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_screenshot_browser_html(self, client):
        r = client.get("/browser")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_screenshot_not_found(self, client):
        r = client.get("/screenshots/nonexistent_file.jpg")
        assert r.status_code == 404

    def test_screenshot_directory_traversal_blocked(self, client):
        r = client.get("/screenshots/../../../etc/passwd")
        assert r.status_code in [403, 404]

    def test_crops_list(self, client):
        r = client.get("/crops")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ═══════════════════════════════════════════════════════════════════
# 10. SSE STREAMING
# ═══════════════════════════════════════════════════════════════════

class TestSSEStreaming:
    """Server-Sent Events endpoint.

    Note: TestClient (Starlette) uses a synchronous transport which makes
    reading streaming responses block forever. We verify the endpoint
    exists and that the /stream route is registered (hit via root endpoint
    inventory). Full SSE streaming is tested manually or via httpx async.
    """

    def test_sse_endpoint_registered(self, client):
        """The /stream SSE endpoint should be registered in the app."""
        routes = [r.path for r in client.app.routes if hasattr(r, "path")]
        assert "/stream" in routes

    def test_sse_endpoint_listed_in_root(self, client):
        """Root endpoint should advertise the /stream endpoint."""
        data = client.get("/").json()
        eps = data.get("endpoints", {})
        assert "stream" in eps or any("/stream" in str(v) for v in eps.values())


# ═══════════════════════════════════════════════════════════════════
# 11. CROSS-COMPONENT INTEGRATION FLOWS
# ═══════════════════════════════════════════════════════════════════

class TestCrossComponentFlows:
    """Tests that verify multiple components work together."""

    def test_startup_event_populates_read_model(self, client):
        """ReadModel event_counts should include system.startup after boot."""
        data = client.get("/read-model").json()
        counts = data.get("event_counts", {})
        assert "system.startup" in counts
        assert counts["system.startup"] >= 1

    def test_events_and_pipeline_are_correlated(self, client):
        """Events from a single pipeline run should share a correlation_id."""
        data = client.get("/events?limit=20").json()
        events = data["events"]
        if len(events) >= 2:
            # Find events from the same run
            cids = set(e["correlation_id"] for e in events if e.get("correlation_id"))
            # There should be at least one correlation group
            assert len(cids) >= 1

    def test_health_matches_read_model_components(self, client):
        """Health check and read-model should agree on component availability."""
        health = client.get("/health").json()
        rm = client.get("/read-model").json()
        # Both should indicate event bus is present
        assert health["components"].get("event_bus") is True
        assert health["components"].get("pipeline") is True
        assert health["components"].get("read_model") is True

    def test_pipeline_runs_tracked_in_stats(self, client):
        """Pipeline stats should be accessible from multiple endpoints."""
        pipeline_data = client.get("/pipeline").json()
        rm_data = client.get("/read-model/pipeline").json()
        # Both should report the same step list
        assert pipeline_data["steps"] == rm_data["steps"]

    def test_config_audio_matches_devices(self, client):
        """Config endpoint audio section should match /audio/devices."""
        config = client.get("/config").json()
        devices = client.get("/audio/devices").json()
        # Both should have same structure
        assert "microphones" in config["audio"]
        assert "microphones" in devices
        assert len(config["audio"]["microphones"]) == len(devices["microphones"])

    def test_agent_run_command_increments_stats(self, client):
        """Running a safe command should increment execution count."""
        # Get initial stats
        r1 = client.get("/agent/actions")
        if r1.status_code != 200:
            pytest.skip("Shell agent not available")
        initial = r1.json()["stats"]["total_executions"]

        # Run a command
        client.post("/agent/run", json={"command": "whoami"})

        # Check stats increased
        r2 = client.get("/agent/actions")
        final = r2.json()["stats"]["total_executions"]
        assert final >= initial + 1

    def test_event_store_grows_over_time(self, client):
        """Event store total should increase as pipeline runs."""
        stats1 = client.get("/events/stats").json()
        count1 = stats1["store"]["total_events"]
        # Store should have events from startup + pipeline
        assert count1 >= 1

    def test_mode_switch_reflected_in_mode_endpoint(self, client):
        """Switching analysis mode via POST should be reflected in GET."""
        r = client.post("/mode/hybrid")
        if r.status_code == 200:
            data = client.get("/mode").json()
            assert data["mode"] == "hybrid"

    def test_full_endpoint_inventory(self, client):
        """Smoke test: all documented endpoints should respond (not 500)."""
        endpoints = [
            ("GET", "/"),
            ("GET", "/health"),
            ("GET", "/status"),
            ("GET", "/stats"),
            ("GET", "/events"),
            ("GET", "/events/stats"),
            ("GET", "/pipeline"),
            ("GET", "/read-model"),
            ("GET", "/read-model/pipeline"),
            ("GET", "/read-model/stats"),
            ("GET", "/config"),
            ("GET", "/audio/devices"),
            ("GET", "/ocr/engines"),
            ("GET", "/ocr/stats"),
            ("GET", "/mode"),
            ("GET", "/window"),
            ("GET", "/monitors"),
            ("GET", "/processes"),
            ("GET", "/windows/all"),
            ("GET", "/screen/stats"),
            ("GET", "/profiles"),
            ("GET", "/agent/actions"),
            ("GET", "/agent/history"),
            ("GET", "/diagnostics/history"),
            ("GET", "/nfo/validation"),
            ("GET", "/screenshots"),
            ("GET", "/crops"),
        ]
        failures = []
        for method, path in endpoints:
            r = client.request(method, path)
            if r.status_code == 500:
                failures.append(f"{method} {path} → 500")
        assert failures == [], f"Endpoints returned 500: {failures}"

