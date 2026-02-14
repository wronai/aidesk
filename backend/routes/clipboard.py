"""Clipboard Intelligence routes — queue, suggestions, snippets."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from typing import Dict

router = APIRouter(tags=["clipboard"])

_state: dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/clipboard/queue")
async def get_clipboard_queue(n: int = 10):
    """Get clipboard queue (newest first)."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})
    items = mgr.queue.get_recent(n)
    return {"items": [i.to_dict() for i in items], "total": len(mgr.queue)}


@router.get("/clipboard/suggestions")
async def get_paste_suggestions():
    """Get paste suggestions for current window context."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})

    # Use latest window category for context
    from window_aware import AppCategory
    latest_window = _state.get("latest_window")
    category = AppCategory.UNKNOWN
    if latest_window and "category" in latest_window:
        try:
            category = AppCategory(latest_window["category"])
        except ValueError:
            pass

    screen_text = _state.get("latest_analysis", "")
    suggestions = mgr.suggest_paste(category, screen_text)
    return {"suggestions": [s.to_dict() for s in suggestions]}


@router.post("/clipboard/push")
async def push_to_clipboard(request: Request):
    """Manually push text to clipboard queue."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})

    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "text required"})

    label = body.get("label", "")
    item = mgr.push(text, label=label)
    if _broadcast:
        await _broadcast("clipboard_updated", {"queue_size": len(mgr.queue)})
    return item.to_dict()


@router.post("/clipboard/paste/{index}")
async def mark_pasted(index: int):
    """Record that item at index was pasted (boosts future relevance)."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})

    items = mgr.queue.get_recent(index + 1)
    if index >= len(items):
        return JSONResponse(status_code=404, content={"error": "Item not found"})

    mgr.mark_pasted(items[index].text)
    return {"ok": True, "text": items[index].text[:60]}


@router.post("/clipboard/pin")
async def pin_item(request: Request):
    """Pin/unpin a clipboard item."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})

    body = await request.json()
    text = body.get("text", "")
    pinned = body.get("pinned", True)

    if pinned:
        ok = mgr.queue.pin(text)
    else:
        ok = mgr.queue.unpin(text)
    return {"ok": ok}


@router.get("/clipboard/snippets")
async def get_snippets():
    """Get all registered snippets."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})
    return {"snippets": [s.to_dict() for s in mgr.snippets.get_all()]}


@router.post("/clipboard/snippets")
async def add_snippet(request: Request):
    """Add a new snippet."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})

    body = await request.json()
    trigger = body.get("trigger", "").strip()
    expansion = body.get("expansion", "").strip()
    if not trigger or not expansion:
        return JSONResponse(status_code=400, content={"error": "trigger and expansion required"})

    mgr.snippets.add(trigger, expansion, label=body.get("label", ""), category=body.get("category", ""))
    return {"ok": True, "trigger": trigger}


@router.post("/analyze-selection")
async def analyze_selection(request: Request):
    """Analyze selected/highlighted text and return structured response."""
    from clipboard_intel import SelectionAnalyzer

    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "text required"})

    analyzer = SelectionAnalyzer()
    result = analyzer.analyze(text)

    # Auto-push clipboard_text to queue if available
    mgr = _state.get("clipboard_manager")
    if mgr and result.clipboard_text:
        from clipboard_intel import ClipSource
        mgr.push(result.clipboard_text, source=ClipSource.AUTO, label=result.label)

    # Broadcast to overlay via SSE
    if _broadcast:
        await _broadcast("selection_analysis", result.to_dict())

    return result.to_dict()


@router.get("/clipboard/stats")
async def clipboard_stats():
    """Get clipboard intelligence statistics."""
    mgr = _state.get("clipboard_manager")
    if not mgr:
        return JSONResponse(status_code=503, content={"error": "Clipboard not initialized"})
    return mgr.get_stats()
