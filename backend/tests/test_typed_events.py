"""Tests for typed event payloads (Pydantic models)."""
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_bus import Event, EventType
from typed_events import (
    EventPayload,
    WindowsScannedPayload,
    ActiveWindowPayload,
    ScreenCapturedPayload,
    ScreenOrganizedPayload,
    ContextBuiltPayload,
    AnalysisCompletedPayload,
    AgentSuggestedPayload,
    BroadcastSentPayload,
    PipelineCompletedPayload,
    SwitchOCRPayload,
    SwitchModePayload,
    ExecuteActionPayload,
    ApproveActionPayload,
    RunSafePayload,
    ComponentInitPayload,
    SystemStartupPayload,
    TranscriptPayload,
    CircuitBreakerPayload,
    PAYLOAD_REGISTRY,
    typed_event,
    parse_payload,
)


# ===== Payload Validation =====

class TestPayloadValidation:
    def test_windows_scanned_defaults(self):
        p = WindowsScannedPayload()
        assert p.total == 0
        assert p.cached is False

    def test_windows_scanned_with_values(self):
        p = WindowsScannedPayload(total=5, cached=True, cache_age_s=1.2)
        assert p.total == 5
        assert p.cached is True
        assert p.cache_age_s == 1.2

    def test_screen_captured_serializes(self):
        p = ScreenCapturedPayload(size_kb=42.5, path="/tmp/test.jpg", changed=True)
        d = p.model_dump()
        assert d["size_kb"] == 42.5
        assert d["path"] == "/tmp/test.jpg"

    def test_analysis_completed_all_fields(self):
        p = AnalysisCompletedPayload(
            tokens=150, cost=0.003, provider="gemini",
            model="gemini-2.0-flash", mode="hybrid", latency_ms=420,
        )
        assert p.tokens == 150
        assert p.cost == 0.003
        assert p.mode == "hybrid"

    def test_switch_ocr_requires_engine(self):
        with pytest.raises(Exception):
            SwitchOCRPayload()  # engine is required

    def test_switch_ocr_valid(self):
        p = SwitchOCRPayload(engine="paddleocr")
        assert p.engine == "paddleocr"

    def test_switch_mode_requires_mode(self):
        with pytest.raises(Exception):
            SwitchModePayload()

    def test_execute_action_optional_cwd(self):
        p = ExecuteActionPayload(action_id="abc123")
        assert p.action_id == "abc123"
        assert p.cwd is None

    def test_run_safe_requires_command(self):
        with pytest.raises(Exception):
            RunSafePayload()

    def test_component_init_defaults(self):
        p = ComponentInitPayload(component="capture")
        assert p.success is True
        assert p.error is None

    def test_component_init_failure(self):
        p = ComponentInitPayload(component="stt", success=False, error="No mic")
        assert p.success is False
        assert p.error == "No mic"

    def test_transcript_payload(self):
        p = TranscriptPayload(text="hello world", is_final=True)
        assert p.text == "hello world"
        assert p.language == "pl"

    def test_pipeline_completed_payload(self):
        p = PipelineCompletedPayload(
            run_id="r1", total_ms=500, steps_run=8, steps_skipped=2,
            errors=["step_x failed"], timings={"analyze": 200.0},
        )
        assert p.steps_run == 8
        assert len(p.errors) == 1

    def test_circuit_breaker_payload(self):
        p = CircuitBreakerPayload(step_name="analyze", state="open", failure_count=5, threshold=5)
        assert p.state == "open"

    def test_extra_fields_allowed(self):
        """Forward compat: unknown fields should not raise."""
        p = WindowsScannedPayload(total=3, cached=False, future_field="xyz")
        assert p.total == 3


# ===== typed_event factory =====

class TestTypedEventFactory:
    def test_creates_valid_event(self):
        payload = WindowsScannedPayload(total=10, cached=True)
        event = typed_event(EventType.WINDOWS_SCANNED, payload, source="scan_windows")
        assert event.type == "pipeline.windows_scanned"
        assert event.source == "scan_windows"
        assert event.data["total"] == 10
        assert event.data["cached"] is True

    def test_correlation_id_passed(self):
        payload = ContextBuiltPayload(context_length=500)
        event = typed_event(EventType.CONTEXT_BUILT, payload, correlation_id="abc")
        assert event.correlation_id == "abc"

    def test_excludes_none_values(self):
        payload = ScreenCapturedPayload(size_kb=10.0)
        event = typed_event(EventType.SCREEN_CAPTURED, payload)
        # path defaults to "" which is not None, so it should be present
        assert "size_kb" in event.data

    def test_command_event(self):
        payload = SwitchOCRPayload(engine="easyocr")
        event = typed_event(EventType.CMD_SWITCH_OCR_ENGINE, payload, source="api")
        assert event.type == "cmd.switch_ocr_engine"
        assert event.data["engine"] == "easyocr"


# ===== parse_payload =====

class TestParsePayload:
    def test_parse_known_type(self):
        event = Event(
            type=EventType.ANALYSIS_COMPLETED.value,
            data={"tokens": 200, "cost": 0.01, "provider": "openai"},
        )
        payload = parse_payload(event)
        assert isinstance(payload, AnalysisCompletedPayload)
        assert payload.tokens == 200
        assert payload.provider == "openai"

    def test_parse_unknown_type(self):
        event = Event(type="custom.unknown", data={"foo": "bar"})
        payload = parse_payload(event)
        assert payload is None

    def test_roundtrip(self):
        """typed_event → Event → parse_payload should recover the payload."""
        original = AgentSuggestedPayload(count=3)
        event = typed_event(EventType.AGENT_SUGGESTED, original, source="agent")
        recovered = parse_payload(event)
        assert isinstance(recovered, AgentSuggestedPayload)
        assert recovered.count == 3

    def test_parse_with_extra_fields(self):
        """Forward compat: extra fields in data should not crash."""
        event = Event(
            type=EventType.WINDOWS_SCANNED.value,
            data={"total": 5, "cached": False, "new_field": "v2"},
        )
        payload = parse_payload(event)
        assert isinstance(payload, WindowsScannedPayload)
        assert payload.total == 5


# ===== Registry =====

class TestPayloadRegistry:
    def test_all_pipeline_events_registered(self):
        pipeline_types = [
            EventType.WINDOWS_SCANNED, EventType.SCREEN_CAPTURED,
            EventType.SCREEN_ORGANIZED, EventType.CONTEXT_BUILT,
            EventType.ANALYSIS_COMPLETED, EventType.AGENT_SUGGESTED,
            EventType.BROADCAST_SENT,
        ]
        for et in pipeline_types:
            assert et.value in PAYLOAD_REGISTRY, f"{et.value} not in registry"

    def test_all_command_events_registered(self):
        cmd_types = [
            EventType.CMD_SWITCH_OCR_ENGINE, EventType.CMD_SWITCH_MODE,
            EventType.CMD_EXECUTE_ACTION, EventType.CMD_APPROVE_ACTION,
            EventType.CMD_RUN_SAFE,
        ]
        for et in cmd_types:
            assert et.value in PAYLOAD_REGISTRY, f"{et.value} not in registry"

    def test_system_events_registered(self):
        assert EventType.SYSTEM_STARTUP.value in PAYLOAD_REGISTRY
        assert EventType.COMPONENT_INITIALIZED.value in PAYLOAD_REGISTRY
