"""OCR routes: /ocr/*, /mode/*."""
import nfo
import base64
from io import BytesIO
from typing import Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_state: Dict = {}
_broadcast = None


def init(app_state: dict, broadcast_fn):
    global _state, _broadcast
    _state = app_state
    _broadcast = broadcast_fn


@router.get("/ocr/engines")
async def ocr_engines():
    """List available OCR engines and their status."""
    if not _state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})
    return {
        "engines": _state["ocr_manager"].get_available_engines(),
        "active": _state["ocr_manager"].active_engine_name,
    }


@router.post("/ocr/engine/{engine_name}")
async def set_ocr_engine(engine_name: str):
    """Switch active OCR engine at runtime."""
    if not _state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})

    success = _state["ocr_manager"].set_engine(engine_name)
    if not success:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Engine '{engine_name}' not available",
                "available": list(_state["ocr_manager"].engines.keys()),
            },
        )

    await _broadcast("ocr_engine_changed", {
        "engine": engine_name,
        "available": list(_state["ocr_manager"].engines.keys()),
    })
    return {"engine": engine_name, "status": "active"}


@router.post("/ocr/benchmark")
async def ocr_benchmark():
    """Run benchmark: all OCR engines on current screen capture."""
    if not _state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})
    if not _state["capture"]:
        return JSONResponse(status_code=503, content={"error": "Capture not initialized"})

    from PIL import Image, ImageDraw
    import structlog
    logger = structlog.get_logger()

    image_b64 = None
    capture = _state["capture"]

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
        img = Image.new("RGB", (capture.screen_width, capture.screen_height), color=(30, 30, 35))
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

    result = _state["ocr_manager"].benchmark(image_b64)
    await _broadcast("ocr_benchmark", result)
    return result


@router.get("/ocr/stats")
async def ocr_stats():
    """Get OCR engine statistics."""
    if not _state["ocr_manager"]:
        return JSONResponse(status_code=404, content={"error": "OCR not initialized"})
    return _state["ocr_manager"].get_stats()


@router.get("/ocr/post-process/stats")
async def ocr_post_process_stats():
    """Get OCR post-processing statistics."""
    enh = _state.get("ocr_enhancer")
    if not enh:
        return JSONResponse(status_code=503, content={"error": "OCR enhancer not initialized"})
    return enh.get_stats()


@router.get("/mode")
async def get_analysis_mode():
    """Get current analysis mode."""
    analyzer = _state["analyzer"]
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


@router.post("/mode/{mode_name}")
async def set_analysis_mode(mode_name: str):
    """Switch analysis mode at runtime."""
    analyzer = _state["analyzer"]
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

    await _broadcast("mode_changed", {"mode": mode_name})
    return {"mode": mode_name, "status": "active"}
