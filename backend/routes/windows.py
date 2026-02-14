"""Window routes: /window/*, /monitors, /processes, /windows/*."""
from typing import Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_state: Dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/window")
async def get_active_window():
    """Get current active window information."""
    wm = _state.get("window_manager")
    if not wm:
        return JSONResponse(status_code=503, content={"error": "Window awareness not enabled"})
    info = wm.get_active_window()
    return info.to_dict()


@router.get("/window/latest")
async def get_latest_window():
    """Get latest cached window info (no subprocess call)."""
    latest = _state.get("latest_window")
    if not latest:
        return JSONResponse(status_code=404, content={"error": "No window data yet"})
    return latest


@router.get("/monitors")
async def get_monitors():
    """Get list of connected monitors."""
    wm = _state.get("window_manager")
    capture = _state.get("capture")

    result = {"monitors": []}
    if wm:
        result["monitors"] = [m.to_dict() for m in wm.get_monitors()]
        result["source"] = "xrandr"
    elif capture:
        result["monitors"] = capture.get_monitors()
        result["source"] = "mss"
    return result


@router.get("/window/stats")
async def window_stats():
    """Get window manager statistics."""
    wm = _state.get("window_manager")
    if not wm:
        return JSONResponse(status_code=503, content={"error": "Window awareness not enabled"})
    return wm.get_stats()


@router.get("/processes")
async def get_all_processes():
    """Scan all visible windows with process info."""
    scanner = _state.get("process_scanner")
    if not scanner:
        return JSONResponse(status_code=503, content={"error": "Process scanner not initialized"})
    return scanner.get_window_layout()


@router.get("/windows/all")
async def get_all_windows():
    """Get all visible windows with geometry and process details."""
    scanner = _state.get("process_scanner")
    if not scanner:
        return JSONResponse(status_code=503, content={"error": "Process scanner not initialized"})
    windows = scanner.scan_all_windows()
    return {
        "total": len(windows),
        "windows": [w.to_dict() for w in windows],
    }


@router.get("/multi-monitor")
async def get_multi_monitor():
    """Get multi-monitor snapshot and activity analysis."""
    mm = _state.get("multi_monitor")
    wm = _state.get("window_manager")
    if not mm:
        return JSONResponse(status_code=503, content={"error": "Multi-monitor not initialized"})

    monitors = wm.get_monitors() if wm else []
    scanner = _state.get("process_scanner")
    all_windows = scanner.last_windows if scanner else []
    active_window = wm.get_active_window() if wm else None

    snapshot = mm.build_snapshot(
        monitors=monitors,
        all_windows=all_windows,
        active_window=active_window,
    )
    return snapshot.to_dict()
