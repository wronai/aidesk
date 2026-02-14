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
import structlog
import litellm
from litellm import acompletion

from ocr_engines import OCRManager, OCRResult

logger = structlog.get_logger()

# Suppress LiteLLM's verbose logging by default
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
        litellm.drop_params = True  # Drop unsupported params silently

        if os.getenv("DEBUG", "false").lower() == "true":
            litellm.set_verbose = True

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
        """Run OCR pre-processing if available."""
        if self.ocr_manager and self.ocr_manager.enabled:
            result = self.ocr_manager.extract(image_b64)
            self.last_ocr_result = result
            return result
        return OCRResult(text="", engine="disabled")

    async def analyze(self, image_b64: str, context: str = "") -> Dict:
        """
        Analyze screen image via configured mode.

        Modes:
          - vision_only: Pure VLM analysis (original behavior)
          - ocr_only: OCR text extraction only (no LLM call, fastest)
          - hybrid: OCR text → LLM text prompt (no image sent, 5-10x faster)
          - ocr_plus_vision: OCR text + image → VLM (most accurate)

        Args:
            image_b64: Base64-encoded JPEG image
            context: Recent conversation context

        Returns:
            Analysis result dict
        """
        await self.limiter.acquire()
        self.total_calls += 1
        start_time = time.time()

        try:
            # === OCR-only mode: fast text extraction, no LLM ===
            if self.analysis_mode == self.MODE_OCR_ONLY:
                ocr_result = self._run_ocr(image_b64)
                latency = time.time() - start_time
                return {
                    "text": ocr_result.text or "(brak wykrytego tekstu)",
                    "tokens": 0,
                    "cost": 0.0,
                    "provider": f"ocr:{ocr_result.engine}",
                    "model": ocr_result.engine,
                    "mode": self.MODE_OCR_ONLY,
                    "ocr": ocr_result.to_dict(),
                }

            # === Run OCR pre-processing for hybrid modes ===
            ocr_result = None
            ocr_context = ""
            if self.analysis_mode in (self.MODE_HYBRID, self.MODE_OCR_PLUS_VISION):
                ocr_result = self._run_ocr(image_b64)
                ocr_context = ocr_result.to_llm_context()

            # === Build prompt ===
            prompt = self.SYSTEM_PROMPT
            if ocr_context:
                prompt += f"\n\nTekst wyekstrahowany z ekranu (OCR):\n{ocr_context}"
            if context:
                prompt += f"\n\nOstatni kontekst:\n{context}"

            # === Build messages based on mode ===
            if self.analysis_mode == self.MODE_HYBRID and ocr_context:
                # Hybrid: text-only prompt (no image), much cheaper/faster
                messages = [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ]
            else:
                # Vision modes: send image
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}",
                                    "detail": self.image_detail,
                                },
                            },
                        ],
                    }
                ]

            # Build kwargs for litellm
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

            # Extract token usage (LiteLLM normalizes this across providers)
            tokens = 0
            cost = 0.0
            if hasattr(response, "usage") and response.usage:
                tokens = response.usage.total_tokens or 0
                # LiteLLM can compute cost automatically
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


def create_analyzer_from_env(ocr_manager: Optional[OCRManager] = None) -> ScreenAnalyzer:
    """Create analyzer from environment variables."""
    from dotenv import load_dotenv

    load_dotenv()

    return ScreenAnalyzer(
        model=os.getenv("VISION_MODEL", "ollama/llava"),
        api_base=os.getenv("LITELLM_API_BASE") or None,
        api_key=os.getenv("LITELLM_API_KEY") or None,
        max_tokens=int(os.getenv("VISION_MAX_TOKENS", "400")),
        temperature=float(os.getenv("VISION_TEMPERATURE", "0.3")),
        image_detail=os.getenv("VISION_IMAGE_DETAIL", "low"),
        ocr_manager=ocr_manager,
        analysis_mode=os.getenv("ANALYSIS_MODE", "hybrid"),
    )
