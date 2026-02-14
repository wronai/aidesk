"""
Proxeen Assistant - FastAPI Backend Server
"""
import asyncio
import json
import os
import time
import sqlite3
import threading
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

from context import ContextManager
from event_bus import EventBus, Event, EventType
from typed_events import typed_event, TranscriptPayload

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
    name="proxeen",
    level=os.getenv("LOG_LEVEL", "INFO"),
    sinks=["sqlite:logs/nfo_proxeen.db", "md:logs/nfo_proxeen.md"],
    bridge_stdlib=True,
    force=True,
)

_SQLITE_CONN: Optional[sqlite3.Connection] = None
_SQLITE_LOCK = threading.Lock()
_SQLITE_PENDING_WRITES = 0
_SQLITE_FLUSH_EVERY = int(os.getenv("LOG_DB_FLUSH_EVERY", "20"))


def _get_sqlite_conn(db_file: str) -> sqlite3.Connection:
    """Get a shared SQLite connection for log writes (reused across log entries)."""
    global _SQLITE_CONN
    if _SQLITE_CONN is None:
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        _SQLITE_CONN = sqlite3.connect(db_file, check_same_thread=False)
        _SQLITE_CONN.execute("PRAGMA journal_mode=WAL")
        _SQLITE_CONN.execute("PRAGMA synchronous=NORMAL")
        _SQLITE_CONN.execute(
            "CREATE TABLE IF NOT EXISTS logs (timestamp TEXT, level TEXT, message TEXT, module TEXT)"
        )
        _SQLITE_CONN.commit()
    return _SQLITE_CONN


def _flush_and_close_sqlite_conn():
    """Flush buffered SQLite log writes and close shared connection on shutdown."""
    global _SQLITE_CONN, _SQLITE_PENDING_WRITES

    conn = _SQLITE_CONN
    if conn is None:
        return

    try:
        with _SQLITE_LOCK:
            if _SQLITE_PENDING_WRITES > 0:
                conn.commit()
                _SQLITE_PENDING_WRITES = 0
            conn.close()
    except Exception:
        # Shutdown path should be best-effort.
        pass
    finally:
        _SQLITE_CONN = None

# Configure structured logging
def setup_logging():
    global _SQLITE_PENDING_WRITES

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

    # SQLite handler (batched commits + shared connection for lower overhead)
    db_conn = _get_sqlite_conn(db_file)

    def sqlite_sink(message):
        global _SQLITE_PENDING_WRITES

        record = message.record
        try:
            with _SQLITE_LOCK:
                db_conn.execute(
                    "INSERT INTO logs VALUES (?, ?, ?, ?)",
                    (record["time"].isoformat(), record["level"].name, record["message"], record["module"]),
                )
                _SQLITE_PENDING_WRITES += 1

                if _SQLITE_PENDING_WRITES >= _SQLITE_FLUSH_EVERY or record["level"].name in ("ERROR", "CRITICAL"):
                    db_conn.commit()
                    _SQLITE_PENDING_WRITES = 0
        except Exception:
            # Logging path must never break app execution.
            pass

    loguru_logger.add(sqlite_sink, level=log_level)

    # Suppress litellm's verbose stdlib loggers — they can leak API keys
    # in request headers when LOG_LEVEL=DEBUG.
    import logging as _logging
    for _name in ("LiteLLM", "litellm", "LiteLLM Router", "LiteLLM Proxy"):
        _logging.getLogger(_name).setLevel(_logging.WARNING)

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
    Main loop — delegates to AnalysisLoop (testable tick extraction).

    All pipeline execution, state broadcasting, and context persistence
    logic lives in analysis_loop.py for testability.
    """
    from analysis_loop import AnalysisLoop
    loop = AnalysisLoop(app_state, broadcast)
    await loop.run_forever()


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
        etype = EventType.SPEECH_FINAL if is_final else EventType.TRANSCRIPT_RECEIVED
        await bus.publish(typed_event(
            etype,
            TranscriptPayload(text=text, is_final=is_final),
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
    FastAPI lifespan context manager — delegates to AppBootstrap.

    All initialization logic lives in bootstrap.py for testability.
    """
    from bootstrap import AppBootstrap

    bootstrap = AppBootstrap(app_state, broadcast, version=APP_VERSION)
    await bootstrap.startup(screen_analysis_loop, on_transcript)

    # nfo startup validation — log all initialized components
    _nfo_validate_startup(app_state)

    yield

    await bootstrap.shutdown()
    _flush_and_close_sqlite_conn()


# Create FastAPI app
app = FastAPI(
    title="Proxeen Assistant API",
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

# Serve overlay static files
overlay_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "overlay")
if os.path.exists(overlay_dir):
    app.mount("/overlay", StaticFiles(directory=overlay_dir, html=True), name="overlay")
    logger.info(f"Serving overlay at /overlay from {overlay_dir}")
else:
    logger.warning(f"Overlay directory not found at {overlay_dir}")


# ===== Register all route modules =====
from routes import register_all_routes
register_all_routes(app, app_state, broadcast)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")
    reload_enabled = os.getenv("DEBUG", "false").lower() == "true"
    app_target = "server:app" if reload_enabled else app

    logger.info("Starting server", host=host, port=port)

    uvicorn.run(
        app_target,
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        reload=reload_enabled,
    )
