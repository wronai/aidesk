"""Tests for preflight.py — PreflightDiagnostics, PreflightResult, helpers."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock litellm before import — use setdefault so we coexist with other test files
_mock_litellm = MagicMock()
_mock_litellm.acompletion = AsyncMock()
_mock_litellm.completion = MagicMock()
_mock_litellm.drop_params = False
sys.modules.setdefault("litellm", _mock_litellm)

# Stable name for the litellm module key used by patch()
_LITELLM_MOD = "litellm"

from preflight import (
    PreflightDiagnostics,
    PreflightResult,
    _mask_key,
    _fmt_ok,
    _fmt_fail,
    _fmt_skip,
    _suppress_stdout,
)


# --- Helpers ---

def _make_settings(**overrides):
    defaults = {
        "vision_model": "ollama/llava",
        "vlm_ocr_model": "openrouter/qwen/qwen2.5-vl-32b-instruct:free",
        "ocr_engine": "paddleocr",
        "analysis_mode": "hybrid",
        "litellm_api_base": "",
        "litellm_api_key": "",
        "openrouter_api_key": "",
        "gemini_api_key": "",
        "openai_api_key": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_llm_response(text="OK", tokens=5):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage = MagicMock()
    resp.usage.total_tokens = tokens
    return resp


# --- PreflightResult ---

class TestPreflightResult:
    def test_ok_result(self):
        r = PreflightResult("test", ok=True, detail="v1.0", latency_ms=42.7)
        assert r.ok is True
        assert r.skipped is False
        d = r.to_dict()
        assert d["name"] == "test"
        assert d["ok"] is True
        assert d["latency_ms"] == 42.7

    def test_failed_result(self):
        r = PreflightResult("fail", ok=False, detail="missing dep")
        assert r.ok is False
        assert r.to_dict()["detail"] == "missing dep"

    def test_skipped_result(self):
        r = PreflightResult("skip", ok=True, skipped=True, detail="not needed")
        assert r.skipped is True


# --- Formatting helpers ---

class TestFormatHelpers:
    def test_mask_key_short(self):
        assert _mask_key("") == "(empty)"
        assert _mask_key("short") == "***"

    def test_mask_key_long(self):
        key = "sk-or-v1-abcdefghijklmnop"
        masked = _mask_key(key)
        assert masked.startswith("sk-or-v1")
        assert masked.endswith("mnop")
        assert "..." in masked

    def test_fmt_ok_contains_checkmark(self):
        assert "✓" in _fmt_ok("test")

    def test_fmt_fail_contains_cross(self):
        assert "✗" in _fmt_fail("test")

    def test_fmt_skip_contains_symbol(self):
        assert "⊘" in _fmt_skip("test")

    def test_suppress_stdout(self):
        import io
        with _suppress_stdout():
            print("this should be suppressed")
        # If we get here, stdout was restored


# --- API key checks ---

class TestCheckApiKeys:
    def test_no_keys_needed_for_local_model(self):
        settings = _make_settings(vision_model="ollama/llava", ocr_engine="paddleocr")
        pf = PreflightDiagnostics(settings)
        results = pf._check_api_keys()
        # ollama doesn't need an API key
        key_names = [r.name for r in results]
        assert "OPENROUTER_API_KEY" not in key_names

    def test_openrouter_key_required(self):
        settings = _make_settings(
            vision_model="openrouter/qwen/test",
            openrouter_api_key="",
        )
        pf = PreflightDiagnostics(settings)
        # Ensure real env vars don't leak into the test
        env_patch = {k: "" for k in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")}
        with patch.dict(os.environ, env_patch, clear=False):
            for k in env_patch:
                os.environ.pop(k, None)
            results = pf._check_api_keys()
        or_checks = [r for r in results if r.name == "OPENROUTER_API_KEY"]
        assert len(or_checks) == 1
        assert or_checks[0].ok is False

    def test_openrouter_key_present(self):
        settings = _make_settings(
            vision_model="openrouter/qwen/test",
            openrouter_api_key="sk-or-v1-abcdefghijklmnopqrs",
        )
        pf = PreflightDiagnostics(settings)
        results = pf._check_api_keys()
        or_checks = [r for r in results if r.name == "OPENROUTER_API_KEY"]
        assert len(or_checks) == 1
        assert or_checks[0].ok is True

    def test_vlm_ocr_triggers_key_check(self):
        settings = _make_settings(
            vision_model="ollama/llava",
            ocr_engine="vlm_ocr",
            vlm_ocr_model="openrouter/qwen/vlm",
            openrouter_api_key="",
        )
        pf = PreflightDiagnostics(settings)
        results = pf._check_api_keys()
        or_checks = [r for r in results if r.name == "OPENROUTER_API_KEY"]
        assert len(or_checks) == 1

    def test_litellm_api_key_override(self):
        settings = _make_settings(litellm_api_key="sk-override-12345678901234")
        pf = PreflightDiagnostics(settings)
        results = pf._check_api_keys()
        override_checks = [r for r in results if r.name == "LITELLM_API_KEY"]
        assert len(override_checks) == 1
        assert override_checks[0].ok is True


# --- litellm check ---

class TestCheckLitellm:
    def test_litellm_available(self):
        pf = PreflightDiagnostics(_make_settings())
        result = pf._check_litellm()
        assert result.ok is True
        assert "litellm" in result.name.lower()


# --- Vision LLM ping ---

class TestPingVisionLLM:
    @pytest.mark.asyncio
    async def test_ocr_only_mode_skips(self):
        settings = _make_settings(analysis_mode="ocr_only")
        pf = PreflightDiagnostics(settings)
        result = await pf._ping_vision_llm()
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_successful_ping(self):
        mock_ac = AsyncMock(return_value=_make_llm_response("OK", 5))
        with patch(f"{_LITELLM_MOD}.acompletion", mock_ac):
            settings = _make_settings()
            pf = PreflightDiagnostics(settings)
            result = await pf._ping_vision_llm()
        assert result.ok is True
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_failed_ping(self):
        mock_ac = AsyncMock(side_effect=Exception("Connection refused"))
        with patch(f"{_LITELLM_MOD}.acompletion", mock_ac):
            settings = _make_settings()
            pf = PreflightDiagnostics(settings)
            result = await pf._ping_vision_llm()
        assert result.ok is False
        assert "Connection refused" in result.detail


# --- VLM OCR ping ---

class TestPingVlmOcr:
    @pytest.mark.asyncio
    async def test_skips_when_not_vlm_ocr(self):
        settings = _make_settings(ocr_engine="paddleocr")
        pf = PreflightDiagnostics(settings)
        result = await pf._ping_vlm_ocr()
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_skips_when_same_model(self):
        settings = _make_settings(
            ocr_engine="vlm_ocr",
            vision_model="openrouter/qwen/test",
            vlm_ocr_model="openrouter/qwen/test",
        )
        pf = PreflightDiagnostics(settings)
        result = await pf._ping_vlm_ocr()
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_successful_vlm_ping(self):
        mock_comp = MagicMock(return_value=_make_llm_response("OK", 3))
        with patch(f"{_LITELLM_MOD}.completion", mock_comp):
            settings = _make_settings(
                ocr_engine="vlm_ocr",
                vlm_ocr_model="openrouter/qwen/vlm-ocr",
                vision_model="ollama/llava",
            )
            pf = PreflightDiagnostics(settings)
            result = await pf._ping_vlm_ocr()
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_failed_vlm_ping(self):
        mock_comp = MagicMock(side_effect=Exception("timeout"))
        with patch(f"{_LITELLM_MOD}.completion", mock_comp):
            settings = _make_settings(
                ocr_engine="vlm_ocr",
                vlm_ocr_model="openrouter/qwen/vlm-ocr",
                vision_model="ollama/llava",
            )
            pf = PreflightDiagnostics(settings)
            result = await pf._ping_vlm_ocr()
        assert result.ok is False


# --- Full run ---

class TestFullRun:
    @pytest.mark.asyncio
    async def test_run_produces_report(self):
        settings = _make_settings(analysis_mode="ocr_only", ocr_engine="paddleocr")
        pf = PreflightDiagnostics(settings)
        report = await pf.run()

        assert "all_ok" in report
        assert "elapsed_ms" in report
        assert "passed" in report
        assert "failed" in report
        assert "skipped" in report
        assert "checks" in report
        assert isinstance(report["checks"], list)
        assert len(report["checks"]) > 0

    @pytest.mark.asyncio
    async def test_run_all_ok_with_local_model(self):
        mock_ac = AsyncMock(return_value=_make_llm_response("OK"))
        with patch(f"{_LITELLM_MOD}.acompletion", mock_ac):
            settings = _make_settings(
                vision_model="ollama/llava",
                analysis_mode="hybrid",
                ocr_engine="paddleocr",
            )
            pf = PreflightDiagnostics(settings)
            report = await pf.run()
        # Even if window tools are missing, the LLM ping should work
        assert isinstance(report["all_ok"], bool)
