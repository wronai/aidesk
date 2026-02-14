"""
OCR Engine abstraction layer with support for PaddleOCR, EasyOCR, and Tesseract.

Provides a unified interface for text extraction from screenshots,
designed as a fast pre-processor for LLM vision analysis (hybrid mode).

Each engine can be hot-swapped at runtime for A/B testing and benchmarking.
"""
import os
import time
import base64
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
import nfo
import structlog

logger = structlog.get_logger()


class OCRResult:
    """Standardized OCR result across all engines."""

    def __init__(
        self,
        text: str,
        boxes: List[Dict] = None,
        confidence: float = 0.0,
        engine: str = "unknown",
        latency_ms: float = 0.0,
        language: str = "pl",
    ):
        self.text = text
        self.boxes = boxes or []
        self.confidence = confidence
        self.engine = engine
        self.latency_ms = latency_ms
        self.language = language

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "boxes_count": len(self.boxes),
            "confidence": round(self.confidence, 4),
            "engine": self.engine,
            "latency_ms": round(self.latency_ms, 2),
            "language": self.language,
        }

    def to_llm_context(self) -> str:
        """Format OCR result as context string for LLM prompt augmentation."""
        if not self.text.strip():
            return ""
        lines = []
        lines.append(f"[OCR: {self.engine}, pewność: {self.confidence:.0%}]")
        lines.append(self.text)
        if self.boxes:
            # Add spatial layout hints for LLM
            regions = self._cluster_regions()
            if regions:
                lines.append(f"[Regiony tekstu: {', '.join(regions)}]")
        return "\n".join(lines)

    def _cluster_regions(self) -> List[str]:
        """Simple spatial clustering of text boxes into regions."""
        if not self.boxes:
            return []
        regions = []
        for box in self.boxes[:10]:  # Limit to first 10 for brevity
            text = box.get("text", "").strip()
            if text and len(text) > 2:
                pos = box.get("position", "")
                if pos:
                    regions.append(f"{pos}: '{text[:30]}'")
        return regions[:5]


class BaseOCREngine(ABC):
    """Abstract base class for OCR engines."""

    def __init__(self, languages: List[str] = None):
        self.languages = languages or ["pl", "en"]
        self.name = "base"
        self.is_initialized = False
        self.total_calls = 0
        self.total_latency_ms = 0.0

    @abstractmethod
    def _initialize(self):
        """Lazy initialization of the engine."""
        pass

    @abstractmethod
    def _extract(self, image: np.ndarray) -> OCRResult:
        """Extract text from numpy image array."""
        pass

    def extract_from_b64(self, image_b64: str) -> OCRResult:
        """
        Extract text from base64-encoded image.

        Args:
            image_b64: Base64-encoded JPEG image

        Returns:
            OCRResult with extracted text and metadata
        """
        if not self.is_initialized:
            try:
                self._initialize()
                self.is_initialized = True
                logger.info(f"OCR engine initialized", engine=self.name)
            except Exception as e:
                logger.error(f"OCR engine init failed", engine=self.name, error=str(e))
                return OCRResult(
                    text="",
                    engine=self.name,
                    latency_ms=0,
                    confidence=0.0,
                )

        start = time.time()

        try:
            # Decode base64 to PIL Image to numpy
            img_bytes = base64.b64decode(image_b64)
            img = Image.open(BytesIO(img_bytes)).convert("RGB")
            img_np = np.array(img)

            result = self._extract(img_np)
            result.latency_ms = (time.time() - start) * 1000
            result.engine = self.name

            self.total_calls += 1
            self.total_latency_ms += result.latency_ms

            logger.debug(
                "OCR extraction complete",
                engine=self.name,
                latency_ms=round(result.latency_ms, 1),
                text_length=len(result.text),
                confidence=round(result.confidence, 3),
            )

            return result

        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error("OCR extraction failed", engine=self.name, error=str(e))
            return OCRResult(
                text="",
                engine=self.name,
                latency_ms=latency,
                confidence=0.0,
            )

    def get_stats(self) -> Dict:
        return {
            "engine": self.name,
            "initialized": self.is_initialized,
            "total_calls": self.total_calls,
            "avg_latency_ms": (
                round(self.total_latency_ms / self.total_calls, 1)
                if self.total_calls > 0
                else 0
            ),
            "languages": self.languages,
        }


class PaddleOCREngine(BaseOCREngine):
    """
    PaddleOCR engine - fastest, best for UI screenshots.
    ~12.7 FPS on GPU, ~500MB VRAM, excellent multilingual support.
    """

    def __init__(self, languages: List[str] = None, use_gpu: bool = False):
        super().__init__(languages)
        self.name = "paddleocr"
        self.use_gpu = use_gpu
        self._ocr = None

    def _initialize(self):
        from paddleocr import PaddleOCR

        # Map language codes: PaddleOCR uses specific codes
        lang_map = {"pl": "latin", "en": "en", "de": "german", "fr": "french"}
        paddle_lang = lang_map.get(self.languages[0], "en")

        self._ocr = PaddleOCR(
            use_angle_cls=True,
            lang=paddle_lang,
            use_gpu=self.use_gpu,
            show_log=False,
            enable_mkldnn=True,  # CPU acceleration
        )

    def _extract(self, image: np.ndarray) -> OCRResult:
        results = self._ocr.ocr(image, cls=True)

        if not results or not results[0]:
            return OCRResult(text="", confidence=0.0)

        texts = []
        boxes = []
        total_conf = 0.0
        count = 0

        for line in results[0]:
            bbox, (text, conf) = line[0], line[1]
            texts.append(text)
            total_conf += conf
            count += 1

            # Calculate position hint
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            x_center = (bbox[0][0] + bbox[2][0]) / 2
            boxes.append({
                "text": text,
                "confidence": conf,
                "bbox": bbox,
                "position": f"({int(x_center)},{int(y_center)})",
            })

        avg_conf = total_conf / count if count > 0 else 0.0

        return OCRResult(
            text="\n".join(texts),
            boxes=boxes,
            confidence=avg_conf,
            language=self.languages[0],
        )


class EasyOCREngine(BaseOCREngine):
    """
    EasyOCR engine - high accuracy (CER 0.09), simple Python API.
    ~56 FPS, good for mixed text content.
    """

    def __init__(self, languages: List[str] = None, use_gpu: bool = False):
        super().__init__(languages)
        self.name = "easyocr"
        self.use_gpu = use_gpu
        self._reader = None

    def _initialize(self):
        import easyocr

        # EasyOCR uses ISO language codes
        lang_map = {"pl": "pl", "en": "en", "de": "de", "fr": "fr"}
        easy_langs = [lang_map.get(l, l) for l in self.languages]

        self._reader = easyocr.Reader(
            easy_langs,
            gpu=self.use_gpu,
            verbose=False,
        )

    def _extract(self, image: np.ndarray) -> OCRResult:
        results = self._reader.readtext(image)

        if not results:
            return OCRResult(text="", confidence=0.0)

        texts = []
        boxes = []
        total_conf = 0.0
        count = 0

        for bbox, text, conf in results:
            texts.append(text)
            total_conf += conf
            count += 1

            y_center = (bbox[0][1] + bbox[2][1]) / 2
            x_center = (bbox[0][0] + bbox[2][0]) / 2
            boxes.append({
                "text": text,
                "confidence": conf,
                "bbox": bbox,
                "position": f"({int(x_center)},{int(y_center)})",
            })

        avg_conf = total_conf / count if count > 0 else 0.0

        return OCRResult(
            text="\n".join(texts),
            boxes=boxes,
            confidence=avg_conf,
            language=self.languages[0],
        )


class TesseractEngine(BaseOCREngine):
    """
    Tesseract 5+ engine - lightweight (~10MB), 0.3-1s per image.
    Best as fallback for simple/clean text screenshots.
    """

    def __init__(self, languages: List[str] = None):
        super().__init__(languages)
        self.name = "tesseract"
        self._lang_str = None

    def _initialize(self):
        import pytesseract

        # Verify tesseract is available
        pytesseract.get_tesseract_version()

        # Map to tesseract language codes
        lang_map = {"pl": "pol", "en": "eng", "de": "deu", "fr": "fra"}
        self._lang_str = "+".join(lang_map.get(l, l) for l in self.languages)

    def _extract(self, image: np.ndarray) -> OCRResult:
        import pytesseract

        # Get detailed data for boxes and confidence
        data = pytesseract.image_to_data(
            image, lang=self._lang_str, output_type=pytesseract.Output.DICT
        )

        texts = []
        boxes = []
        total_conf = 0.0
        count = 0

        n_items = len(data["text"])
        for i in range(n_items):
            text = data["text"][i].strip()
            conf = float(data["conf"][i])

            if text and conf > 0:
                texts.append(text)
                total_conf += conf / 100.0  # Normalize to 0-1
                count += 1

                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]
                boxes.append({
                    "text": text,
                    "confidence": conf / 100.0,
                    "bbox": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                    "position": f"({x + w // 2},{y + h // 2})",
                })

        avg_conf = total_conf / count if count > 0 else 0.0

        # Also get plain text for cleaner output
        full_text = pytesseract.image_to_string(image, lang=self._lang_str).strip()

        return OCRResult(
            text=full_text if full_text else "\n".join(texts),
            boxes=boxes,
            confidence=avg_conf,
            language=self.languages[0],
        )


# Engine registry
ENGINES = {
    "paddleocr": PaddleOCREngine,
    "easyocr": EasyOCREngine,
    "tesseract": TesseractEngine,
    # vlm_ocr registered dynamically in OCRManager._register_available_engines()
}


class OCRManager:
    """
    Manages multiple OCR engines with hot-swapping and benchmarking.

    Supports real-time engine switching and A/B comparison testing.
    """

    def __init__(
        self,
        default_engine: str = "paddleocr",
        languages: List[str] = None,
        use_gpu: bool = False,
        enabled: bool = True,
    ):
        self.languages = languages or ["pl", "en"]
        self.use_gpu = use_gpu
        self.enabled = enabled
        self.engines: Dict[str, BaseOCREngine] = {}
        self.active_engine_name = default_engine
        self.benchmark_results: List[Dict] = []

        if enabled:
            self._register_available_engines()

        logger.info(
            "OCR manager initialized",
            default_engine=default_engine,
            available=list(self.engines.keys()),
            enabled=enabled,
        )

    def _register_available_engines(self):
        """Register engines that have their dependencies installed."""
        engine_checks = {
            "paddleocr": "paddleocr",
            "easyocr": "easyocr",
            "tesseract": "pytesseract",
        }

        for name, module in engine_checks.items():
            try:
                __import__(module)
                cls = ENGINES[name]
                if name == "tesseract":
                    self.engines[name] = cls(languages=self.languages)
                else:
                    self.engines[name] = cls(
                        languages=self.languages, use_gpu=self.use_gpu
                    )
                logger.info(f"OCR engine registered", engine=name)
            except ImportError:
                logger.debug(f"OCR engine not available (missing dependency)", engine=name)

        # VLM OCR — Cloud-based (available when litellm is installed)
        try:
            import litellm  # noqa: F401
            from vlm_ocr_engine import VLMOCREngine

            vlm_model = os.environ.get(
                "VLM_OCR_MODEL",
                "openrouter/qwen/qwen2.5-vl-32b-instruct:free",
            )
            vlm_engine = VLMOCREngine(
                model=vlm_model,
                max_tokens=int(os.environ.get("VLM_OCR_MAX_TOKENS", "1500")),
                timeout=float(os.environ.get("VLM_OCR_TIMEOUT", "15.0")),
                image_detail=os.environ.get("VLM_OCR_IMAGE_DETAIL", "low"),
                languages=self.languages,
            )
            self.engines["vlm_ocr"] = vlm_engine
            logger.info("OCR engine registered", engine="vlm_ocr", model=vlm_model)
        except ImportError:
            logger.debug("VLM OCR not available (litellm not installed)")

    @property
    def active_engine(self) -> Optional[BaseOCREngine]:
        return self.engines.get(self.active_engine_name)

    def set_engine(self, engine_name: str) -> bool:
        """
        Switch active OCR engine at runtime.

        Args:
            engine_name: Name of engine to activate

        Returns:
            True if switch was successful
        """
        if engine_name not in self.engines:
            logger.warning("OCR engine not available", engine=engine_name,
                          available=list(self.engines.keys()))
            return False

        old = self.active_engine_name
        self.active_engine_name = engine_name
        logger.info("OCR engine switched", old=old, new=engine_name)
        return True

    def extract(self, image_b64: str) -> OCRResult:
        """Extract text using active engine."""
        if not self.enabled or not self.active_engine:
            return OCRResult(text="", engine="disabled", confidence=0.0)
        return self.active_engine.extract_from_b64(image_b64)

    def benchmark(self, image_b64: str) -> Dict:
        """
        Run all available engines on the same image for comparison.

        Returns:
            Dict with results per engine and winner info
        """
        results = {}
        for name, engine in self.engines.items():
            result = engine.extract_from_b64(image_b64)
            results[name] = result.to_dict()
            results[name]["text_preview"] = result.text[:200] if result.text else ""

        # Determine winners
        if results:
            fastest = min(results.items(), key=lambda x: x[1]["latency_ms"])
            most_confident = max(results.items(), key=lambda x: x[1]["confidence"])
            most_text = max(results.items(), key=lambda x: len(x[1].get("text_preview", "")))
        else:
            fastest = most_confident = most_text = ("none", {})

        benchmark = {
            "engines": results,
            "winners": {
                "fastest": fastest[0],
                "most_confident": most_confident[0],
                "most_text": most_text[0],
            },
            "timestamp": time.time(),
        }

        self.benchmark_results.append(benchmark)
        # Keep last 20 benchmarks
        if len(self.benchmark_results) > 20:
            self.benchmark_results = self.benchmark_results[-20:]

        logger.info(
            "OCR benchmark complete",
            engines_tested=len(results),
            fastest=fastest[0],
            fastest_ms=fastest[1].get("latency_ms", 0) if isinstance(fastest[1], dict) else 0,
        )

        return benchmark

    def get_available_engines(self) -> List[Dict]:
        """Get list of available engines with status."""
        available = []
        for name, engine in self.engines.items():
            info = engine.get_stats()
            info["active"] = name == self.active_engine_name
            available.append(info)
        return available

    def get_stats(self) -> Dict:
        return {
            "enabled": self.enabled,
            "active_engine": self.active_engine_name,
            "available_engines": list(self.engines.keys()),
            "languages": self.languages,
            "use_gpu": self.use_gpu,
            "engines": {
                name: engine.get_stats() for name, engine in self.engines.items()
            },
            "benchmarks_count": len(self.benchmark_results),
        }


@nfo.log_call(level="INFO")
def create_ocr_manager_from_env(settings=None) -> OCRManager:
    """Create OCRManager from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return OCRManager(
        default_engine=settings.ocr_engine,
        languages=settings.ocr_languages_list,
        use_gpu=settings.ocr_use_gpu,
        enabled=settings.enable_ocr,
    )
