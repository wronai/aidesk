"""
Route package — decomposed from monolithic server.py.

Each module contains a FastAPI APIRouter for a specific domain.
All routers are aggregated here and included in the main app.
"""
from fastapi import APIRouter

from routes.core import router as core_router
from routes.ocr import router as ocr_router
from routes.windows import router as windows_router
from routes.agent import router as agent_router
from routes.events import router as events_router
from routes.screen import router as screen_router
from routes.memory import router as memory_router
from routes.templates import router as templates_router
from routes.config import router as config_router
from routes.clipboard import router as clipboard_router


def register_all_routes(app, app_state: dict, broadcast_fn):
    """
    Register all route modules on the FastAPI app.

    Each router receives app_state and broadcast via its module-level
    `init(state, broadcast)` function, then is included on the app.
    """
    # Initialize each router module with shared state
    from routes import core, ocr, windows, agent, events, screen, memory, templates, config, clipboard

    for mod in (core, ocr, windows, agent, events, screen, memory, templates, config, clipboard):
        mod.init(app_state, broadcast_fn)

    # Include routers
    app.include_router(core_router)
    app.include_router(ocr_router)
    app.include_router(windows_router)
    app.include_router(agent_router)
    app.include_router(events_router)
    app.include_router(screen_router)
    app.include_router(memory_router)
    app.include_router(templates_router)
    app.include_router(config_router)
    app.include_router(clipboard_router)
