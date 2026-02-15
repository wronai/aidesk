"""Tests for Pydantic Settings configuration."""
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from settings import Settings, get_settings, reload_settings


class TestSettingsDefaults:
    def test_default_vision_model(self, monkeypatch):
        monkeypatch.delenv("VISION_MODEL", raising=False)
        s = Settings(_env_file=None)
        assert s.vision_model == "ollama/llava"

    def test_default_port(self, monkeypatch):
        monkeypatch.delenv("PORT", raising=False)
        s = Settings(_env_file=None)
        assert s.port == 8000

    def test_default_analysis_mode(self, monkeypatch):
        monkeypatch.delenv("ANALYSIS_MODE", raising=False)
        s = Settings(_env_file=None)
        assert s.analysis_mode == "hybrid"

    def test_default_ocr_engine(self, monkeypatch):
        monkeypatch.delenv("OCR_ENGINE", raising=False)
        s = Settings(_env_file=None)
        assert s.ocr_engine == "paddleocr"

    def test_default_tts_engine(self):
        s = Settings(_env_file=None)
        assert s.tts_engine == "auto"

    def test_default_feature_flags(self):
        s = Settings(_env_file=None)
        assert s.enable_stt is True
        assert s.enable_vision is True
        assert s.enable_window_aware is True
        assert s.enable_shell_agent is True

    def test_default_circuit_breaker(self):
        s = Settings(_env_file=None)
        assert s.analyze_circuit_threshold == 5
        assert s.analyze_max_retries == 2


class TestSettingsValidation:
    def test_invalid_analysis_mode_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, analysis_mode="invalid_mode")

    def test_vlm_ocr_engine_accepted(self):
        s = Settings(_env_file=None, ocr_engine="vlm_ocr")
        assert s.ocr_engine == "vlm_ocr"

    def test_invalid_ocr_engine_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, ocr_engine="invalid_engine")

    def test_invalid_tts_engine_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, tts_engine="invalid_tts")

    def test_port_range_enforced(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, port=99999)

    def test_temperature_range_enforced(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, vision_temperature=5.0)

    def test_jpeg_quality_range(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, jpeg_quality=0)

    def test_valid_capture_mode(self):
        s = Settings(_env_file=None, capture_mode="window")
        assert s.capture_mode == "window"

    def test_invalid_capture_mode(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, capture_mode="invalid")


class TestOptimizationSettingsValidation:
    def test_defaults_present(self):
        s = Settings(_env_file=None)
        assert s.optimization_priority == "auto"
        assert s.hardware_profile == "auto"
        assert s.prefer_local_ocr == "auto"
        assert s.prefer_local_llm == "auto"

    def test_invalid_optimization_priority_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, optimization_priority="cheap")

    def test_invalid_hardware_profile_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, hardware_profile="gpu_ultra")

    def test_invalid_prefer_local_ocr_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, prefer_local_ocr="sometimes")

    def test_invalid_prefer_local_llm_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, prefer_local_llm="sometimes")

    def test_budget_threshold_range_enforced(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, budget_warning_pct=101)
        with pytest.raises(Exception):
            Settings(_env_file=None, budget_critical_pct=-1)


class TestVlmOcrSettingsValidation:
    def test_vlm_ocr_timeout_too_low_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, vlm_ocr_timeout=0.5)

    def test_vlm_ocr_timeout_too_high_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, vlm_ocr_timeout=200.0)

    def test_vlm_ocr_image_detail_invalid_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, vlm_ocr_image_detail="ultra")

    def test_vlm_ocr_max_tokens_too_low_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, vlm_ocr_max_tokens=50)

    def test_vlm_ocr_max_tokens_too_high_rejected(self):
        with pytest.raises(Exception):
            Settings(_env_file=None, vlm_ocr_max_tokens=10000)

    def test_vlm_ocr_defaults_valid(self, monkeypatch):
        monkeypatch.delenv("VLM_OCR_MODEL", raising=False)
        s = Settings(_env_file=None)
        assert s.vlm_ocr_model == "openrouter/qwen/qwen2.5-vl-32b-instruct:free"
        assert s.vlm_ocr_max_tokens == 1500
        assert s.vlm_ocr_timeout == 15.0
        assert s.vlm_ocr_image_detail == "low"


class TestSettingsProperties:
    def test_cors_origins_list(self):
        s = Settings(_env_file=None, cors_origins="http://a,http://b,http://c")
        assert s.cors_origins_list == ["http://a", "http://b", "http://c"]

    def test_ocr_languages_list(self):
        s = Settings(_env_file=None, ocr_languages="pl,en,de")
        assert s.ocr_languages_list == ["pl", "en", "de"]

    def test_empty_cors_origins(self):
        s = Settings(_env_file=None, cors_origins="")
        assert s.cors_origins_list == []


class TestSettingsFromEnv:
    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("VISION_MODEL", "gemini/gemini-2.0-flash")
        monkeypatch.setenv("PORT", "9999")
        s = Settings(_env_file=None)
        assert s.vision_model == "gemini/gemini-2.0-flash"
        assert s.port == 9999

    def test_bool_from_env(self, monkeypatch):
        monkeypatch.setenv("ENABLE_STT", "false")
        s = Settings(_env_file=None)
        assert s.enable_stt is False

    def test_float_from_env(self, monkeypatch):
        monkeypatch.setenv("VISION_TEMPERATURE", "0.7")
        s = Settings(_env_file=None)
        assert s.vision_temperature == 0.7

    def test_tts_engine_from_env(self, monkeypatch):
        monkeypatch.setenv("TTS_ENGINE", "pico2wave")
        s = Settings(_env_file=None)
        assert s.tts_engine == "pico2wave"


class TestGetSettings:
    def test_returns_settings_instance(self):
        reload_settings()  # clear cache
        s = get_settings()
        assert isinstance(s, Settings)

    def test_cached(self):
        reload_settings()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_reload_clears_cache(self):
        s1 = get_settings()
        s2 = reload_settings()
        assert isinstance(s2, Settings)
