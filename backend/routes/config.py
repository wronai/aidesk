"""Configuration routes: /config/*, /audio/*."""
import os
from typing import Dict

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from config_service import get_config_with_schema, update_env, discover_audio_devices

router = APIRouter()

_state: Dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/config")
async def get_config():
    """Get full configuration: current .env values + schema + audio devices."""
    return get_config_with_schema()


@router.post("/config")
async def post_config(request: Request):
    """Update .env configuration."""
    body = await request.json()
    if not isinstance(body, dict) or not body:
        return JSONResponse(status_code=400, content={"error": "Expected JSON object with key-value pairs"})

    for k, v in body.items():
        if not isinstance(k, str) or not isinstance(v, str):
            return JSONResponse(status_code=400, content={"error": f"Invalid key/value type for '{k}'"})

    updated = update_env(body)
    await _broadcast("config_changed", {"keys": list(body.keys())})
    return {"values": updated, "updated_keys": list(body.keys())}


@router.get("/audio/devices")
async def get_audio_devices():
    """List all available audio devices (PulseAudio/PipeWire + sounddevice)."""
    return discover_audio_devices()


@router.get("/config/ui", response_class=FileResponse)
async def config_ui():
    """Serve the configuration UI."""
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.html"))
