"""Core routes: /, /stream, /status, /stats, /health, /diagnostics, /nfo, /profiles."""
import asyncio
import json
import time
from typing import Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

router = APIRouter()

_state: Dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/")
async def root():
    """Root endpoint."""
    from server import APP_VERSION
    return {
        "name": "AI Desktop Assistant API",
        "version": APP_VERSION,
        "status": "running",
        "endpoints": {
            "stream": "/stream - SSE endpoint for real-time updates",
            "status": "/status - Current status and latest data",
            "stats": "/stats - Detailed statistics",
            "health": "/health - Health check",
            "window": "/window - Active window info (GET)",
            "monitors": "/monitors - Connected monitors (GET)",
            "profiles": "/profiles - Per-app analysis profiles (GET)",
            "agent_actions": "/agent/actions - Pending agent actions (GET)",
            "events": "/events - Query event store (GET)",
            "pipeline_info": "/pipeline - Pipeline steps and stats (GET)",
            "config": "/config - Get/update .env configuration (GET/POST)",
            "config_ui": "/config/ui - Browser-based configuration UI (GET)",
        },
    }


@router.get("/stream")
async def sse_stream(request: Request):
    """Server-Sent Events endpoint for real-time updates."""
    queue = asyncio.Queue(maxsize=100)
    _state["subscribers"].append(queue)

    async def event_generator():
        try:
            yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except asyncio.TimeoutError:
                    yield "event: heartbeat\ndata: {}\n\n"
        except Exception:
            pass
        finally:
            if queue in _state["subscribers"]:
                _state["subscribers"].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/status")
async def status():
    """Get current status and latest data."""
    return {
        "latest_analysis": _state["latest_analysis"],
        "latest_transcript": _state["latest_transcript"],
        "active_subscribers": len(_state["subscribers"]),
        "context_items": len(_state["context"].history),
    }


@router.get("/stats")
async def stats():
    """Get detailed statistics."""
    uptime = time.time() - _state["stats"]["start_time"]
    stats_data = {
        "uptime_seconds": round(uptime),
        "uptime_formatted": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m",
        "total_screen_analyses": _state["stats"]["total_screen_analyses"],
        "total_transcripts": _state["stats"]["total_transcripts"],
        "total_errors": _state["stats"]["total_errors"],
        "active_subscribers": len(_state["subscribers"]),
    }

    for key in ("capture", "analyzer", "ocr_manager", "stt", "window_manager",
                "profile_manager", "shell_agent", "process_scanner", "window_cropper",
                "profile_selector", "multi_monitor", "semantic_memory",
                "action_library", "ocr_enhancer", "predictive_engine"):
        component = _state.get(key)
        if component and hasattr(component, "get_stats"):
            stats_key = "ocr" if key == "ocr_manager" else key
            stats_data[stats_key] = component.get_stats()

    stats_data["context"] = _state["context"].get_stats()
    return stats_data


@router.get("/health")
async def health():
    """Health check endpoint."""
    is_healthy = _state["capture"] is not None and _state["analyzer"] is not None
    return JSONResponse(
        status_code=200 if is_healthy else 503,
        content={
            "status": "healthy" if is_healthy else "unhealthy",
            "components": {
                k: _state.get(k) is not None
                for k in ("capture", "analyzer", "ocr_manager", "stt", "window_manager",
                          "profile_manager", "shell_agent", "process_scanner",
                          "window_cropper", "event_bus", "pipeline", "read_model")
            },
        },
    )


@router.get("/diagnostics")
async def diagnostics():
    """Get latest autodiagnostics result."""
    diag = _state.get("diagnostics")
    if not diag or not diag.get_latest():
        return JSONResponse(status_code=503, content={"error": "Diagnostics not yet available"})
    return diag.get_latest()


@router.get("/diagnostics/history")
async def diagnostics_history():
    """Get diagnostics history."""
    diag = _state.get("diagnostics")
    if not diag:
        return []
    return diag.get_history()


@router.get("/nfo/validation")
async def get_nfo_validation():
    """Get latest nfo startup validation result."""
    from server import _nfo_validate_startup
    return _nfo_validate_startup(_state)


@router.get("/profiles")
async def get_profiles():
    """Get all available per-app analysis profiles."""
    pm = _state.get("profile_manager")
    if not pm:
        return JSONResponse(status_code=503, content={"error": "Profile manager not initialized"})
    return {
        "profiles": pm.get_all_profiles(),
        "active": pm.active_category.value if pm.active_category else None,
        "stats": pm.get_stats(),
    }
