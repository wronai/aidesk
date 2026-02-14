"""Event Bus & Pipeline routes: /events/*, /pipeline/*, /read-model/*."""
import nfo
from typing import Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_state: Dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/events")
async def query_events(
    type: Optional[str] = None,
    source: Optional[str] = None,
    correlation_id: Optional[str] = None,
    since: Optional[float] = None,
    limit: int = 50,
):
    """Query the event store (Event Sourcing read side)."""
    bus = _state.get("event_bus")
    if not bus or not bus.store:
        return JSONResponse(status_code=503, content={"error": "Event store not enabled"})
    events = bus.store.query(
        event_type=type, source=source,
        correlation_id=correlation_id, since=since, limit=limit,
    )
    return {"total": len(events), "events": events}


@router.get("/events/stats")
async def event_bus_stats():
    """Get event bus and event store statistics."""
    bus = _state.get("event_bus")
    if not bus:
        return JSONResponse(status_code=503, content={"error": "Event bus not initialized"})
    return bus.get_stats()


@router.get("/pipeline")
async def pipeline_info():
    """Get pipeline configuration, steps, and execution statistics."""
    pipeline = _state.get("pipeline")
    if not pipeline:
        return JSONResponse(status_code=503, content={"error": "Pipeline not initialized"})
    result = {
        "steps": pipeline.get_step_names(),
        "stats": pipeline.get_stats(),
    }
    ps = _state.get("profile_selector")
    if ps:
        result["profile_selector"] = ps.get_stats()
    return result


@router.get("/read-model")
async def get_read_model():
    """Get CQRS read model — materialized views."""
    qry = _state.get("query_handlers")
    if not qry:
        return JSONResponse(status_code=503, content={"error": "Query handlers not initialized"})
    return {
        "pipeline": qry.read_model.get_pipeline_view(),
        "analysis": qry.read_model.get_analysis_view(),
        "event_counts": qry.read_model.get_event_counts(),
    }


@router.get("/read-model/pipeline")
async def get_read_model_pipeline():
    """Get pipeline execution view from CQRS read model."""
    qry = _state.get("query_handlers")
    if not qry:
        return JSONResponse(status_code=503, content={"error": "Query handlers not initialized"})
    return qry.query_pipeline()


@router.get("/read-model/stats")
async def get_read_model_stats():
    """Get enriched stats from CQRS read model."""
    qry = _state.get("query_handlers")
    if not qry:
        return JSONResponse(status_code=503, content={"error": "Query handlers not initialized"})
    return qry.query_stats()


@router.get("/traces")
async def get_traces(name: Optional[str] = None, n: int = 20):
    """Get tracer metrics and recent spans."""
    from observability import get_tracer
    tracer = get_tracer()
    return {
        "stats": tracer.get_stats(),
        "recent_spans": tracer.get_recent_spans(n=n, name=name),
    }


@router.get("/predictive")
async def get_predictive_stats():
    """Get predictive engine stats and transition patterns."""
    pe = _state.get("predictive_engine")
    if not pe:
        return JSONResponse(status_code=503, content={"error": "Predictive engine not initialized"})
    return {
        "stats": pe.get_stats(),
        "transition_matrix": pe.get_transition_matrix(),
        "top_patterns": pe.get_top_patterns(10),
    }
