"""
AI Desktop Assistant - FastAPI Backend Server
"""
import asyncio
import json
import os
import time
import sqlite3
from contextlib import asynccontextmanager
from typing import Dict, List

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
from pipeline import PipelineOrchestrator, PipelineContext, create_pipeline
from command_handlers import CommandHandlers
from query_handlers import QueryHandlers, ReadModel

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
    "read_model": None,
    "command_handlers": None,
    "query_handlers": None,
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

    Pipeline order (composable steps, each emits events to EventBus):
    1. ScanWindows        → scan all visible windows with process info
    2. DetectActiveWindow  → detect active window, build window context, ROI
    3. CaptureScreen       → capture fullscreen or ROI screenshot
    4. CropWindows         → crop each visible app from fullscreen
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

    logger.info(
        "Screen analysis loop started (pipeline-based)",
        steps=pipeline.get_step_names(),
        total_steps=len(pipeline.steps),
    )

    while True:
        try:
            # Create fresh context for this pipeline run
            ctx = PipelineContext()

            # Execute all pipeline steps (skip if can_run() is False)
            ctx = await pipeline.run(ctx)

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
    )

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

    # Start screen analysis loop
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


@app.get("/")
async def root():
    """Root endpoint."""
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
            "window_latest": "/window/latest - Cached window info (GET)",
            "monitors": "/monitors - Connected monitors (GET)",
            "profiles": "/profiles - Per-app analysis profiles (GET)",
            "agent_actions": "/agent/actions - Pending agent actions (GET)",
            "agent_approve": "/agent/approve/{id} - Approve action (POST)",
            "agent_execute": "/agent/execute/{id} - Execute action (POST)",
            "agent_run": "/agent/run - Run safe command (POST)",
            "agent_history": "/agent/history - Action history (GET)",
            "processes": "/processes - All visible windows with process info (GET)",
            "windows_all": "/windows/all - All visible windows with geometry (GET)",
            "screen_organized": "/screen/organized - Latest organized screen data (GET)",
            "screen_stats": "/screen/stats - Process scanner & cropper stats (GET)",
            "nfo_validation": "/nfo/validation - nfo startup validation (GET)",
        },
    }


@app.get("/stream")
async def sse_stream(request: Request):
    """
    Server-Sent Events endpoint for real-time updates.

    This is the main connection point for the overlay UI.
    """
    queue = asyncio.Queue(maxsize=100)
    app_state["subscribers"].append(queue)

    async def event_generator():
        try:
            # Send initial state
            yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"

            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Wait for message or timeout
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield "event: heartbeat\ndata: {}\n\n"

        except Exception as e:
            logger.error("SSE stream error", error=str(e))
        finally:
            # Clean up subscriber
            if queue in app_state["subscribers"]:
                app_state["subscribers"].remove(queue)
            logger.info(
                "SSE client disconnected",
                active_subscribers=len(app_state["subscribers"]),
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/status")
async def status():
    """Get current status and latest data."""
    return {
        "latest_analysis": app_state["latest_analysis"],
        "latest_transcript": app_state["latest_transcript"],
        "active_subscribers": len(app_state["subscribers"]),
        "context_items": len(app_state["context"].history),
    }


@app.get("/stats")
async def stats():
    """Get detailed statistics."""
    uptime = time.time() - app_state["stats"]["start_time"]

    stats_data = {
        "uptime_seconds": round(uptime),
        "uptime_formatted": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m",
        "total_screen_analyses": app_state["stats"]["total_screen_analyses"],
        "total_transcripts": app_state["stats"]["total_transcripts"],
        "total_errors": app_state["stats"]["total_errors"],
        "active_subscribers": len(app_state["subscribers"]),
    }

    # Add component stats
    if app_state["capture"]:
        stats_data["capture"] = app_state["capture"].get_stats()

    if app_state["analyzer"]:
        stats_data["analyzer"] = app_state["analyzer"].get_stats()

    if app_state["ocr_manager"]:
        stats_data["ocr"] = app_state["ocr_manager"].get_stats()

    if app_state["stt"]:
        stats_data["stt"] = app_state["stt"].get_stats()

    if app_state["window_manager"]:
        stats_data["window_manager"] = app_state["window_manager"].get_stats()

    if app_state["profile_manager"]:
        stats_data["profile_manager"] = app_state["profile_manager"].get_stats()

    if app_state["shell_agent"]:
        stats_data["shell_agent"] = app_state["shell_agent"].get_stats()

    if app_state.get("process_scanner"):
        stats_data["process_scanner"] = app_state["process_scanner"].get_stats()

    if app_state.get("window_cropper"):
        stats_data["window_cropper"] = app_state["window_cropper"].get_stats()

    stats_data["context"] = app_state["context"].get_stats()

    return stats_data


@app.get("/health")
async def health():
    """Health check endpoint."""
    is_healthy = (
        app_state["capture"] is not None and app_state["analyzer"] is not None
    )

    return JSONResponse(
        status_code=200 if is_healthy else 503,
        content={
            "status": "healthy" if is_healthy else "unhealthy",
            "components": {
                "capture": app_state["capture"] is not None,
                "analyzer": app_state["analyzer"] is not None,
                "ocr": app_state["ocr_manager"] is not None,
                "stt": app_state["stt"] is not None,
                "window_manager": app_state["window_manager"] is not None,
                "profile_manager": app_state["profile_manager"] is not None,
                "shell_agent": app_state["shell_agent"] is not None,
                "process_scanner": app_state["process_scanner"] is not None,
                "window_cropper": app_state["window_cropper"] is not None,
            },
        },
    )


# ===== OCR Management Endpoints =====

@app.get("/ocr/engines")
async def ocr_engines():
    """List available OCR engines and their status."""
    if not app_state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})
    return {
        "engines": app_state["ocr_manager"].get_available_engines(),
        "active": app_state["ocr_manager"].active_engine_name,
    }


@app.post("/ocr/engine/{engine_name}")
async def set_ocr_engine(engine_name: str):
    """Switch active OCR engine at runtime."""
    if not app_state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})

    success = app_state["ocr_manager"].set_engine(engine_name)
    if not success:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Engine '{engine_name}' not available",
                "available": list(app_state["ocr_manager"].engines.keys()),
            },
        )

    await broadcast("ocr_engine_changed", {
        "engine": engine_name,
        "available": list(app_state["ocr_manager"].engines.keys()),
    })

    return {"engine": engine_name, "status": "active"}


@app.post("/ocr/benchmark")
async def ocr_benchmark():
    """
    Run benchmark: all OCR engines on current screen capture.
    Returns comparative results for A/B testing.
    """
    if not app_state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})
    if not app_state["capture"]:
        return JSONResponse(status_code=503, content={"error": "Capture not initialized"})

    from PIL import Image
    from io import BytesIO
    import base64

    image_b64 = None
    capture = app_state["capture"]

    # Try to grab a fresh screenshot
    try:
        sct = capture.sct
        monitor = sct.monitors[1]
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        img_resized = img.resize(
            (capture.screen_width, capture.screen_height), Image.Resampling.LANCZOS
        )
        buffer = BytesIO()
        img_resized.save(buffer, format="JPEG", quality=capture.jpeg_quality, optimize=True)
        image_b64 = base64.b64encode(buffer.getvalue()).decode()
    except Exception as e:
        logger.warning("Benchmark: screen capture failed, using test image", error=str(e))
        # Fallback: generate a test image with sample text for benchmarking
        img = Image.new("RGB", (capture.screen_width, capture.screen_height), color=(30, 30, 35))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        sample_texts = [
            (50, 30, "AI Desktop Assistant - Benchmark Test"),
            (50, 80, "Analiza zrzutu ekranu w trybie testowym"),
            (50, 130, "def analyze(image): return ocr.extract(image)"),
            (50, 180, "Status: Connected | Mode: hybrid | OCR: active"),
            (50, 230, "Polskie znaki: ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ"),
        ]
        for x, y, text in sample_texts:
            draw.text((x, y), text, fill=(220, 220, 220))
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=capture.jpeg_quality, optimize=True)
        image_b64 = base64.b64encode(buffer.getvalue()).decode()

    # Run benchmark
    result = app_state["ocr_manager"].benchmark(image_b64)

    await broadcast("ocr_benchmark", result)

    return result


@app.get("/ocr/stats")
async def ocr_stats():
    """Get OCR engine statistics."""
    if not app_state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})
    return app_state["ocr_manager"].get_stats()


# ===== Analysis Mode Endpoints =====

@app.get("/mode")
async def get_analysis_mode():
    """Get current analysis mode."""
    analyzer = app_state["analyzer"]
    if not analyzer:
        return JSONResponse(status_code=503, content={"error": "Analyzer not initialized"})
    return {
        "mode": analyzer.analysis_mode,
        "available_modes": [
            {"id": "vision_only", "name": "Vision Only", "desc": "Pure VLM – obraz → LLM"},
            {"id": "ocr_only", "name": "OCR Only", "desc": "Tylko OCR – najszybszy, bez LLM"},
            {"id": "hybrid", "name": "Hybrid (OCR→LLM)", "desc": "OCR tekst → LLM (rekomendowany)"},
            {"id": "ocr_plus_vision", "name": "OCR + Vision", "desc": "OCR tekst + obraz → VLM (najdokładniejszy)"},
        ],
    }


@app.post("/mode/{mode_name}")
async def set_analysis_mode(mode_name: str):
    """Switch analysis mode at runtime."""
    analyzer = app_state["analyzer"]
    if not analyzer:
        return JSONResponse(status_code=503, content={"error": "Analyzer not initialized"})

    success = analyzer.set_mode(mode_name)
    if not success:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Invalid mode '{mode_name}'",
                "valid_modes": ["vision_only", "ocr_only", "hybrid", "ocr_plus_vision"],
            },
        )

    await broadcast("mode_changed", {"mode": mode_name})

    return {"mode": mode_name, "status": "active"}


# ===== Diagnostics Endpoints =====

@app.get("/diagnostics")
async def diagnostics():
    """Get latest autodiagnostics result."""
    diag = app_state.get("diagnostics")
    if not diag or not diag.get_latest():
        return JSONResponse(status_code=503, content={"error": "Diagnostics not yet available", "detail": "First check runs after configured interval"})
    return diag.get_latest()


@app.get("/diagnostics/history")
async def diagnostics_history():
    """Get diagnostics history."""
    diag = app_state.get("diagnostics")
    if not diag:
        return []
    return diag.get_history()


# ===== Window Awareness Endpoints =====

@app.get("/window")
async def get_active_window():
    """Get current active window information."""
    wm = app_state.get("window_manager")
    if not wm:
        return JSONResponse(status_code=503, content={"error": "Window awareness not enabled"})
    info = wm.get_active_window()
    return info.to_dict()


@app.get("/window/latest")
async def get_latest_window():
    """Get latest cached window info (no subprocess call)."""
    latest = app_state.get("latest_window")
    if not latest:
        return JSONResponse(status_code=404, content={"error": "No window data yet"})
    return latest


@app.get("/monitors")
async def get_monitors():
    """Get list of connected monitors."""
    wm = app_state.get("window_manager")
    capture = app_state.get("capture")

    result = {"monitors": []}

    if wm:
        result["monitors"] = [m.to_dict() for m in wm.get_monitors()]
        result["source"] = "xrandr"
    elif capture:
        result["monitors"] = capture.get_monitors()
        result["source"] = "mss"

    return result


@app.get("/window/stats")
async def window_stats():
    """Get window manager statistics."""
    wm = app_state.get("window_manager")
    if not wm:
        return JSONResponse(status_code=503, content={"error": "Window awareness not enabled"})
    return wm.get_stats()


# ===== Process Scanner & Window Cropper Endpoints =====

@app.get("/processes")
async def get_all_processes():
    """
    Scan all visible windows with process info.
    Returns organized layout grouped by category.
    """
    scanner = app_state.get("process_scanner")
    if not scanner:
        return JSONResponse(status_code=503, content={"error": "Process scanner not initialized"})
    return scanner.get_window_layout()


@app.get("/windows/all")
async def get_all_windows():
    """Get all visible windows with geometry and process details."""
    scanner = app_state.get("process_scanner")
    if not scanner:
        return JSONResponse(status_code=503, content={"error": "Process scanner not initialized"})
    windows = scanner.scan_all_windows()
    return {
        "total": len(windows),
        "windows": [w.to_dict() for w in windows],
    }


@app.get("/screen/organized")
async def get_organized_screen():
    """Get latest organized screen data (per-app crops + categories)."""
    data = app_state.get("latest_organized_screen")
    if not data:
        return JSONResponse(status_code=404, content={"error": "No organized screen data yet"})
    return data


@app.get("/screen/stats")
async def get_screen_stats():
    """Get process scanner and window cropper statistics."""
    result = {}
    scanner = app_state.get("process_scanner")
    if scanner:
        result["process_scanner"] = scanner.get_stats()
    cropper = app_state.get("window_cropper")
    if cropper:
        result["window_cropper"] = cropper.get_stats()
    return result


@app.get("/nfo/validation")
async def get_nfo_validation():
    """Get latest nfo startup validation result."""
    return _nfo_validate_startup(app_state)


# ===== App Profiles Endpoints =====

@app.get("/profiles")
async def get_profiles():
    """Get all available per-app analysis profiles."""
    pm = app_state.get("profile_manager")
    if not pm:
        return JSONResponse(status_code=503, content={"error": "Profile manager not initialized"})
    return {
        "profiles": pm.get_all_profiles(),
        "active": pm.active_category.value if pm.active_category else None,
        "stats": pm.get_stats(),
    }


# ===== Shell Agent Endpoints =====

@app.get("/agent/actions")
async def get_pending_actions():
    """Get pending agent actions awaiting approval."""
    agent = app_state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})
    return {
        "pending": agent.get_pending_actions(),
        "stats": agent.get_stats(),
    }


@app.post("/agent/approve/{action_id}")
async def approve_action(action_id: str):
    """Approve a pending agent action."""
    agent = app_state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})

    success = agent.approve_action(action_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": f"Action not found: {action_id}"})

    return {"action_id": action_id, "status": "approved"}


@app.post("/agent/execute/{action_id}")
async def execute_action(action_id: str):
    """Execute an approved agent action."""
    agent = app_state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})

    try:
        # Get CWD from latest window if available
        cwd = None
        latest_window = app_state.get("latest_window")
        if latest_window and latest_window.get("cwd"):
            cwd = latest_window["cwd"]

        result = agent.execute_action(action_id, cwd=cwd)
        await broadcast("agent_result", result.to_dict())
        return result.to_dict()
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@app.get("/agent/history")
async def agent_history():
    """Get agent action execution history."""
    agent = app_state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})
    return {
        "history": agent.get_history(n=50),
        "stats": agent.get_stats(),
    }


@app.post("/agent/run")
async def agent_run_safe(request: Request):
    """
    Run a safe (read-only) command directly.
    Body: {"command": "git status", "cwd": "/optional/path"}
    """
    agent = app_state.get("shell_agent")
    if not agent:
        return JSONResponse(status_code=503, content={"error": "Shell agent not enabled"})

    body = await request.json()
    command = body.get("command", "").strip()
    cwd = body.get("cwd")

    if not command:
        return JSONResponse(status_code=400, content={"error": "Missing 'command' field"})

    result = agent.execute_safe(command, cwd=cwd)
    return result.to_dict()


# ===== Screenshot Browser Endpoints =====

@app.get("/screenshots")
async def list_screenshots():
    """List all saved screenshots."""
    captures_dir = os.getenv("CAPTURES_DIR", "/tmp/aidesk_captures")
    if not os.path.exists(captures_dir):
        return []
    
    files = []
    for f in os.listdir(captures_dir):
        if f.endswith(".jpg"):
            path = os.path.join(captures_dir, f)
            stats = os.stat(path)
            files.append({
                "name": f,
                "timestamp": stats.st_mtime,
                "size": stats.st_size,
                "url": f"/screenshots/{f}"
            })
    
    # Sort by timestamp descending
    files.sort(key=lambda x: x["timestamp"], reverse=True)
    return files


@app.get("/screenshots/{filename}")
async def get_screenshot(filename: str):
    """Serve a specific screenshot file."""
    captures_dir = os.getenv("CAPTURES_DIR", "/tmp/aidesk_captures")
    file_path = os.path.join(captures_dir, filename)
    
    # Security check: prevent directory traversal
    if not os.path.abspath(file_path).startswith(os.path.abspath(captures_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(file_path)


@app.get("/browser", response_class=FileResponse)
async def screenshot_browser():
    """Serve the screenshot browser UI."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "screenshots.html"))


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
