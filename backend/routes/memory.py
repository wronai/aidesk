"""Semantic Memory routes: /memory/*."""
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


@router.get("/memory/search")
async def search_memory(q: str = "", k: int = 5, type: Optional[str] = None):
    """Semantic search through memory (or recent items if no query)."""
    sm = _state.get("semantic_memory")
    if not sm:
        return JSONResponse(status_code=503, content={"error": "Semantic memory not initialized"})

    if q:
        items = sm.recall_relevant(q, k=k, context_type=type)
    else:
        items = sm.recall_recent(n=k, context_type=type)

    return {
        "query": q,
        "results": [item.to_dict() for item in items],
        "total": len(items),
    }


@router.get("/memory/stats")
async def memory_stats():
    """Get semantic memory statistics."""
    sm = _state.get("semantic_memory")
    if not sm:
        return JSONResponse(status_code=503, content={"error": "Semantic memory not initialized"})
    return sm.get_stats()


@router.post("/memory/compress")
async def compress_memory():
    """Manually trigger memory compression."""
    sm = _state.get("semantic_memory")
    if not sm:
        return JSONResponse(status_code=503, content={"error": "Semantic memory not initialized"})
    count = sm.compress_old_context()
    return {"compressed": count, "remaining": sm.total_memories}
