"""
Vision AI analyzer with support for multiple providers.
"""
import os
import time
import asyncio
from typing import Optional, Dict, Literal
import structlog
import google.generativeai as genai
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
import PIL.Image
from io import BytesIO
import base64
import json

logger = structlog.get_logger()

VisionProvider = Literal["gemini", "openai", "claude"]


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
    Multi-provider vision AI analyzer for screen content.
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

    def __init__(
        self,
        provider: VisionProvider = "gemini",
        gemini_model: str = "gemini-2.0-flash-exp",
        openai_model: str = "gpt-4o-mini",
        claude_model: str = "claude-sonnet-4-20250514",
    ):
        """
        Initialize analyzer.

        Args:
            provider: Vision provider (gemini | openai | claude)
            gemini_model: Gemini model name
            openai_model: OpenAI model name
            claude_model: Claude model name
        """
        self.provider = provider
        self.limiter = TokenBucketLimiter(max_tokens=5, refill_rate=1.0)
        self.total_calls = 0
        self.total_tokens = 0
        self.total_cost = 0.0

        # Initialize providers
        if provider == "gemini":
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not set in environment")
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(gemini_model)
            self.model_name = gemini_model
            logger.info("Gemini initialized", model=gemini_model)

        elif provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not set in environment")
            self.openai = AsyncOpenAI(api_key=api_key)
            self.model_name = openai_model
            logger.info("OpenAI initialized", model=openai_model)

        elif provider == "claude":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set in environment")
            self.anthropic = AsyncAnthropic(api_key=api_key)
            self.model_name = claude_model
            logger.info("Claude initialized", model=claude_model)

        else:
            raise ValueError(f"Unknown provider: {provider}")

    async def analyze(self, image_b64: str, context: str = "") -> Dict:
        """
        Analyze screen image.

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
            prompt = self.SYSTEM_PROMPT
            if context:
                prompt += f"\n\nOstatni kontekst:\n{context}"

            if self.provider == "gemini":
                result = await self._analyze_gemini(image_b64, prompt)
            elif self.provider == "openai":
                result = await self._analyze_openai(image_b64, prompt)
            elif self.provider == "claude":
                result = await self._analyze_claude(image_b64, prompt)

            latency = time.time() - start_time

            logger.info(
                "Vision analysis complete",
                provider=self.provider,
                latency_ms=round(latency * 1000),
                tokens=result.get("tokens", 0),
                cost=result.get("cost", 0.0),
            )

            return result

        except Exception as e:
            logger.error("Vision analysis failed", provider=self.provider, error=str(e))
            return {
                "text": f"⚠️ Błąd analizy: {str(e)}",
                "error": True,
                "tokens": 0,
                "cost": 0.0,
            }

    async def _analyze_gemini(self, image_b64: str, prompt: str) -> Dict:
        """Analyze with Gemini."""
        image_bytes = base64.b64decode(image_b64)
        img = PIL.Image.open(BytesIO(image_bytes))

        response = await asyncio.to_thread(
            self.model.generate_content, [prompt, img]
        )

        text = response.text
        # Gemini pricing: $0.10 / 1M input tokens, $0.40 / 1M output
        tokens = response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') else 1500
        cost = (tokens * 0.10 / 1_000_000)  # Simplified

        self.total_tokens += tokens
        self.total_cost += cost

        return {"text": text, "tokens": tokens, "cost": cost, "provider": "gemini"}

    async def _analyze_openai(self, image_b64: str, prompt: str) -> Dict:
        """Analyze with OpenAI."""
        response = await self.openai.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": "low",  # 85 tokens vs 1105 for high
                            },
                        },
                    ],
                }
            ],
            max_tokens=400,
        )

        text = response.choices[0].message.content
        tokens = response.usage.total_tokens

        # GPT-4o-mini pricing: $0.15 / 1M input, $0.60 / 1M output
        cost = (
            response.usage.prompt_tokens * 0.15 / 1_000_000
            + response.usage.completion_tokens * 0.60 / 1_000_000
        )

        self.total_tokens += tokens
        self.total_cost += cost

        return {"text": text, "tokens": tokens, "cost": cost, "provider": "openai"}

    async def _analyze_claude(self, image_b64: str, prompt: str) -> Dict:
        """Analyze with Claude."""
        image_bytes = base64.b64decode(image_b64)

        response = await self.anthropic.messages.create(
            model=self.model_name,
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        text = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens

        # Claude Sonnet pricing: $3.00 / 1M input, $15.00 / 1M output
        cost = (
            response.usage.input_tokens * 3.00 / 1_000_000
            + response.usage.output_tokens * 15.00 / 1_000_000
        )

        self.total_tokens += tokens
        self.total_cost += cost

        return {"text": text, "tokens": tokens, "cost": cost, "provider": "claude"}

    def get_stats(self) -> Dict:
        """Get analyzer statistics."""
        return {
            "provider": self.provider,
            "model": self.model_name,
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost, 4),
            "avg_tokens_per_call": (
                round(self.total_tokens / self.total_calls)
                if self.total_calls > 0
                else 0
            ),
        }


def create_analyzer_from_env() -> ScreenAnalyzer:
    """Create analyzer from environment variables."""
    from dotenv import load_dotenv

    load_dotenv()

    return ScreenAnalyzer(
        provider=os.getenv("VISION_PROVIDER", "gemini"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
    )
