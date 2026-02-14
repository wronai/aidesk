"""Screen routes: /screen/*, /screenshots/*, /crops/*, /browser."""
import nfo
import os
from typing import Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

_state: Dict = {}
_broadcast = None


def _get_crops_dir() -> str:
    """Resolve crop directory from env (matches settings.crops_dir)."""
    return os.getenv("CROPS_DIR", "/tmp/proxeen_crops")


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/screen/organized")
async def get_organized_screen():
    """Get latest organized screen data (per-app crops + categories)."""
    data = _state.get("latest_organized_screen")
    if not data:
        return JSONResponse(status_code=404, content={"error": "No organized screen data yet"})
    return data


@router.get("/screen/stats")
async def get_screen_stats():
    """Get process scanner and window cropper statistics."""
    result = {}
    scanner = _state.get("process_scanner")
    if scanner:
        result["process_scanner"] = scanner.get_stats()
    cropper = _state.get("window_cropper")
    if cropper:
        result["window_cropper"] = cropper.get_stats()
    return result


@router.get("/screenshots")
async def list_screenshots():
    """List all saved screenshots."""
    captures_dir = os.getenv("CAPTURES_DIR", "/tmp/proxeen_captures")
    if not os.path.exists(captures_dir):
        return []

    files = []
    for f in os.listdir(captures_dir):
        if f.endswith(".jpg"):
            path = os.path.join(captures_dir, f)
            st = os.stat(path)
            files.append({"name": f, "timestamp": st.st_mtime, "size": st.st_size, "url": f"/screenshots/{f}"})

    files.sort(key=lambda x: x["timestamp"], reverse=True)
    return files


@router.get("/screenshots/{filename}")
async def get_screenshot(filename: str):
    """Serve a specific screenshot file."""
    captures_dir = os.getenv("CAPTURES_DIR", "/tmp/proxeen_captures")
    file_path = os.path.join(captures_dir, filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(captures_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@router.get("/crops")
async def list_crops():
    """List all saved per-app crop files."""
    crops_dir = _get_crops_dir()
    if not os.path.exists(crops_dir):
        return []

    files = []
    for f in os.listdir(crops_dir):
        if f.endswith(".jpg"):
            path = os.path.join(crops_dir, f)
            st = os.stat(path)
            files.append({"name": f, "timestamp": st.st_mtime, "size": st.st_size, "url": f"/crops/{f}"})

    files.sort(key=lambda x: x["timestamp"], reverse=True)
    return files


@router.get("/crops/{filename}")
async def get_crop(filename: str):
    """Serve a specific crop file."""
    crops_dir = _get_crops_dir()
    file_path = os.path.join(crops_dir, filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(crops_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@router.get("/browser", response_class=FileResponse)
async def screenshot_browser():
    """Serve the screenshot browser UI."""
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "screenshots.html"))
