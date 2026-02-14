"""
Typed Event Payloads — Pydantic models for type-safe event data.

Replaces raw Dict[str, Any] payloads with validated, documented models.
Each event type gets a corresponding payload class that:
- Validates data at creation time (fail-fast on typos/missing fields)
- Provides IDE autocompletion and type checking
- Documents the event schema in code
- Serializes to dict for backward compat with Event(data=...)

Usage:
    from typed_events import WindowsScannedPayload, typed_event

    # Create typed event (validates payload)
    event = typed_event(
        EventType.WINDOWS_SCANNED,
        WindowsScannedPayload(total=5, cached=False),
        source="scan_windows",
    )
    await bus.publish(event)

    # Read typed payload from event
    payload = WindowsScannedPayload(**event.data)
    print(payload.total)  # int, not Any
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from event_bus import Event, EventType


# ===== Base Payload =====

class EventPayload(BaseModel):
    """Base class for all typed event payloads."""
    model_config = ConfigDict(extra="allow")  # Forward compat: ignore unknown fields


# ===== Pipeline Event Payloads =====

class WindowsScannedPayload(EventPayload):
    """Payload for pipeline.windows_scanned events."""
    total: int = 0
    cached: bool = False
    cache_age_s: Optional[float] = None


class ActiveWindowPayload(EventPayload):
    """Payload for detect_active_window events."""
    active: Dict[str, Any] = Field(default_factory=dict)


class ScreenCapturedPayload(EventPayload):
    """Payload for pipeline.screen_captured events."""
    size_kb: float = 0.0
    path: str = ""
    changed: bool = True
    width: int = 0
    height: int = 0


class ScreenOrganizedPayload(EventPayload):
    """Payload for pipeline.screen_organized events."""
    total_windows: int = 0
    categories: List[str] = Field(default_factory=list)
    active_app: str = ""


class ContextBuiltPayload(EventPayload):
    """Payload for pipeline.context_built events."""
    context_length: int = 0


class AnalysisCompletedPayload(EventPayload):
    """Payload for pipeline.analysis_completed events."""
    tokens: int = 0
    cost: float = 0.0
    provider: str = ""
    model: str = ""
    mode: str = ""
    latency_ms: int = 0


class AgentSuggestedPayload(EventPayload):
    """Payload for pipeline.agent_suggested events."""
    count: int = 0


class BroadcastSentPayload(EventPayload):
    """Payload for pipeline.broadcast_sent events."""
    keys: List[str] = Field(default_factory=list)


class PipelineCompletedPayload(EventPayload):
    """Payload for pipeline.completed events."""
    run_id: str = ""
    total_ms: int = 0
    steps_run: int = 0
    steps_skipped: int = 0
    errors: List[str] = Field(default_factory=list)
    timings: Dict[str, float] = Field(default_factory=dict)


# ===== Command Payloads =====

class SwitchOCRPayload(EventPayload):
    """Payload for cmd.switch_ocr_engine."""
    engine: str


class SwitchModePayload(EventPayload):
    """Payload for cmd.switch_mode."""
    mode: str


class ExecuteActionPayload(EventPayload):
    """Payload for cmd.execute_action."""
    action_id: str
    cwd: Optional[str] = None


class ApproveActionPayload(EventPayload):
    """Payload for cmd.approve_action."""
    action_id: str


class RunSafePayload(EventPayload):
    """Payload for cmd.run_safe."""
    command: str
    cwd: Optional[str] = None


# ===== System Payloads =====

class ComponentInitPayload(EventPayload):
    """Payload for component.initialized / component.failed."""
    component: str
    success: bool = True
    error: Optional[str] = None


class SystemStartupPayload(EventPayload):
    """Payload for system.startup."""
    version: str = ""
    components: List[str] = Field(default_factory=list)


class SystemShutdownPayload(EventPayload):
    """Payload for system.shutdown."""
    uptime_seconds: float = 0.0


class TranscriptPayload(EventPayload):
    """Payload for external.transcript."""
    text: str
    is_final: bool = False
    language: str = "pl"


# ===== Clipboard Payloads =====

class ClipboardUpdatedPayload(EventPayload):
    """Payload for pipeline.clipboard_updated."""
    auto_copied: int = 0
    queue_size: int = 0
    sources: List[str] = Field(default_factory=list)


class PasteSuggestedPayload(EventPayload):
    """Payload for pipeline.paste_suggested."""
    count: int = 0
    top_score: float = 0.0
    top_label: str = ""


# ===== Circuit Breaker Payloads =====

class CircuitBreakerPayload(EventPayload):
    """Payload for circuit breaker state changes."""
    step_name: str
    state: str  # "open", "closed", "half_open"
    failure_count: int = 0
    threshold: int = 0


# ===== Payload Registry =====

PAYLOAD_REGISTRY: Dict[str, type] = {
    EventType.WINDOWS_SCANNED.value: WindowsScannedPayload,
    EventType.SCREEN_CAPTURED.value: ScreenCapturedPayload,
    EventType.SCREEN_ORGANIZED.value: ScreenOrganizedPayload,
    EventType.CONTEXT_BUILT.value: ContextBuiltPayload,
    EventType.ANALYSIS_COMPLETED.value: AnalysisCompletedPayload,
    EventType.AGENT_SUGGESTED.value: AgentSuggestedPayload,
    EventType.BROADCAST_SENT.value: BroadcastSentPayload,
    EventType.CMD_SWITCH_OCR_ENGINE.value: SwitchOCRPayload,
    EventType.CMD_SWITCH_MODE.value: SwitchModePayload,
    EventType.CMD_EXECUTE_ACTION.value: ExecuteActionPayload,
    EventType.CMD_APPROVE_ACTION.value: ApproveActionPayload,
    EventType.CMD_RUN_SAFE.value: RunSafePayload,
    EventType.COMPONENT_INITIALIZED.value: ComponentInitPayload,
    EventType.COMPONENT_FAILED.value: ComponentInitPayload,
    EventType.SYSTEM_STARTUP.value: SystemStartupPayload,
    EventType.SYSTEM_SHUTDOWN.value: SystemShutdownPayload,
    EventType.TRANSCRIPT_RECEIVED.value: TranscriptPayload,
    EventType.SPEECH_FINAL.value: TranscriptPayload,
    EventType.PIPELINE_COMPLETED.value: PipelineCompletedPayload,
    EventType.CLIPBOARD_UPDATED.value: ClipboardUpdatedPayload,
    EventType.PASTE_SUGGESTED.value: PasteSuggestedPayload,
}


# ===== Factory =====

def typed_event(
    event_type: EventType,
    payload: EventPayload,
    source: str = "",
    correlation_id: str = "",
) -> Event:
    """
    Create a type-safe Event from a Pydantic payload.

    Validates the payload at creation time, then serializes to dict
    for backward compatibility with the existing Event dataclass.

    Args:
        event_type: EventType enum member
        payload: Pydantic model instance (validated)
        source: Component that emitted this event
        correlation_id: Links related events

    Returns:
        Event with validated, serialized data dict
    """
    return Event(
        type=event_type.value,
        data=payload.model_dump(exclude_none=True),
        source=source,
        correlation_id=correlation_id,
    )


def parse_payload(event: Event) -> Optional[EventPayload]:
    """
    Parse an Event's data dict into its typed payload model.

    Returns None if no registered payload type exists for this event type.
    Uses extra="allow" so unknown fields don't cause errors (forward compat).
    """
    payload_cls = PAYLOAD_REGISTRY.get(event.type)
    if payload_cls is None:
        return None
    return payload_cls(**event.data)
