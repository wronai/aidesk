"""
AI Desktop Assistant - FastAPI Backend Server
"""
import asyncio
import json
import os
import time
import sqlite3
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import structlog
from loguru import logger as loguru_logger
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import nfo

from capture import create_capture_from_env
from analyzer import create_analyzer_from_env
from ocr_engines import create_ocr_manager_from_env
from context import ContextManager
from diagnostics import AutoDiagnostics
from window_aware import WindowManager, create_window_manager_from_env
from app_profiles import ProfileManager, create_profile_manager
from shell_agent import ShellAgent, create_shell_agent_from_env
from process_scanner import ProcessScanner, create_process_scanner
from window_cropper import WindowCropper, create_window_cropper
from event_bus import EventBus, EventStore, Event, EventType, create_event_bus
from pipeline import PipelineOrchestrator, PipelineContext, PipelineProfile, ProfileSelector, create_pipeline, create_profile_selector
from command_handlers import CommandHandlers
from query_handlers import QueryHandlers, ReadModel
from config_service import get_config_with_schema, read_env, update_env, discover_audio_devices
from multi_monitor import MonitorAwareCapture, create_multi_monitor_from_env
from semantic_memory import SemanticMemory, create_semantic_memory_from_env
from action_templates import AppActionLibrary, create_action_library_from_env
from ocr_post_process import OCREnhancer, create_ocr_enhancer_from_env
from predictive_engine import PredictiveAnalyzer, create_predictive_engine_from_env

# Lazy STT import - sounddevice may not be available
def _import_stt():
    try:
        from stt import create_stt_from_env
        return create_stt_from_env
    except (ImportError, OSError) as e:
        structlog.get_logger().warning("STT module unavailable", error=str(e))
        return None

# Load environment variables
load_dotenv()

# Read version from VERSION file
def _read_version() -> str:
    version_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
    try:
        with open(version_file) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "2.0.0"

APP_VERSION = _read_version()

# Configure nfo structured function logging (SQLite + Markdown)
os.makedirs("logs", exist_ok=True)
nfo_logger = nfo.configure(
    name="aidesk",
    level=os.getenv("LOG_LEVEL", "INFO"),
    sinks=["sqlite:logs/nfo_aidesk.db", "md:logs/nfo_aidesk.md"],
    bridge_stdlib=False,
    force=True,
)

# Configure structured logging
def setup_logging():
    log_file = os.getenv("LOG_FILE", "logs/assistant.log")
    db_file = os.getenv("LOG_DB", "logs/logs.sqlite")
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Ensure logs directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Loguru configuration
    loguru_logger.remove() # Remove default handler
    
    # Console handler
    loguru_logger.add(lambda msg: print(msg, end=""), level=log_level)
    
    # File handler
    loguru_logger.add(log_file, rotation="10 MB", level=log_level, format="{time} | {level} | {message}")

    # SQLite handler
    def sqlite_sink(message):
        record = message.record
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS logs (timestamp TEXT, level TEXT, message TEXT, module TEXT)"
            )
            cursor.execute(
                "INSERT INTO logs VALUES (?, ?, ?, ?)",
                (record["time"].isoformat(), record["level"].name, record["message"], record["module"])
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Failed to log to SQLite: {e}")

    loguru_logger.add(sqlite_sink, level=log_level)

    # Structlog configuration to use loguru
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=lambda *args: loguru_logger,
        wrapper_class=structlog.make_filtering_bound_logger(20), # INFO
        cache_logger_on_first_use=True,
    )

setup_logging()
logger = structlog.get_logger()

# Global application state
app_state = {
    "latest_analysis": "",
    "latest_transcript": "",
    "context": ContextManager(max_items=int(os.getenv("MAX_CONTEXT_ITEMS", "20"))),
    "subscribers": [],
    "capture": None,
    "analyzer": None,
    "ocr_manager": None,
    "stt": None,
    "diagnostics": None,
    "window_manager": None,
    "profile_manager": None,
    "shell_agent": None,
    "process_scanner": None,
    "window_cropper": None,
    "event_bus": None,
    "pipeline": None,
    "profile_selector": None,
    "read_model": None,
    "command_handlers": None,
    "query_handlers": None,
    "multi_monitor": None,
    "semantic_memory": None,
    "action_library": None,
    "ocr_enhancer": None,
    "predictive_engine": None,
    "latest_window": None,
    "latest_organized_screen": None,
    "stats": {
        "start_time": time.time(),
        "total_screen_analyses": 0,
        "total_transcripts": 0,
        "total_errors": 0,
    },
}


async def broadcast(event_type: str, data: Dict):
    """
    Broadcast SSE event to all connected overlay clients.

    Args:
        event_type: Event type name
        data: Event data dict
    """
    if not app_state["subscribers"]:
        return

    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    # Send to all subscribers
    dead_queues = []
    for queue in app_state["subscribers"]:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead_queues.append(queue)

    # Remove dead subscribers
    for queue in dead_queues:
        app_state["subscribers"].remove(queue)
        logger.debug("Removed full subscriber queue")


async def screen_analysis_loop():
    """
    Main loop: delegates to PipelineOrchestrator (SOLID/CQRS/Event Sourcing).

    Pipeline profiles (adaptive per tick):
    - FAST:   skip cropping, cached window scan, low-latency
    - NORMAL: cached scan, top-K crops, hybrid analysis
    - FULL:   full scan, crop all, deep analysis (periodic or on app switch)

    Pipeline order (composable steps, each emits events to EventBus):
    1. ScanWindows        → scan all visible windows (cached on FAST/NORMAL)
    2. DetectActiveWindow  → detect active window, build window context, ROI
    3. CaptureScreen       → capture fullscreen or ROI screenshot
    4. CropWindows         → crop visible apps (skipped on FAST)
    5. BuildContext        → build rich context from window info + profiles + TTS
    6. Analyze             → OCR + LLM analysis
    7. SuggestActions      → shell agent suggests commands
    8. BuildBroadcast      → assemble SSE broadcast payload

    Each step is independently testable, swappable, and emits typed events.
    """
    pipeline: PipelineOrchestrator = app_state["pipeline"]
    bus: EventBus = app_state["event_bus"]
    capture = app_state["capture"]
    context_mgr = app_state["context"]
    profile_selector: ProfileSelector = app_state["profile_selector"]
    prev_active_wid = 0

    logger.info(
        "Screen analysis loop started (pipeline-based, profile-aware)",
        steps=pipeline.get_step_names(),
        total_steps=len(pipeline.steps),
    )

    while True:
        try:
            # Select pipeline profile for this tick
            ctx = PipelineContext()
            profile = profile_selector.select(ctx, capture=capture)
            ctx.profile = profile.value

            # Execute all pipeline steps (profile-aware gating)
            ctx = await pipeline.run(ctx)

            # Notify selector on active window change (triggers FULL next tick)
            if ctx.active_window and hasattr(ctx.active_window, 'window_id'):
                new_wid = ctx.active_window.window_id
                if new_wid != prev_active_wid:
                    profile_selector.notify_active_window_changed(new_wid)
                    prev_active_wid = new_wid

            # ── Post-pipeline: update shared state & SSE broadcasts ──

            # Update latest window state
            if ctx.active_window:
                app_state["latest_window"] = ctx.active_window.to_dict()
                await broadcast("window", ctx.active_window.to_dict())

            # Broadcast all-windows layout
            if ctx.all_windows:
                await broadcast("windows_layout", {
                    "total": len(ctx.all_windows),
                    "windows": [w.to_dict() for w in ctx.all_windows],
                })

            # Broadcast organized screen
            if ctx.organized_screen:
                app_state["latest_organized_screen"] = ctx.organized_screen.to_dict()
                await broadcast("organized_screen", {
                    "total_windows": ctx.organized_screen.total_windows,
                    "summary": ctx.organized_screen.screen_summary,
                    "active_app": (
                        ctx.organized_screen.active_app.window.to_dict()
                        if ctx.organized_screen.active_app else None
                    ),
                    "categories": list(ctx.organized_screen.by_category.keys()),
                })

            # Store analysis and broadcast
            if ctx.analysis_result:
                analysis = ctx.analysis_result
                app_state["latest_analysis"] = analysis["text"]
                app_state["stats"]["total_screen_analyses"] += 1

                # Add to context history
                context_mgr.add(
                    content=analysis["text"][:200],
                    context_type="screen",
                    metadata={
                        "tokens": analysis.get("tokens", 0),
                        "cost": analysis.get("cost", 0.0),
                        "provider": analysis.get("provider", "unknown"),
                        "window": ctx.active_window.title if ctx.active_window else None,
                        "category": ctx.active_window.category.value if ctx.active_window else None,
                        "organized_windows": ctx.organized_screen.total_windows if ctx.organized_screen else 0,
                        "pipeline_run_id": ctx.run_id,
                        "steps_executed": ctx.steps_executed,
                        "step_timings": ctx.step_timings,
                    },
                )

                # Broadcast agent actions
                if ctx.agent_actions:
                    await broadcast("agent_actions", {"actions": ctx.agent_actions})

                # Broadcast main analysis payload
                if ctx.broadcast_data:
                    await broadcast("analysis", ctx.broadcast_data)

            # Log pipeline metrics
            if ctx.errors:
                for err in ctx.errors:
                    logger.warning("Pipeline step error", **err)

            # Adaptive sleep based on capture interval
            await asyncio.sleep(capture.adaptive_interval)

        except Exception as e:
            logger.error("Screen analysis loop error", error=str(e))
            app_state["stats"]["total_errors"] += 1
            await broadcast("error", {"message": f"Screen analysis error: {str(e)}"})
            await asyncio.sleep(5)


async def on_transcript(text: str, is_final: bool):
    """
    Callback for STT transcripts.

    Args:
        text: Transcript text
        is_final: Whether this is final or interim result
    """
    app_state["latest_transcript"] = text

    if is_final:
        app_state["stats"]["total_transcripts"] += 1
        app_state["context"].add(
            content=text, context_type="speech", metadata={"language": "pl"}
        )

    await broadcast("transcript", {"text": text, "is_final": is_final})

    # Emit to EventBus
    bus = app_state.get("event_bus")
    if bus:
        etype = EventType.SPEECH_FINAL.value if is_final else EventType.TRANSCRIPT_RECEIVED.value
        await bus.publish(Event(
            type=etype,
            data={"text": text, "is_final": is_final},
            source="stt",
        ))


@nfo.log_call(level="INFO")
def _nfo_validate_startup(state: Dict):
    """
    nfo-instrumented startup validation.
    Logs all component initialization statuses to SQLite + Markdown.
    """
    components = {
        "capture": state.get("capture") is not None,
        "analyzer": state.get("analyzer") is not None,
        "ocr_manager": state.get("ocr_manager") is not None,
        "window_manager": state.get("window_manager") is not None,
        "profile_manager": state.get("profile_manager") is not None,
        "shell_agent": state.get("shell_agent") is not None,
        "process_scanner": state.get("process_scanner") is not None,
        "window_cropper": state.get("window_cropper") is not None,
        "event_bus": state.get("event_bus") is not None,
        "pipeline": state.get("pipeline") is not None,
        "read_model": state.get("read_model") is not None,
        "multi_monitor": state.get("multi_monitor") is not None,
        "semantic_memory": state.get("semantic_memory") is not None,
        "action_library": state.get("action_library") is not None,
        "ocr_enhancer": state.get("ocr_enhancer") is not None,
        "predictive_engine": state.get("predictive_engine") is not None,
    }
    ok = [k for k, v in components.items() if v]
    failed = [k for k, v in components.items() if not v]

    logger.info(
        "nfo startup validation",
        ok_components=ok,
        failed_components=failed,
        all_ok=len(failed) == 0,
    )

    return {
        "all_ok": len(failed) == 0,
        "ok": ok,
        "failed": failed,
        "total": len(components),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup/shutdown.
    """
    # Startup
    logger.info("Starting AI Desktop Assistant backend")

    # Initialize core components
    app_state["capture"] = create_capture_from_env()
    app_state["ocr_manager"] = create_ocr_manager_from_env()
    app_state["analyzer"] = create_analyzer_from_env(ocr_manager=app_state["ocr_manager"])

    # Initialize window awareness (Linux X11/Wayland)
    if os.getenv("ENABLE_WINDOW_AWARE", "true").lower() == "true":
        try:
            app_state["window_manager"] = create_window_manager_from_env()
            logger.info("Window awareness enabled")
        except Exception as e:
            logger.warning("Window awareness initialization failed", error=str(e))

    # Initialize per-app profiles
    app_state["profile_manager"] = create_profile_manager()

    # Initialize shell agent
    if os.getenv("ENABLE_SHELL_AGENT", "true").lower() == "true":
        try:
            app_state["shell_agent"] = create_shell_agent_from_env()
            logger.info("Shell agent enabled")
        except Exception as e:
            logger.warning("Shell agent initialization failed", error=str(e))

    # Initialize process scanner and window cropper
    try:
        app_state["process_scanner"] = create_process_scanner(
            window_manager=app_state.get("window_manager")
        )
        app_state["window_cropper"] = create_window_cropper(
            process_scanner=app_state["process_scanner"]
        )
        logger.info("Process scanner & window cropper enabled")
    except Exception as e:
        logger.warning("Process scanner/cropper initialization failed", error=str(e))

    # Initialize Tier 1 modules: Multi-Monitor, Semantic Memory, Action Templates, OCR Post-Processing, Predictive Engine
    try:
        app_state["multi_monitor"] = create_multi_monitor_from_env()
        logger.info("Multi-monitor intelligence enabled")
    except Exception as e:
        logger.warning("Multi-monitor initialization failed", error=str(e))

    try:
        app_state["semantic_memory"] = create_semantic_memory_from_env()
        logger.info("Semantic memory enabled", model=app_state["semantic_memory"].model_name if app_state["semantic_memory"].enabled else "disabled")
    except Exception as e:
        logger.warning("Semantic memory initialization failed", error=str(e))

    try:
        app_state["action_library"] = create_action_library_from_env()
        logger.info("Action templates enabled", templates=len(app_state["action_library"]._templates))
    except Exception as e:
        logger.warning("Action templates initialization failed", error=str(e))

    try:
        app_state["ocr_enhancer"] = create_ocr_enhancer_from_env()
        logger.info("OCR post-processing enabled")
    except Exception as e:
        logger.warning("OCR post-processing initialization failed", error=str(e))

    try:
        app_state["predictive_engine"] = create_predictive_engine_from_env()
        logger.info("Predictive pre-fetching enabled")
    except Exception as e:
        logger.warning("Predictive engine initialization failed", error=str(e))

    # nfo startup validation — log all initialized components
    _nfo_validate_startup(app_state)

    # Initialize Event Bus (Event Sourcing + CQRS)
    app_state["event_bus"] = create_event_bus(
        enable_store=True,
        db_path=os.getenv("EVENT_STORE_DB", "logs/events.db"),
    )

    # Initialize Pipeline Orchestrator (SOLID composable steps)
    app_state["pipeline"] = create_pipeline(
        bus=app_state["event_bus"],
        capture=app_state["capture"],
        analyzer=app_state["analyzer"],
        context_mgr=app_state["context"],
        window_mgr=app_state.get("window_manager"),
        profile_mgr=app_state.get("profile_manager"),
        shell_agent=app_state.get("shell_agent"),
        process_scanner=app_state.get("process_scanner"),
        window_cropper=app_state.get("window_cropper"),
        app_state_ref=app_state,
        multi_monitor=app_state.get("multi_monitor"),
        semantic_memory=app_state.get("semantic_memory"),
        action_library=app_state.get("action_library"),
        ocr_enhancer=app_state.get("ocr_enhancer"),
        predictive_engine=app_state.get("predictive_engine"),
    )

    # Pipeline Profile Selector (adaptive FAST/NORMAL/FULL routing)
    app_state["profile_selector"] = create_profile_selector()

    # CQRS Read Model (materialized views for queries)
    read_model = ReadModel()
    app_state["read_model"] = read_model

    # Command handlers (CQRS write side)
    cmd_handlers = CommandHandlers(app_state["event_bus"], app_state)
    cmd_handlers.set_broadcast(broadcast)
    cmd_handlers.register_all()
    app_state["command_handlers"] = cmd_handlers

    # Query handlers (CQRS read side — domain event projectors)
    qry_handlers = QueryHandlers(app_state["event_bus"], app_state, read_model)
    qry_handlers.register_all()
    app_state["query_handlers"] = qry_handlers

    # Emit startup event
    await app_state["event_bus"].publish(Event(
        type=EventType.SYSTEM_STARTUP.value,
        data={
            "version": APP_VERSION,
            "pipeline_steps": app_state["pipeline"].get_step_names(),
            "components": {k: v is not None for k, v in app_state.items()
                          if k not in ("stats", "subscribers", "latest_analysis",
                                       "latest_transcript", "latest_window",
                                       "latest_organized_screen")},
        },
        source="lifespan",
    ))

    # Start screen analysis loop (pipeline-driven)
    screen_task = asyncio.create_task(screen_analysis_loop())

    # Initialize and start STT if enabled
    stt_task = None
    if os.getenv("ENABLE_STT", "true").lower() == "true":
        try:
            create_stt = _import_stt()
            app_state["stt"] = create_stt() if create_stt else None
            if app_state["stt"]:
                stt_task = asyncio.create_task(app_state["stt"].start(on_transcript))
                logger.info("STT enabled and started")
        except Exception as e:
            logger.warning("STT initialization failed", error=str(e))

    # Start autodiagnostics
    diag_interval = float(os.getenv("DIAG_INTERVAL", "30"))
    app_state["diagnostics"] = AutoDiagnostics(app_state, interval=diag_interval)
    diag_task = asyncio.create_task(app_state["diagnostics"].run_loop(broadcast))

    logger.info("Backend fully initialized and running")

    yield

    # Shutdown
    logger.info("Shutting down backend")

    # Emit shutdown event
    bus = app_state.get("event_bus")
    if bus:
        await bus.publish(Event(
            type=EventType.SYSTEM_SHUTDOWN.value,
            data={"uptime_seconds": round(time.time() - app_state["stats"]["start_time"], 1)},
            source="lifespan",
        ))

    screen_task.cancel()
    diag_task.cancel()

    if stt_task:
        stt_task.cancel()
        if app_state["stt"]:
            await app_state["stt"].stop()

    logger.info("Backend shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="AI Desktop Assistant API",
    description="Real-time screen + voice AI assistant backend",
    version=APP_VERSION,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:*,http://127.0.0.1:*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== Register all route modules =====
from routes import register_all_routes
register_all_routes(app, app_state, broadcast)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")

    logger.info("Starting server", host=host, port=port)

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        reload=os.getenv("DEBUG", "false").lower() == "true",
    )
