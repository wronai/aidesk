"""Action Templates routes: /templates/*."""
import json
from typing import Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_state: Dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/templates")
async def get_action_templates():
    """Get all action templates with learning stats."""
    lib = _state.get("action_library")
    if not lib:
        return JSONResponse(status_code=503, content={"error": "Action templates not initialized"})
    return {
        "templates": [t.to_dict() for t in lib._templates.values()],
        "stats": lib.get_stats(),
    }


@router.post("/templates/import")
async def import_templates(request: Request):
    """Import action templates from JSON."""
    lib = _state.get("action_library")
    if not lib:
        return JSONResponse(status_code=503, content={"error": "Action templates not initialized"})
    body = await request.body()
    count = lib.import_templates(body.decode())
    return {"imported": count, "total": len(lib._templates)}


@router.get("/templates/export")
async def export_templates(include_stats: bool = False):
    """Export action templates as JSON."""
    lib = _state.get("action_library")
    if not lib:
        return JSONResponse(status_code=503, content={"error": "Action templates not initialized"})
    return JSONResponse(content=json.loads(lib.export_templates(include_stats=include_stats)))
