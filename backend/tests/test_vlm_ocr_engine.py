"""Tests for vlm_ocr_engine.py — VLMOCREngine sync/async extraction, cost tracking, clean_response."""
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock litellm before import
_mock_litellm = MagicMock()
_mock_litellm.completion = MagicMock()
_mock_litellm.acompletion = AsyncMock()
_mock_litellm.completion_cost = MagicMock(return_value=0.0)
sys.modules.setdefault("litellm", _mock_litellm)

from ocr_engines import BaseOCREngine, OCRResult
from vlm_ocr_engine import VLMOCREngine, VLM_OCR_SYSTEM_PROMPT, VLM_OCR_SYSTEM_PROMPT_SHORT


def _make_response(text="extracted text", tokens=50, cost=0.0001):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage = MagicMock()
    resp.usage.total_tokens = tokens
    _mock_litellm.completion_cost.return_value = cost
    return resp


class TestVLMOCREngineInit:
    def test_inherits_base_ocr_engine(self):
        engine = VLMOCREngine()
        assert isinstance(engine, BaseOCREngine)

    def test_default_values(self):
        engine = VLMOCREngine()
        assert engine.name == "vlm_ocr"
        assert engine.ENGINE_NAME == "vlm_ocr"
        assert engine.DISPLAY_NAME == "VLM OCR (Cloud)"
        assert engine.max_tokens == 1500
        assert engine.temperature == 0.1
        assert engine.image_detail == "low"
        assert engine.timeout == 15.0
        assert engine.short_prompt is True

    def test_custom_values(self):
        engine = VLMOCREngine(
            model="gemini/gemini-2.0-flash",
            api_key="test-key",
            max_tokens=2000,
            temperature=0.3,
            image_detail="high",
            timeout=30.0,
            short_prompt=False,
            languages=["en"],
        )
        assert engine.model == "gemini/gemini-2.0-flash"
        assert engine.api_key == "test-key"
        assert engine.max_tokens == 2000
        assert engine.image_detail == "high"
        assert engine.languages == ["en"]

    def test_extract_raises_not_implemented(self):
        engine = VLMOCREngine()
        with pytest.raises(NotImplementedError):
            engine._extract(None)


class TestVLMOCREngineInitialize:
    def test_initialize_sets_litellm(self):
        engine = VLMOCREngine()
        engine._initialize()
        assert engine._litellm is not None

    def test_initialize_resolves_openrouter_key(self):
        engine = VLMOCREngine(model="openrouter/qwen/test")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}):
            engine._initialize()
        assert engine.api_key == "sk-or-test"

    def test_initialize_resolves_gemini_key(self):
        engine = VLMOCREngine(model="gemini/flash")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-test"}):
            engine._initialize()
        assert engine.api_key == "AIza-test"

    def test_initialize_keeps_explicit_key(self):
        engine = VLMOCREngine(model="openrouter/qwen/test", api_key="explicit")
        engine._initialize()
        assert engine.api_key == "explicit"


class TestVLMOCREngineExtract:
    def setup_method(self):
        self.engine = VLMOCREngine(api_key="test")
        self.engine._litellm = _mock_litellm
        self.engine.is_initialized = True
        _mock_litellm.completion.side_effect = None
        _mock_litellm.completion_cost.side_effect = None
        _mock_litellm.completion_cost.return_value = 0.0

    def test_successful_extraction(self):
        _mock_litellm.completion.return_value = _make_response("Hello World", 30, 0.0001)
        result = self.engine.extract_from_b64("fake_b64")

        assert isinstance(result, OCRResult)
        assert result.text == "Hello World"
        assert result.engine == "vlm_ocr"
        assert result.confidence == 0.85
        assert result.latency_ms > 0
        assert self.engine.total_calls == 1

    def test_no_text_detected(self):
        _mock_litellm.completion.return_value = _make_response("NO_TEXT_DETECTED")
        result = self.engine.extract_from_b64("fake_b64")
        assert result.text == ""
        assert result.confidence == 0.0

    def test_api_failure_returns_empty(self):
        _mock_litellm.completion.side_effect = Exception("API timeout")
        result = self.engine.extract_from_b64("fake_b64")
        assert result.text == ""
        assert result.confidence == 0.0
        assert self.engine._error_count == 1

    def test_lazy_init_on_first_call(self):
        engine = VLMOCREngine(api_key="test")
        engine._litellm = None
        engine.is_initialized = False
        # _initialize will set _litellm from import
        _mock_litellm.completion.return_value = _make_response("text")
        result = engine.extract_from_b64("fake_b64")
        assert engine.is_initialized is True

    def test_init_failure_returns_empty(self):
        engine = VLMOCREngine(api_key="test")
        engine.is_initialized = False
        with patch.object(engine, "_initialize", side_effect=RuntimeError("no litellm")):
            result = engine.extract_from_b64("fake_b64")
        assert result.text == ""
        assert result.confidence == 0.0

    def test_cost_tracking(self):
        _mock_litellm.completion.return_value = _make_response("text", 100, 0.005)
        self.engine.extract_from_b64("fake_b64")
        assert self.engine._last_cost == 0.005
        assert self.engine._total_cost == 0.005

        _mock_litellm.completion.return_value = _make_response("more", 50, 0.003)
        self.engine.extract_from_b64("fake_b64")
        assert self.engine._last_cost == 0.003
        assert self.engine._total_cost == 0.008

    def test_token_tracking(self):
        _mock_litellm.completion.return_value = _make_response("text", 120)
        self.engine.extract_from_b64("fake_b64")
        assert self.engine._total_tokens_used == 120

    def test_short_prompt_used_by_default(self):
        _mock_litellm.completion.return_value = _make_response("text")
        self.engine.extract_from_b64("fake_b64")
        call_kwargs = _mock_litellm.completion.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        system_content = messages[0]["content"]
        assert system_content == VLM_OCR_SYSTEM_PROMPT_SHORT

    def test_long_prompt_when_short_disabled(self):
        self.engine.short_prompt = False
        _mock_litellm.completion.return_value = _make_response("text")
        self.engine.extract_from_b64("fake_b64")
        call_kwargs = _mock_litellm.completion.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        system_content = messages[0]["content"]
        assert system_content == VLM_OCR_SYSTEM_PROMPT


class TestVLMOCREngineAsync:
    def setup_method(self):
        self.engine = VLMOCREngine(api_key="test")
        self.engine._litellm = _mock_litellm
        self.engine.is_initialized = True

    @pytest.mark.asyncio
    async def test_async_extraction(self):
        _mock_litellm.acompletion.return_value = _make_response("async text", 40, 0.0002)
        result = await self.engine.aextract_from_b64("fake_b64")
        assert result.text == "async text"
        assert result.engine == "vlm_ocr"
        assert result.confidence == 0.85
        _mock_litellm.acompletion.assert_called()

    @pytest.mark.asyncio
    async def test_async_api_failure(self):
        _mock_litellm.acompletion.side_effect = Exception("network error")
        result = await self.engine.aextract_from_b64("fake_b64")
        assert result.text == ""
        assert result.confidence == 0.0
        assert self.engine._error_count == 1

    @pytest.mark.asyncio
    async def test_async_cost_tracking(self):
        _mock_litellm.acompletion.return_value = _make_response("text", 80, 0.004)
        _mock_litellm.acompletion.side_effect = None
        await self.engine.aextract_from_b64("fake_b64")
        assert self.engine._last_cost == 0.004
        assert self.engine._total_cost == 0.004

    @pytest.mark.asyncio
    async def test_async_no_text_detected(self):
        _mock_litellm.acompletion.return_value = _make_response("NO_TEXT_DETECTED")
        _mock_litellm.acompletion.side_effect = None
        result = await self.engine.aextract_from_b64("fake_b64")
        assert result.text == ""


class TestCleanResponse:
    def setup_method(self):
        self.engine = VLMOCREngine()

    def test_strips_markdown_code_blocks(self):
        assert self.engine._clean_response("```\nhello\n```") == "hello"

    def test_strips_leading_backticks(self):
        result = self.engine._clean_response("```hello world")
        assert "```" not in result

    def test_strips_common_prefixes(self):
        assert self.engine._clean_response("Here is the extracted text: hello") == "hello"
        assert self.engine._clean_response("Extracted text: world") == "world"
        assert self.engine._clean_response("OCR Result: foo") == "foo"

    def test_preserves_clean_text(self):
        assert self.engine._clean_response("just normal text") == "just normal text"


class TestGetStats:
    def test_stats_include_vlm_fields(self):
        engine = VLMOCREngine(model="test/model")
        engine._error_count = 2
        engine._total_tokens_used = 500
        engine._total_cost = 0.01
        engine._last_cost = 0.002
        stats = engine.get_stats()

        assert stats["display_name"] == "VLM OCR (Cloud)"
        assert stats["model"] == "test/model"
        assert stats["errors"] == 2
        assert stats["total_tokens"] == 500
        assert stats["total_cost_usd"] == 0.01
        assert stats["last_cost_usd"] == 0.002
        assert stats["image_detail"] == "low"
        # Base stats
        assert "engine" in stats
        assert "initialized" in stats
        assert "total_calls" in stats
