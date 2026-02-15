"""
Vision AI analyzer using LiteLLM as unified provider gateway.

Supports local (Ollama, LM Studio, vLLM, llama.cpp) and remote
(Gemini, OpenAI, Claude, Groq, Mistral, DeepSeek) providers through
a single standardized interface.

Hybrid mode: OCR pre-processing (PaddleOCR/EasyOCR/Tesseract) feeds
extracted text into VLM prompts, reducing inference time by 5-10x
and improving accuracy on text-heavy screenshots.

See PROVIDERS.md for full configuration reference.
"""
import os
import time
import asyncio
from typing import Optional, Dict, Literal
import nfo
import structlog
try:
    import litellm
    from litellm import acompletion
    _LITELLM_AVAILABLE = True
except Exception:  # pragma: no cover - env-dependent import
    litellm = None
    _LITELLM_AVAILABLE = False

    async def acompletion(*args, **kwargs):
        raise RuntimeError(
            "litellm is not installed. Install dependency: pip install litellm"
        )

from ocr_engines import OCRManager, OCRResult
from typing import Protocol as TypingProtocol

logger = structlog.get_logger()


# ===== Analysis Strategy Protocol =====

class AnalysisStrategy(TypingProtocol):
    """Strategy interface for analysis modes (Strategy Pattern)."""
    def build_messages(self, prompt: str, image_b64: str, image_detail: str) -> list: ...
    def needs_ocr(self) -> bool: ...
    def needs_llm(self) -> bool: ...


class VisionOnlyStrategy:
    """Pure VLM: send image to vision model, no OCR."""
    def needs_ocr(self) -> bool:
        return False

    def needs_llm(self) -> bool:
        return True

    def build_messages(self, prompt: str, image_b64: str, image_detail: str) -> list:
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}",
                    "detail": image_detail,
                }},
            ],
        }]


class OCROnlyStrategy:
    """Pure OCR: extract text only, no LLM call."""
    def needs_ocr(self) -> bool:
        return True

    def needs_llm(self) -> bool:
        return False

    def build_messages(self, prompt: str, image_b64: str, image_detail: str) -> list:
        return []  # No LLM messages needed


class HybridStrategy:
    """OCR text → LLM text prompt (no image sent, 5-10x faster)."""
    def needs_ocr(self) -> bool:
        return True

    def needs_llm(self) -> bool:
        return True

    def build_messages(self, prompt: str, image_b64: str, image_detail: str) -> list:
        return [{"role": "user", "content": prompt}]


class OCRPlusVisionStrategy:
    """OCR text + image → VLM (most accurate, most expensive)."""
    def needs_ocr(self) -> bool:
        return True

    def needs_llm(self) -> bool:
        return True

    def build_messages(self, prompt: str, image_b64: str, image_detail: str) -> list:
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}",
                    "detail": image_detail,
                }},
            ],
        }]


# Strategy registry
_STRATEGIES = {
    "vision_only": VisionOnlyStrategy(),
    "ocr_only": OCROnlyStrategy(),
    "hybrid": HybridStrategy(),
    "ocr_plus_vision": OCRPlusVisionStrategy(),
}

# Suppress LiteLLM's verbose logging by default
if _LITELLM_AVAILABLE:
    litellm.suppress_debug_info = True


class TokenBucketLimiter:
    """Token bucket rate limiter."""

    def __init__(self, max_tokens: int = 5, refill_rate: float = 1.0):
        self.max_tokens = max_tokens
        self.tokens = max_tokens
        self.refill_rate = refill_rate
        self.last_refill = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Acquire a token, waiting if necessary."""
        async with self.lock:
            while True:
                now = time.time()
                elapsed = now - self.last_refill
                self.tokens = min(
                    self.max_tokens, self.tokens + elapsed * self.refill_rate
                )
                self.last_refill = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    return True

                await asyncio.sleep(0.1)


class ScreenAnalyzer:
    """
    Unified vision AI analyzer using LiteLLM.

    LiteLLM routes requests to 100+ LLM providers using model name prefixes:
      - "ollama/llava"            → local Ollama
      - "gemini/gemini-2.0-flash" → Google Gemini
      - "gpt-4o-mini"             → OpenAI
      - "anthropic/claude-sonnet-4-20250514" → Anthropic
      - "groq/llava-v1.5-7b-4096-preview"   → Groq
      - "deepseek/deepseek-chat"  → DeepSeek

    For OpenAI-compatible local servers (LM Studio, vLLM, llama.cpp),
    set LITELLM_API_BASE to the server URL.
    """

    SYSTEM_PROMPT = """Jesteś real-time desktop assistant analizujący zrzuty ekranu użytkownika.

Dla każdego zrzutu ekranu:
1. Zidentyfikuj aktywną aplikację i bieżące zadanie użytkownika
2. Podaj zwięzłe, praktyczne sugestie związane z tym, co użytkownik robi
3. Jeśli widoczny jest kod, zasugeruj ulepszenia lub wykryj błędy
4. Jeśli widoczne jest spotkanie/prezentacja, podsumuj kluczowe punkty
5. Zwróć uwagę na błędy, ostrzeżenia lub elementy wymagające uwagi

Odpowiadaj BARDZO zwięźle (max 100 słów). Skup się na tym, co NOWE lub PRAKTYCZNE.
Mów po polsku, używaj emoji dla lepszej czytelności.

Zwróć odpowiedź jako JSON:
{
  "app": "nazwa aplikacji",
  "task": "co użytkownik robi",
  "suggestions": ["sugestia 1", "sugestia 2"],
  "priority": "low" | "medium" | "high",
  "summary": "krótkie podsumowanie jednym zdaniem"
}"""

    # Analysis modes
    MODE_VISION_ONLY = "vision_only"       # Pure VLM (original)
    MODE_OCR_ONLY = "ocr_only"             # Pure OCR (no LLM)
    MODE_HYBRID = "hybrid"                 # OCR pre-process → LLM (recommended)
    MODE_OCR_PLUS_VISION = "ocr_plus_vision"  # OCR text + image → VLM

    def __init__(
        self,
        model: str = "ollama/llava",
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: int = 400,
        temperature: float = 0.3,
        image_detail: str = "low",
        ocr_manager: Optional[OCRManager] = None,
        analysis_mode: str = "hybrid",
    ):
        """
        Initialize analyzer with LiteLLM.

        Args:
            model: LiteLLM model identifier (e.g. "ollama/llava", "gemini/gemini-2.0-flash")
            api_base: Custom API base URL for local servers (LM Studio, vLLM, etc.)
            api_key: API key override (if not using env vars)
            max_tokens: Max response tokens
            temperature: Sampling temperature
            image_detail: Image detail level for vision ("low", "high", "auto")
            ocr_manager: OCR manager instance for hybrid mode
            analysis_mode: One of vision_only, ocr_only, hybrid, ocr_plus_vision
        """
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.image_detail = image_detail
        self.ocr_manager = ocr_manager
        self.analysis_mode = analysis_mode
        self.limiter = TokenBucketLimiter(max_tokens=5, refill_rate=1.0)
        self.total_calls = 0
        self.total_tokens = 0
        self.total_cost = 0.0
        self.last_ocr_result: Optional[OCRResult] = None

        # Detect provider from model name for logging
        self.provider = self._detect_provider(model)

        # Configure LiteLLM
        if _LITELLM_AVAILABLE:
            litellm.drop_params = True  # Drop unsupported params silently

        # Only enable verbose litellm logging if explicitly requested.
        # DEBUG=true alone does NOT enable it — litellm verbose mode can leak
        # API keys in request headers to stdout/logs.
        if _LITELLM_AVAILABLE and os.getenv("LITELLM_VERBOSE", "false").lower() == "true":
            litellm.set_verbose = True
            litellm.suppress_debug_info = False

        if not _LITELLM_AVAILABLE:
            logger.warning(
                "LiteLLM dependency unavailable; analysis calls will return error until installed"
            )

        logger.info(
            "LiteLLM analyzer initialized",
            model=model,
            provider=self.provider,
            api_base=api_base or "default",
            analysis_mode=analysis_mode,
            ocr_enabled=ocr_manager is not None and ocr_manager.enabled,
        )

    @staticmethod
    def _detect_provider(model: str) -> str:
        """Detect provider name from LiteLLM model string."""
        if "/" in model:
            return model.split("/")[0]
        # Models without prefix are assumed OpenAI
        if model.startswith("gpt-") or model.startswith("o1-") or model.startswith("o3-"):
            return "openai"
        return "unknown"

    def set_mode(self, mode: str) -> bool:
        """Switch analysis mode at runtime."""
        valid = [self.MODE_VISION_ONLY, self.MODE_OCR_ONLY,
                 self.MODE_HYBRID, self.MODE_OCR_PLUS_VISION]
        if mode not in valid:
            return False
        old = self.analysis_mode
        self.analysis_mode = mode
        logger.info("Analysis mode switched", old=old, new=mode)
        return True

    def _run_ocr(self, image_b64: str) -> OCRResult:
        """Run OCR pre-processing if available (synchronous)."""
        if self.ocr_manager and self.ocr_manager.enabled:
            result = self.ocr_manager.extract(image_b64)
            self.last_ocr_result = result
            return result
        return OCRResult(text="", engine="disabled")

    async def _arun_ocr(self, image_b64: str) -> OCRResult:
        """Run OCR pre-processing asynchronously (preferred in pipeline path)."""
        if self.ocr_manager and self.ocr_manager.enabled:
            if hasattr(self.ocr_manager, "aextract"):
                result = await self.ocr_manager.aextract(image_b64)
            else:
                result = self.ocr_manager.extract(image_b64)
            self.last_ocr_result = result
            return result
        return OCRResult(text="", engine="disabled")

    async def analyze(self, image_b64: str, context: str = "") -> Dict:
        """
        Analyze screen image via configured mode (Strategy Pattern).

        Each mode is an independent AnalysisStrategy that determines:
        - Whether OCR pre-processing is needed
        - Whether LLM call is needed
        - How to build the LLM messages (text-only vs image+text)

        Args:
            image_b64: Base64-encoded JPEG image
            context: Recent conversation context

        Returns:
            Analysis result dict
        """
        await self.limiter.acquire()
        self.total_calls += 1
        start_time = time.time()

        strategy = _STRATEGIES.get(self.analysis_mode, _STRATEGIES["hybrid"])

        try:
            # Step 1: OCR pre-processing (if strategy requires it)
            ocr_result = None
            ocr_context = ""
            if strategy.needs_ocr():
                ocr_result = await self._arun_ocr(image_b64)
                ocr_context = ocr_result.to_llm_context()

            # Step 2: OCR-only shortcut (no LLM call)
            if not strategy.needs_llm():
                return {
                    "text": ocr_result.text if ocr_result else "(brak wykrytego tekstu)",
                    "tokens": 0,
                    "cost": 0.0,
                    "provider": f"ocr:{ocr_result.engine}" if ocr_result else "ocr:disabled",
                    "model": ocr_result.engine if ocr_result else "none",
                    "mode": self.analysis_mode,
                    "ocr": ocr_result.to_dict() if ocr_result else None,
                }

            # Step 3: Build prompt
            prompt = self._build_prompt(ocr_context, context)

            # Step 4: Build messages via strategy
            messages = strategy.build_messages(prompt, image_b64, self.image_detail)

            # Step 5: Call LLM
            result = await self._call_llm(messages, ocr_result, start_time)
            return result

        except Exception as e:
            logger.error(
                "Vision analysis failed",
                provider=self.provider,
                model=self.model,
                mode=self.analysis_mode,
                error=str(e),
            )
            return {
                "text": f"⚠️ Błąd analizy: {str(e)}",
                "error": True,
                "tokens": 0,
                "cost": 0.0,
                "provider": self.provider,
                "model": self.model,
                "mode": self.analysis_mode,
            }

    def _build_prompt(self, ocr_context: str, context: str) -> str:
        """Build the system prompt with optional OCR and conversation context."""
        prompt = self.SYSTEM_PROMPT
        if ocr_context:
            prompt += f"\n\nTekst wyekstrahowany z ekranu (OCR):\n{ocr_context}"
        if context:
            prompt += f"\n\nOstatni kontekst:\n{context}"
        return prompt

    async def _call_llm(self, messages: list, ocr_result, start_time: float) -> Dict:
        """Execute LLM call via LiteLLM and return structured result."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        response = await acompletion(**kwargs)
        text = response.choices[0].message.content or ""

        tokens = 0
        cost = 0.0
        if hasattr(response, "usage") and response.usage:
            tokens = response.usage.total_tokens or 0
            try:
                cost = litellm.completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

        self.total_tokens += tokens
        self.total_cost += cost
        latency = time.time() - start_time

        logger.info(
            "Vision analysis complete",
            provider=self.provider,
            model=self.model,
            mode=self.analysis_mode,
            latency_ms=round(latency * 1000),
            tokens=tokens,
            cost=round(cost, 6),
            ocr_engine=ocr_result.engine if ocr_result else "none",
        )

        result = {
            "text": text,
            "tokens": tokens,
            "cost": cost,
            "provider": self.provider,
            "model": self.model,
            "mode": self.analysis_mode,
        }
        if ocr_result:
            result["ocr"] = ocr_result.to_dict()
        return result

    def get_stats(self) -> Dict:
        """Get analyzer statistics."""
        stats = {
            "provider": self.provider,
            "model": self.model,
            "api_base": self.api_base or "default",
            "analysis_mode": self.analysis_mode,
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost, 4),
            "avg_tokens_per_call": (
                round(self.total_tokens / self.total_calls)
                if self.total_calls > 0
                else 0
            ),
        }
        if self.ocr_manager:
            stats["ocr"] = self.ocr_manager.get_stats()
        return stats


@nfo.log_call(level="INFO")
def create_analyzer_from_env(ocr_manager: Optional[OCRManager] = None, settings=None) -> ScreenAnalyzer:
    """Create analyzer from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return ScreenAnalyzer(
        model=settings.vision_model,
        api_base=settings.litellm_api_base or None,
        api_key=settings.litellm_api_key or None,
        max_tokens=settings.vision_max_tokens,
        temperature=settings.vision_temperature,
        image_detail=settings.vision_image_detail,
        ocr_manager=ocr_manager,
        analysis_mode=settings.analysis_mode,
    )
