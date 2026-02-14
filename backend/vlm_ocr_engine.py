"""
VLM OCR Engine — Cloud-based OCR via Vision Language Models (OpenRouter / LiteLLM).

Sends screenshot to a fast vision model (e.g. Qwen2.5-VL, Gemini Flash)
and gets extracted text back. No local OCR dependencies needed.

Architecture:
  BaseOCREngine (ABC)
  ├── PaddleOCREngine   ← local
  ├── EasyOCREngine     ← local
  ├── TesseractEngine   ← local
  └── VLMOCREngine      ← NEW: cloud via LiteLLM/OpenRouter
"""
import os
import time
from typing import Dict, List, Optional

import nfo
import structlog

from ocr_engines import BaseOCREngine, OCRResult

logger = structlog.get_logger()

# --- OCR-optimized system prompts ---

VLM_OCR_SYSTEM_PROMPT = """You are a precise OCR engine. Extract ALL visible text from the screenshot.

Rules:
- Output ONLY the extracted text, nothing else
- Preserve original layout: line breaks, indentation, columns
- Preserve special characters: →, ←, ●, ■, etc.
- For UI elements: include labels, buttons, menus, tooltips, status bars
- For code/terminal: preserve exact formatting, indentation, syntax
- For mixed content: separate regions with blank lines
- If no text visible: output exactly "NO_TEXT_DETECTED"
- Do NOT add descriptions, explanations, or commentary
- Do NOT wrap in markdown code blocks
- Language: preserve original language (do not translate)"""

VLM_OCR_SYSTEM_PROMPT_SHORT = """OCR engine. Extract ALL visible text from screenshot.
Output ONLY raw text. Preserve layout, line breaks, indentation.
UI labels, buttons, menus — include all. Code/terminal — exact formatting.
No text? Output: NO_TEXT_DETECTED. No descriptions or markdown."""


class VLMOCREngine(BaseOCREngine):
    """
    Cloud-based OCR engine using Vision Language Models via LiteLLM.

    Instead of processing images locally (like PaddleOCR), sends them to
    a vision model (e.g. Qwen2.5-VL on OpenRouter) with an OCR prompt.

    Cost vs. accuracy:
    ┌──────────────────────┬──────────┬────────────┬──────────────┐
    │ Model                │ Cost     │ Latency    │ Accuracy     │
    ├──────────────────────┼──────────┼────────────┼──────────────┤
    │ Qwen2.5-VL 72B :free│ $0       │ 1-3s       │ ★★★★★        │
    │ Qwen2.5-VL 32B :free│ $0       │ 0.8-2s     │ ★★★★☆        │
    │ Gemini 2.0 Flash     │ ~$0.0001 │ 0.3-0.8s   │ ★★★★★        │
    │ Llama 3.2 11B :free  │ $0       │ 0.5-1.5s   │ ★★★☆☆        │
    └──────────────────────┴──────────┴────────────┴──────────────┘
    """

    ENGINE_NAME = "vlm_ocr"
    DISPLAY_NAME = "VLM OCR (Cloud)"

    def __init__(
        self,
        model: str = "openrouter/qwen/qwen2.5-vl-32b-instruct:free",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        max_tokens: int = 1500,
        temperature: float = 0.1,
        image_detail: str = "low",
        timeout: float = 15.0,
        short_prompt: bool = True,
        languages: Optional[List[str]] = None,
    ):
        super().__init__(languages)
        self.name = self.ENGINE_NAME
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.image_detail = image_detail
        self.timeout = timeout
        self.short_prompt = short_prompt

        # VLM-specific stats
        self._error_count = 0
        self._total_tokens_used = 0
        self._litellm = None

    def _initialize(self):
        """Lazy init — verify litellm is available and resolve API key."""
        try:
            import litellm
            self._litellm = litellm
        except ImportError:
            raise RuntimeError("litellm not installed. Run: pip install litellm")

        # Resolve API key from env if not provided
        if not self.api_key:
            if "openrouter" in self.model:
                self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
            elif "gemini" in self.model:
                self.api_key = os.environ.get("GEMINI_API_KEY", "")
            elif "openai" in self.model or "gpt" in self.model:
                self.api_key = os.environ.get("OPENAI_API_KEY", "")

        logger.info("VLM OCR engine initialized", model=self.model)

    def _extract(self, image):
        """Not used — VLMOCREngine overrides extract_from_b64 directly."""
        raise NotImplementedError("VLMOCREngine uses extract_from_b64, not _extract")

    def extract_from_b64(self, image_b64: str) -> OCRResult:
        """
        Extract text from base64-encoded image via VLM API call.

        Overrides BaseOCREngine.extract_from_b64 to skip numpy conversion
        and send base64 directly to the vision model.
        """
        if not self.is_initialized:
            try:
                self._initialize()
                self.is_initialized = True
            except Exception as e:
                logger.error("VLM OCR init failed", error=str(e))
                return OCRResult(text="", engine=self.name, confidence=0.0)

        start = time.time()

        try:
            system_prompt = (
                VLM_OCR_SYSTEM_PROMPT_SHORT if self.short_prompt
                else VLM_OCR_SYSTEM_PROMPT
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": self.image_detail,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all text:",
                        },
                    ],
                },
            ]

            kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "timeout": self.timeout,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            # Synchronous LiteLLM call
            response = self._litellm.completion(**kwargs)

            latency_ms = (time.time() - start) * 1000
            text = response.choices[0].message.content or ""

            if "NO_TEXT_DETECTED" in text:
                text = ""

            text = self._clean_response(text)

            # Track token usage
            usage = getattr(response, "usage", None)
            tokens_used = getattr(usage, "total_tokens", 0) if usage else 0
            self._total_tokens_used += tokens_used

            self.total_calls += 1
            self.total_latency_ms += latency_ms

            logger.debug(
                "VLM OCR extraction complete",
                engine=self.name,
                model=self.model,
                latency_ms=round(latency_ms, 1),
                text_length=len(text.strip()),
                tokens=tokens_used,
            )

            return OCRResult(
                text=text.strip(),
                boxes=[],  # VLM does not return bounding boxes
                confidence=0.85 if text.strip() else 0.0,
                engine=self.name,
                latency_ms=latency_ms,
                language=self.languages[0] if self.languages else "pl",
            )

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            self._error_count += 1
            logger.error(
                "VLM OCR extraction failed",
                engine=self.name,
                error=str(e),
                latency_ms=round(latency_ms, 1),
            )
            return OCRResult(
                text="",
                boxes=[],
                confidence=0.0,
                engine=self.name,
                latency_ms=latency_ms,
            )

    def _clean_response(self, text: str) -> str:
        """Remove typical VLM response artifacts."""
        # Strip markdown code blocks
        if text.startswith("```") and text.endswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        elif text.startswith("```"):
            text = text.lstrip("`").lstrip()

        # Strip common VLM prefixes
        prefixes_to_strip = [
            "Here is the extracted text:",
            "Extracted text:",
            "The text in the image:",
            "Text from the screenshot:",
            "OCR Result:",
        ]
        for prefix in prefixes_to_strip:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()

        return text

    def get_stats(self) -> Dict:
        """Engine stats — compatible with BaseOCREngine.get_stats()."""
        base = super().get_stats()
        base.update({
            "display_name": self.DISPLAY_NAME,
            "model": self.model,
            "errors": self._error_count,
            "total_tokens": self._total_tokens_used,
            "image_detail": self.image_detail,
        })
        return base
