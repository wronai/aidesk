"""Tests for analyzer.py — strategies, mode selection, rate limiting, LLM call mocking.

litellm is mocked at module level to avoid import failures when not installed.
"""
import asyncio
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock litellm before importing analyzer
_mock_litellm = MagicMock()
_mock_litellm.suppress_debug_info = False
_mock_litellm.drop_params = False
_mock_litellm.set_verbose = False
_mock_litellm.acompletion = AsyncMock()
_mock_litellm.completion_cost = MagicMock(return_value=0.001)
sys.modules.setdefault("litellm", _mock_litellm)

from analyzer import (
    ScreenAnalyzer, TokenBucketLimiter,
    VisionOnlyStrategy, OCROnlyStrategy, HybridStrategy, OCRPlusVisionStrategy,
    _STRATEGIES,
)


# ── Strategies ────────────────────────────────────────────────────────

class TestStrategies:
    def test_vision_only(self):
        s = VisionOnlyStrategy()
        assert s.needs_ocr() is False
        assert s.needs_llm() is True
        msgs = s.build_messages("prompt", "abc123", "low")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        # Should contain image_url
        content = msgs[0]["content"]
        assert any(c.get("type") == "image_url" for c in content)

    def test_ocr_only(self):
        s = OCROnlyStrategy()
        assert s.needs_ocr() is True
        assert s.needs_llm() is False
        msgs = s.build_messages("prompt", "abc123", "low")
        assert msgs == []

    def test_hybrid(self):
        s = HybridStrategy()
        assert s.needs_ocr() is True
        assert s.needs_llm() is True
        msgs = s.build_messages("prompt text", "abc123", "low")
        assert len(msgs) == 1
        # Hybrid sends text-only, no image
        assert msgs[0]["content"] == "prompt text"

    def test_ocr_plus_vision(self):
        s = OCRPlusVisionStrategy()
        assert s.needs_ocr() is True
        assert s.needs_llm() is True
        msgs = s.build_messages("prompt", "abc123", "low")
        assert len(msgs) == 1
        content = msgs[0]["content"]
        assert any(c.get("type") == "image_url" for c in content)

    def test_strategy_registry(self):
        assert "vision_only" in _STRATEGIES
        assert "ocr_only" in _STRATEGIES
        assert "hybrid" in _STRATEGIES
        assert "ocr_plus_vision" in _STRATEGIES


# ── Provider detection ────────────────────────────────────────────────

class TestProviderDetection:
    def test_ollama(self):
        assert ScreenAnalyzer._detect_provider("ollama/llava") == "ollama"

    def test_gemini(self):
        assert ScreenAnalyzer._detect_provider("gemini/gemini-2.0-flash") == "gemini"

    def test_openai_gpt(self):
        assert ScreenAnalyzer._detect_provider("gpt-4o-mini") == "openai"

    def test_openai_o1(self):
        assert ScreenAnalyzer._detect_provider("o1-preview") == "openai"

    def test_anthropic(self):
        assert ScreenAnalyzer._detect_provider("anthropic/claude-sonnet-4-20250514") == "anthropic"

    def test_unknown(self):
        assert ScreenAnalyzer._detect_provider("some-model") == "unknown"


# ── Mode switching ────────────────────────────────────────────────────

class TestModeSwitch:
    def setup_method(self):
        self.analyzer = ScreenAnalyzer(model="ollama/llava", analysis_mode="hybrid")

    def test_switch_to_valid_mode(self):
        assert self.analyzer.set_mode("ocr_only") is True
        assert self.analyzer.analysis_mode == "ocr_only"

    def test_switch_to_invalid_mode(self):
        assert self.analyzer.set_mode("nonexistent") is False
        assert self.analyzer.analysis_mode == "hybrid"  # unchanged

    def test_switch_all_modes(self):
        for mode in ["vision_only", "ocr_only", "hybrid", "ocr_plus_vision"]:
            assert self.analyzer.set_mode(mode) is True
            assert self.analyzer.analysis_mode == mode


# ── Prompt building ──────────────────────────────────────────────────

class TestPromptBuilding:
    def setup_method(self):
        self.analyzer = ScreenAnalyzer(model="ollama/llava")

    def test_base_prompt(self):
        prompt = self.analyzer._build_prompt("", "")
        assert "desktop assistant" in prompt

    def test_prompt_with_ocr(self):
        prompt = self.analyzer._build_prompt("OCR extracted text here", "")
        assert "OCR extracted text here" in prompt
        assert "OCR" in prompt

    def test_prompt_with_context(self):
        prompt = self.analyzer._build_prompt("", "user context info")
        assert "user context info" in prompt

    def test_prompt_with_both(self):
        prompt = self.analyzer._build_prompt("ocr text", "context text")
        assert "ocr text" in prompt
        assert "context text" in prompt


# ── OCR execution ────────────────────────────────────────────────────

class TestOCR:
    def test_ocr_disabled_returns_empty(self):
        analyzer = ScreenAnalyzer(model="ollama/llava", ocr_manager=None)
        result = analyzer._run_ocr("fake_b64")
        assert result.text == ""
        assert result.engine == "disabled"

    def test_ocr_with_manager(self):
        mock_mgr = MagicMock()
        mock_mgr.enabled = True
        mock_result = MagicMock()
        mock_result.text = "detected text"
        mock_mgr.extract.return_value = mock_result

        analyzer = ScreenAnalyzer(model="ollama/llava", ocr_manager=mock_mgr)
        result = analyzer._run_ocr("fake_b64")
        assert result.text == "detected text"
        mock_mgr.extract.assert_called_once_with("fake_b64")


# ── Analyze (integration with mocked LLM) ────────────────────────────

class TestAnalyze:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.analyzer = ScreenAnalyzer(
            model="ollama/llava",
            analysis_mode="hybrid",
            ocr_manager=None,
        )

    @pytest.mark.asyncio
    async def test_ocr_only_mode_no_llm_call(self):
        mock_mgr = MagicMock()
        mock_mgr.enabled = True
        mock_ocr_result = MagicMock()
        mock_ocr_result.text = "screen text"
        mock_ocr_result.engine = "tesseract"
        mock_ocr_result.to_llm_context.return_value = "screen text"
        mock_ocr_result.to_dict.return_value = {"text": "screen text", "engine": "tesseract"}
        mock_mgr.extract.return_value = mock_ocr_result

        self.analyzer.ocr_manager = mock_mgr
        self.analyzer.set_mode("ocr_only")

        result = await self.analyzer.analyze("fake_b64", "context")
        assert result["mode"] == "ocr_only"
        assert result["text"] == "screen text"
        assert result["tokens"] == 0
        assert result["cost"] == 0.0
        # LLM should NOT be called
        _mock_litellm.acompletion.assert_not_called()

    @pytest.mark.asyncio
    async def test_hybrid_mode_calls_llm(self):
        # Setup mock LLM response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"app": "test", "summary": "ok"}'
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 150
        _mock_litellm.acompletion.return_value = mock_response
        _mock_litellm.completion_cost.return_value = 0.001

        self.analyzer.set_mode("hybrid")
        result = await self.analyzer.analyze("fake_b64", "context")

        assert result["mode"] == "hybrid"
        assert result["tokens"] == 150
        assert result["provider"] == "ollama"
        _mock_litellm.acompletion.assert_called()

    @pytest.mark.asyncio
    async def test_analyze_error_handling(self):
        _mock_litellm.acompletion.side_effect = Exception("API timeout")

        self.analyzer.set_mode("vision_only")
        result = await self.analyzer.analyze("fake_b64")

        assert result.get("error") is True
        assert "timeout" in result["text"].lower() or "błąd" in result["text"].lower()
        _mock_litellm.acompletion.side_effect = None  # cleanup

    @pytest.mark.asyncio
    async def test_analyze_increments_stats(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "result"
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 100
        _mock_litellm.acompletion.return_value = mock_response
        _mock_litellm.completion_cost.return_value = 0.002

        initial_calls = self.analyzer.total_calls
        await self.analyzer.analyze("fake_b64")
        assert self.analyzer.total_calls == initial_calls + 1


# ── Token bucket rate limiter ─────────────────────────────────────────

class TestTokenBucketLimiter:
    @pytest.mark.asyncio
    async def test_acquire_succeeds(self):
        limiter = TokenBucketLimiter(max_tokens=5, refill_rate=1.0)
        result = await limiter.acquire()
        assert result is True
        assert limiter.tokens < 5

    @pytest.mark.asyncio
    async def test_multiple_acquires(self):
        limiter = TokenBucketLimiter(max_tokens=3, refill_rate=1.0)
        for _ in range(3):
            await limiter.acquire()
        # All 3 tokens consumed
        assert limiter.tokens < 1

    @pytest.mark.asyncio
    async def test_refill_over_time(self):
        limiter = TokenBucketLimiter(max_tokens=2, refill_rate=100.0)  # fast refill
        await limiter.acquire()
        await limiter.acquire()
        # Wait a bit for refill
        await asyncio.sleep(0.05)
        result = await limiter.acquire()
        assert result is True


# ── Stats ─────────────────────────────────────────────────────────────

class TestAnalyzerStats:
    def test_initial_stats(self):
        analyzer = ScreenAnalyzer(model="gemini/gemini-2.0-flash", analysis_mode="hybrid")
        stats = analyzer.get_stats()
        assert stats["provider"] == "gemini"
        assert stats["model"] == "gemini/gemini-2.0-flash"
        assert stats["total_calls"] == 0
        assert stats["analysis_mode"] == "hybrid"

    def test_stats_with_ocr_manager(self):
        mock_mgr = MagicMock()
        mock_mgr.enabled = True
        mock_mgr.get_stats.return_value = {"engine": "tesseract", "total_extractions": 5}

        analyzer = ScreenAnalyzer(model="ollama/llava", ocr_manager=mock_mgr)
        stats = analyzer.get_stats()
        assert "ocr" in stats
        assert stats["ocr"]["engine"] == "tesseract"

    def test_avg_tokens_zero_calls(self):
        analyzer = ScreenAnalyzer(model="ollama/llava")
        assert analyzer.get_stats()["avg_tokens_per_call"] == 0
