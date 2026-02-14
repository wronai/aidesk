"""
AI Desktop Assistant - FastAPI Backend Server
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from capture import create_capture_from_env
from analyzer import create_analyzer_from_env
from stt import create_stt_from_env
from context import ContextManager

# Load environment variables
load_dotenv()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

# Global application state
app_state = {
    "latest_analysis": "",
    "latest_transcript": "",
    "context": ContextManager(max_items=int(os.getenv("MAX_CONTEXT_ITEMS", "20"))),
    "subscribers": [],
    "capture": None,
    "analyzer": None,
    "stt": None,
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
    Main loop: capture → detect change → analyze → broadcast.
    """
    capture = app_state["capture"]
    analyzer = app_state["analyzer"]
    context_mgr = app_state["context"]

    logger.info("Screen analysis loop started")

    while True:
        try:
            # Capture screen (returns None if no change)
            result = capture.capture()

            if result:
                # Get recent context
                context_str = context_mgr.get_context_string(n=5, max_length=500)

                # Analyze screen
                analysis = await analyzer.analyze(result["image_b64"], context_str)

                # Store in state
                app_state["latest_analysis"] = analysis["text"]
                app_state["stats"]["total_screen_analyses"] += 1

                # Add to context
                context_mgr.add(
                    content=analysis["text"][:200],
                    context_type="screen",
                    metadata={
                        "tokens": analysis.get("tokens", 0),
                        "cost": analysis.get("cost", 0.0),
                        "provider": analysis.get("provider", "unknown"),
                    },
                )

                # Broadcast to overlay
                await broadcast(
                    "analysis",
                    {
                        "text": analysis["text"],
                        "timestamp": result["timestamp"],
                        "size_kb": result["size_kb"],
                        "tokens": analysis.get("tokens", 0),
                        "cost": round(analysis.get("cost", 0.0), 6),
                        "provider": analysis.get("provider", "unknown"),
                    },
                )

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup/shutdown.
    """
    # Startup
    logger.info("Starting AI Desktop Assistant backend")

    # Initialize components
    app_state["capture"] = create_capture_from_env()
    app_state["analyzer"] = create_analyzer_from_env()

    # Start screen analysis loop
    screen_task = asyncio.create_task(screen_analysis_loop())

    # Initialize and start STT if enabled
    stt_task = None
    if os.getenv("ENABLE_STT", "true").lower() == "true":
        try:
            app_state["stt"] = create_stt_from_env()
            if app_state["stt"]:
                stt_task = asyncio.create_task(app_state["stt"].start(on_transcript))
                logger.info("STT enabled and started")
        except Exception as e:
            logger.warning("STT initialization failed", error=str(e))

    logger.info("Backend fully initialized and running")

    yield

    # Shutdown
    logger.info("Shutting down backend")
    screen_task.cancel()

    if stt_task:
        stt_task.cancel()
        if app_state["stt"]:
            await app_state["stt"].stop()

    logger.info("Backend shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="AI Desktop Assistant API",
    description="Real-time screen + voice AI assistant backend",
    version="1.0.0",
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
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "stream": "/stream - SSE endpoint for real-time updates",
            "status": "/status - Current status and latest data",
            "stats": "/stats - Detailed statistics",
            "health": "/health - Health check",
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

    if app_state["stt"]:
        stats_data["stt"] = app_state["stt"].get_stats()

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
                "stt": app_state["stt"] is not None,
            },
        },
    )


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
