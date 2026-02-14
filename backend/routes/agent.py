"""Shell Agent routes: /agent/*."""
import nfo
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


@router.get("/agent/actions")
async def get_pending_actions():
    """Get pending agent actions awaiting approval."""
    agent = _state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})
    return {
        "pending": agent.get_pending_actions(),
        "stats": agent.get_stats(),
    }


@router.post("/agent/approve/{action_id}")
async def approve_action(action_id: str):
    """Approve a pending agent action."""
    agent = _state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})

    success = agent.approve_action(action_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": f"Action not found: {action_id}"})

    # Feed approval into action template learning loop
    library = _state.get("action_library")
    if library:
        # action_id may match a template_id if the action was template-generated
        library.learn_from_approval(action_id)

    return {"action_id": action_id, "status": "approved"}


@router.post("/agent/execute/{action_id}")
async def execute_action(action_id: str):
    """Execute an approved agent action."""
    agent = _state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})

    try:
        cwd = None
        latest_window = _state.get("latest_window")
        if latest_window and latest_window.get("cwd"):
            cwd = latest_window["cwd"]

        result = agent.execute_action(action_id, cwd=cwd)

        # Feed execution into action template learning loop
        library = _state.get("action_library")
        if library:
            library.learn_from_execution(action_id)

        await _broadcast("agent_result", result.to_dict())
        return result.to_dict()
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@router.get("/agent/history")
async def agent_history():
    """Get agent action execution history."""
    agent = _state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})
    return {
        "history": agent.get_history(n=50),
        "stats": agent.get_stats(),
    }


@router.post("/agent/run")
async def agent_run_safe(request: Request):
    """Run a safe (read-only) command directly."""
    agent = _state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})

    body = await request.json()
    command = body.get("command", "").strip()
    cwd = body.get("cwd")

    if not command:
        return JSONResponse(status_code=400, content={"error": "Missing 'command' field"})

    result = agent.execute_safe(command, cwd=cwd)
    return result.to_dict()
