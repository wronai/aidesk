"""
Query Handlers — CQRS Read Side.

Handles read-only queries as typed events via the EventBus.
Queries never mutate state — they only project current state into responses.

Single Responsibility: each handler projects one view of the state.
Interface Segregation: callers only see the query interface they need.
Liskov Substitution: all handlers follow the same (Event) -> None contract.
"""
import time
import structlog
from typing import Any, Callable, Dict, Optional

from event_bus import Event, EventBus, EventType

logger = structlog.get_logger()


class ReadModel:
    """
    CQRS Read Model — materialized view of application state.

    Updated by subscribing to domain events from the pipeline.
    Queried by API endpoints and query handlers.

    Separation of Concerns: read model is separate from write model (app_state).
    This allows optimizing reads independently of writes.
    """

    def __init__(self):
        # Pipeline run metrics
        self.last_pipeline_run_id: str = ""
        self.last_pipeline_correlation_id: str = ""
        self.last_pipeline_steps: list = []
        self.last_pipeline_timings: dict = {}
        self.last_pipeline_errors: list = []
        self.total_pipeline_runs: int = 0
        self.total_pipeline_errors: int = 0

        # Aggregated event counts
        self.event_counts: Dict[str, int] = {}

        # Latest state projections
        self.last_window_count: int = 0
        self.last_analysis_tokens: int = 0
        self.last_analysis_cost: float = 0.0
        self.last_analysis_provider: str = ""
        self.last_capture_size_kb: float = 0.0
        self.last_agent_action_count: int = 0
        self.total_agent_suggestions: int = 0

    def on_event(self, event: Event):
        """Update read model from any domain event (projection)."""
        etype = event.type
        self.event_counts[etype] = self.event_counts.get(etype, 0) + 1

    def on_windows_scanned(self, data: Dict):
        self.last_window_count = data.get("total", 0)

    def on_screen_captured(self, data: Dict):
        self.last_capture_size_kb = data.get("size_kb", 0)

    def on_analysis_completed(self, data: Dict):
        self.last_analysis_tokens = data.get("tokens", 0)
        self.last_analysis_cost = data.get("cost", 0.0)
        self.last_analysis_provider = data.get("provider", "")

    def on_agent_suggested(self, data: Dict):
        count = data.get("count", 0)
        self.last_agent_action_count = count
        self.total_agent_suggestions += count

    def on_pipeline_completed(self, run_id: str, steps: list, timings: dict, errors: list):
        self.last_pipeline_run_id = run_id
        self.last_pipeline_steps = steps
        self.last_pipeline_timings = timings
        self.last_pipeline_errors = errors
        self.total_pipeline_runs += 1
        if errors:
            self.total_pipeline_errors += 1

    def get_pipeline_view(self) -> Dict:
        """Materialized view of pipeline state."""
        return {
            "last_run_id": self.last_pipeline_run_id,
            "total_runs": self.total_pipeline_runs,
            "total_errors": self.total_pipeline_errors,
            "last_steps": self.last_pipeline_steps,
            "last_timings": self.last_pipeline_timings,
            "last_errors": self.last_pipeline_errors,
        }

    def get_analysis_view(self) -> Dict:
        """Materialized view of analysis metrics."""
        return {
            "last_tokens": self.last_analysis_tokens,
            "last_cost": self.last_analysis_cost,
            "last_provider": self.last_analysis_provider,
            "last_capture_size_kb": self.last_capture_size_kb,
            "last_window_count": self.last_window_count,
            "last_agent_actions": self.last_agent_action_count,
            "total_agent_suggestions": self.total_agent_suggestions,
        }

    def get_event_counts(self) -> Dict:
        """Event type frequency counts."""
        return dict(sorted(self.event_counts.items(), key=lambda x: -x[1]))


class QueryHandlers:
    """
    Registry of CQRS query handlers.

    Subscribes to query.* events and also provides direct query methods
    for API endpoints. Does NOT mutate state.
    """

    def __init__(self, bus: EventBus, app_state: Dict[str, Any], read_model: ReadModel):
        self.bus = bus
        self.state = app_state
        self.read_model = read_model

    def register_all(self):
        """Subscribe domain event projectors to the EventBus."""
        # Project domain events into read model
        self.bus.subscribe(EventType.WINDOWS_SCANNED.value, self._project_windows)
        self.bus.subscribe(EventType.SCREEN_CAPTURED.value, self._project_capture)
        self.bus.subscribe(EventType.ANALYSIS_COMPLETED.value, self._project_analysis)
        self.bus.subscribe(EventType.AGENT_SUGGESTED.value, self._project_agent)

        # Pipeline completion
        self.bus.subscribe("pipeline.completed", self._project_pipeline)

        # Wildcard: count all events
        self.bus.subscribe("*", self._project_all)

        logger.info("Query handlers registered (read model projectors)")

    async def _project_windows(self, event: Event):
        self.read_model.on_windows_scanned(event.data)

    async def _project_capture(self, event: Event):
        self.read_model.on_screen_captured(event.data)

    async def _project_analysis(self, event: Event):
        self.read_model.on_analysis_completed(event.data)

    async def _project_agent(self, event: Event):
        self.read_model.on_agent_suggested(event.data)

    async def _project_pipeline(self, event: Event):
        self.read_model.on_pipeline_completed(
            run_id=event.data.get("run_id", ""),
            steps=event.data.get("steps_executed", []),
            timings=event.data.get("step_timings", {}),
            errors=event.data.get("errors", []),
        )

    async def _project_all(self, event: Event):
        self.read_model.on_event(event)

    # ── Direct query methods for API endpoints ──

    def query_health(self) -> Dict:
        """Query system health (read-only)."""
        components = {}
        for key in ["capture", "analyzer", "ocr_manager", "stt",
                     "window_manager", "profile_manager", "shell_agent",
                     "process_scanner", "window_cropper", "event_bus", "pipeline"]:
            obj = self.state.get(key)
            components[key] = "ok" if obj is not None else "not_initialized"

        all_ok = all(v == "ok" for k, v in components.items()
                     if k not in ("stt",))  # STT is optional

        return {
            "status": "healthy" if all_ok else "degraded",
            "components": components,
            "uptime_seconds": round(time.time() - self.state["stats"]["start_time"], 1),
        }

    def query_stats(self) -> Dict:
        """Query detailed stats (read-only)."""
        stats = self.state["stats"].copy()
        uptime = time.time() - stats["start_time"]
        stats["uptime_seconds"] = round(uptime, 1)
        stats["uptime_formatted"] = f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m"

        # Enrich with read model
        stats["pipeline"] = self.read_model.get_pipeline_view()
        stats["analysis"] = self.read_model.get_analysis_view()
        stats["event_counts"] = self.read_model.get_event_counts()

        # Event bus stats
        bus_stats = self.bus.get_stats()
        stats["event_bus"] = bus_stats

        return stats

    def query_events(
        self,
        event_type: str = None,
        source: str = None,
        correlation_id: str = None,
        since: float = None,
        limit: int = 50,
    ) -> Dict:
        """Query events from the event store."""
        if not self.bus.store:
            return {"events": [], "total": 0}

        events = self.bus.store.query(
            event_type=event_type,
            source=source,
            correlation_id=correlation_id,
            since=since,
            limit=limit,
        )
        return {
            "events": events,
            "total": len(events),
            "query": {
                "type": event_type,
                "source": source,
                "correlation_id": correlation_id,
                "since": since,
                "limit": limit,
            },
        }

    def query_pipeline(self) -> Dict:
        """Query pipeline execution state."""
        pipeline = self.state.get("pipeline")
        result = self.read_model.get_pipeline_view()
        if pipeline:
            result["steps"] = pipeline.get_step_names()
            result["orchestrator"] = pipeline.get_stats()
        return result

    def query_event_store_stats(self) -> Dict:
        """Query event store statistics."""
        if not self.bus.store:
            return {"enabled": False}
        stats = self.bus.store.get_stats()
        stats["enabled"] = True
        return stats
