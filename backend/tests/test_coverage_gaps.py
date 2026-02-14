"""
Tests for coverage gaps identified in Tier 1 analysis:
- config_service.py: .env parsing, update, schema
- analyzer.py: Strategy pattern (4 strategies)
- ocr_post_process.py: property-based fuzz tests
- async_subprocess.py: async helpers
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===== config_service.py =====

from config_service import _parse_env_file, read_env, update_env


class TestParseEnvFile:
    def _write_env(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_parse_simple_key_value(self):
        path = self._write_env("FOO=bar\nBAZ=123\n")
        lines, values, alts = _parse_env_file(path)
        assert values["FOO"] == "bar"
        assert values["BAZ"] == "123"

    def test_parse_preserves_comments(self):
        path = self._write_env("# This is a comment\nKEY=val\n")
        lines, values, alts = _parse_env_file(path)
        assert "KEY" in values
        assert len(lines) == 2

    def test_parse_empty_value(self):
        path = self._write_env("EMPTY=\n")
        _, values, _ = _parse_env_file(path)
        assert values["EMPTY"] == ""

    def test_parse_commented_alternative(self):
        path = self._write_env("#   VISION_MODEL=ollama/llava ← Ollama\nVISION_MODEL=gemini/gemini-2.0-flash\n")
        _, values, alts = _parse_env_file(path)
        assert values["VISION_MODEL"] == "gemini/gemini-2.0-flash"
        assert "VISION_MODEL" in alts
        assert any("ollama" in a["value"] for a in alts["VISION_MODEL"])

    def test_parse_nonexistent_file(self):
        lines, values, alts = _parse_env_file("/tmp/nonexistent_env_file_xyz.env")
        assert lines == []
        assert values == {}

    def test_parse_blank_lines(self):
        path = self._write_env("\n\nKEY=val\n\n")
        lines, values, _ = _parse_env_file(path)
        assert values["KEY"] == "val"
        assert len(lines) == 4

    def test_parse_example_line(self):
        path = self._write_env("# Przykład: STT_INPUT_DEVICE=alsa_input.usb\n")
        _, _, alts = _parse_env_file(path)
        assert "STT_INPUT_DEVICE" in alts


class TestReadEnv:
    def test_read_env_returns_dict(self):
        result = read_env()
        assert isinstance(result, dict)


class TestUpdateEnv:
    def test_update_creates_file_if_missing(self):
        # Save original path, point to temp
        import config_service
        orig = config_service.ENV_PATH
        try:
            with tempfile.NamedTemporaryFile(suffix=".env", delete=False) as f:
                config_service.ENV_PATH = f.name
            # Write initial content
            with open(config_service.ENV_PATH, "w") as f:
                f.write("KEY1=old\nKEY2=keep\n")
            result = update_env({"KEY1": "new"})
            assert result["KEY1"] == "new"
            # KEY2 should still exist
            with open(config_service.ENV_PATH) as f:
                content = f.read()
            assert "KEY2=keep" in content
            assert "KEY1=new" in content
        finally:
            config_service.ENV_PATH = orig


# ===== analyzer.py Strategy Pattern =====

from analyzer import (
    VisionOnlyStrategy,
    OCROnlyStrategy,
    HybridStrategy,
    OCRPlusVisionStrategy,
    _STRATEGIES,
)


class TestAnalysisStrategies:
    def test_vision_only_needs_no_ocr(self):
        s = VisionOnlyStrategy()
        assert not s.needs_ocr()
        assert s.needs_llm()

    def test_vision_only_builds_image_message(self):
        s = VisionOnlyStrategy()
        msgs = s.build_messages("prompt", "base64img", "low")
        assert len(msgs) == 1
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "base64img" in content[1]["image_url"]["url"]

    def test_ocr_only_needs_ocr_no_llm(self):
        s = OCROnlyStrategy()
        assert s.needs_ocr()
        assert not s.needs_llm()

    def test_ocr_only_builds_empty_messages(self):
        s = OCROnlyStrategy()
        msgs = s.build_messages("prompt", "img", "low")
        assert msgs == []

    def test_hybrid_needs_ocr_and_llm(self):
        s = HybridStrategy()
        assert s.needs_ocr()
        assert s.needs_llm()

    def test_hybrid_builds_text_only_message(self):
        s = HybridStrategy()
        msgs = s.build_messages("my prompt", "img", "low")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "my prompt"
        # No image_url in hybrid mode
        assert isinstance(msgs[0]["content"], str)

    def test_ocr_plus_vision_needs_both(self):
        s = OCRPlusVisionStrategy()
        assert s.needs_ocr()
        assert s.needs_llm()

    def test_ocr_plus_vision_builds_image_message(self):
        s = OCRPlusVisionStrategy()
        msgs = s.build_messages("prompt", "b64", "high")
        content = msgs[0]["content"]
        assert content[1]["image_url"]["detail"] == "high"

    def test_strategy_registry_has_all_modes(self):
        assert "vision_only" in _STRATEGIES
        assert "ocr_only" in _STRATEGIES
        assert "hybrid" in _STRATEGIES
        assert "ocr_plus_vision" in _STRATEGIES

    def test_strategy_registry_returns_correct_types(self):
        assert isinstance(_STRATEGIES["vision_only"], VisionOnlyStrategy)
        assert isinstance(_STRATEGIES["ocr_only"], OCROnlyStrategy)
        assert isinstance(_STRATEGIES["hybrid"], HybridStrategy)
        assert isinstance(_STRATEGIES["ocr_plus_vision"], OCRPlusVisionStrategy)


# ===== OCR Post-Processing: Fuzz/Property Tests =====

from ocr_post_process import OCREnhancer, PostProcessResult, TextType
import random
import string


class TestOCREnhancerFuzz:
    """Property-based-style fuzz tests for OCR enhancer robustness."""

    def _random_text(self, min_len=0, max_len=500):
        length = random.randint(min_len, max_len)
        chars = string.ascii_letters + string.digits + string.punctuation + " \n\t"
        return "".join(random.choice(chars) for _ in range(length))

    def test_enhance_never_crashes_on_random_input(self):
        """Fuzz: random text should never crash the enhancer."""
        enh = OCREnhancer(enable_spell_check=False)
        for _ in range(100):
            text = self._random_text(0, 1000)
            result = enh.enhance(text)
            assert isinstance(result, PostProcessResult)
            assert isinstance(result.enhanced_text, str)

    def test_enhance_empty_string(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("")
        assert result.enhanced_text == ""

    def test_enhance_whitespace_only(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("   \n\t  ")
        assert isinstance(result, PostProcessResult)

    def test_enhance_unicode(self):
        enh = OCREnhancer(enable_spell_check=False)
        result = enh.enhance("ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ 日本語 🎉")
        assert isinstance(result, PostProcessResult)

    def test_enhance_very_long_text(self):
        enh = OCREnhancer(enable_spell_check=False)
        text = "x" * 10000
        result = enh.enhance(text)
        assert isinstance(result, PostProcessResult)

    def test_enhance_all_text_types_valid(self):
        enh = OCREnhancer(enable_spell_check=False)
        for hint in (TextType.CODE, TextType.TERMINAL, TextType.PROSE, TextType.MIXED, TextType.UNKNOWN):
            result = enh.enhance("some text here", hint_type=hint)
            assert isinstance(result, PostProcessResult)
            assert result.text_type == hint

    def test_enhance_preserves_length_approximately(self):
        """Enhanced text should not be drastically different in length."""
        enh = OCREnhancer(enable_spell_check=False)
        text = "def __init__(self):\n    self.value = None\n    return True"
        result = enh.enhance(text, hint_type=TextType.CODE)
        # Should not shrink to empty or grow 10x
        assert len(result.enhanced_text) > 0
        assert len(result.enhanced_text) < len(text) * 3

    def test_idempotent_on_clean_text(self):
        """Clean text should pass through with minimal changes."""
        enh = OCREnhancer(enable_spell_check=False)
        clean = "def hello():\n    print('world')\n    return 42"
        result = enh.enhance(clean, hint_type=TextType.CODE)
        # No corrections expected on clean code
        assert result.enhanced_text == clean or result.corrections_count == 0


# ===== async_subprocess.py =====

from async_subprocess import run_async, run_async_shell, run_in_thread


class TestAsyncSubprocess:
    def test_run_async_echo(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_async(["echo", "hello"])
        )
        assert result == "hello"

    def test_run_async_nonexistent_command(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_async(["nonexistent_command_xyz"], timeout=1.0)
        )
        assert result is None

    def test_run_async_timeout(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_async(["sleep", "10"], timeout=0.1)
        )
        assert result is None

    def test_run_async_shell_echo(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_async_shell("echo hello_world")
        )
        assert result["returncode"] == 0
        assert "hello_world" in result["stdout"]

    def test_run_async_shell_timeout(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_async_shell("sleep 10", timeout=0.1)
        )
        assert result["timed_out"]

    def test_run_in_thread_echo(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_in_thread(["echo", "threaded"])
        )
        assert result == "threaded"

    def test_run_in_thread_failure(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_in_thread(["false"])
        )
        assert result is None
