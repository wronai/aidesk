"""
Typed Configuration — Pydantic Settings for validated, documented config.

Replaces scattered os.getenv() calls with a single validated settings object.
Reads from .env file + environment variables (env vars take precedence).

Usage:
    from settings import get_settings

    settings = get_settings()
    print(settings.vision_model)        # "ollama/llava"
    print(settings.port)                # 8000
    print(settings.enable_stt)          # True

Benefits over raw os.getenv():
- Type validation at startup (fail-fast on bad config)
- Default values documented in one place
- IDE autocompletion
- Nested grouping (vision, ocr, agent, etc.)
"""
import os
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings — loaded from .env + environment variables.

    All fields have sensible defaults matching .env.example.
    Environment variables override .env file values.
    """

    # ===== Optimization Strategy =====
    optimization_priority: str = Field("auto", description="auto|budget|speed|quality")
    hardware_profile: str = Field("auto", description="auto|gpu_high|gpu_low|cpu_only")

    # Extended budget
    budget_hourly_limit: float = Field(0.50, ge=0.0)
    budget_daily_limit: float = Field(5.00, ge=0.0)
    budget_warning_pct: int = Field(80, ge=0, le=100)
    budget_critical_pct: int = Field(95, ge=0, le=100)
    budget_fallback_chain: str = Field("ocr_plus_vision,hybrid,ocr_only")

    # Model routing
    vision_model_primary: str = Field("", description="Primary vision model (empty = use VISION_MODEL)")
    vision_model_fallback: str = Field("", description="Cheaper fallback model")
    vision_model_emergency: str = Field("", description="Zero-cost emergency fallback")

    # Local vs Cloud preferences
    prefer_local_ocr: str = Field("auto", description="auto|always|never")
    prefer_local_llm: str = Field("auto", description="auto|always|never")
    local_llm_endpoint: str = Field("", description="e.g. http://localhost:11434/v1")
    local_ocr_max_latency_ms: int = Field(500, ge=50)
    cloud_ocr_cost_threshold: float = Field(0.001, ge=0.0)
    max_tick_latency_ms: int = Field(5000, ge=500)

    # ===== Vision Model (LiteLLM) =====
    vision_model: str = Field("ollama/llava", description="LiteLLM model identifier")
    litellm_api_base: str = Field("", description="Custom API base URL for local servers")
    litellm_api_key: str = Field("", description="API key override")
    vision_max_tokens: int = Field(400, ge=1, le=8192)
    vision_temperature: float = Field(0.3, ge=0.0, le=2.0)
    vision_image_detail: str = Field("low", pattern="^(low|high|auto)$")

    # ===== Analysis Mode =====
    analysis_mode: str = Field("hybrid", description="vision_only|ocr_only|hybrid|ocr_plus_vision")

    # ===== OCR =====
    ocr_engine: str = Field("paddleocr", description="paddleocr|easyocr|tesseract|vlm_ocr")
    ocr_languages: str = Field("pl,en")
    ocr_use_gpu: bool = False
    enable_ocr: bool = True

    # ===== VLM OCR (Cloud-based OCR via OpenRouter/LiteLLM) =====
    vlm_ocr_model: str = Field(
        "openrouter/qwen/qwen2.5-vl-32b-instruct:free",
        description="LiteLLM model string for VLM OCR engine",
    )
    vlm_ocr_max_tokens: int = Field(1500, ge=100, le=8000)
    vlm_ocr_timeout: float = Field(15.0, ge=1.0, le=120.0)
    vlm_ocr_image_detail: str = Field("low", pattern="^(low|high|auto)$")

    # ===== API Keys =====
    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    mistral_api_key: str = ""
    deepgram_api_key: str = ""

    # ===== STT =====
    stt_language: str = "pl"
    deepgram_model: str = "nova-3"
    stt_input_device: str = ""
    stt_monitor_device: str = ""
    audio_output_device: str = ""

    # ===== TTS =====
    tts_engine: str = Field(
        "auto",
        description="auto|piper|pico2wave|rhvoice|flite|spd-say|festival|espeak-ng|espeak",
    )
    tts_piper_model: str = Field(
        "",
        description="Optional path to piper voice model (.onnx)",
    )

    # ===== Performance =====
    change_threshold: int = Field(8, ge=1, le=20)
    min_capture_interval: float = Field(1.0, ge=0.1)
    idle_threshold: int = Field(30, ge=1)
    idle_interval: float = Field(10.0, ge=1.0)
    screen_width: int = Field(1280, ge=320)
    screen_height: int = Field(720, ge=240)
    max_dimension: int = Field(1280, ge=320, description="Max dimension for aspect-ratio resize")
    jpeg_quality: int = Field(60, ge=1, le=100)

    # ===== Feature Flags =====
    enable_stt: bool = True
    enable_vision: bool = True
    enable_window_aware: bool = True
    enable_shell_agent: bool = True
    capture_mode: str = Field("fullscreen", pattern="^(fullscreen|window)$")
    debug: bool = False

    # ===== Window Awareness =====
    enable_git_context: bool = True
    git_timeout: float = Field(2.0, ge=0.1)
    window_cache_ttl: float = Field(0.5, ge=0.0)

    # ===== Shell Agent =====
    agent_auto_execute: bool = True
    agent_max_output: int = Field(2000, ge=100)
    agent_timeout: float = Field(10.0, ge=1.0)

    # ===== Process Scanner & Window Cropper =====
    crops_dir: str = "/tmp/proxeen_crops"
    min_window_size: int = Field(100, ge=10)
    max_crop_windows: int = Field(0, ge=0)
    crop_change_threshold: float = Field(3.0, ge=0.1)

    # ===== Clipboard Intelligence =====
    clipboard_max_items: int = Field(20, ge=1)

    # ===== Pipeline Profiles =====
    pipeline_profile: str = Field("", description="auto|fast|normal|full (empty=auto)")
    pipeline_full_interval: float = Field(60.0, ge=5.0)
    scan_cache_ttl: float = Field(3.0, ge=0.0)

    # ===== Disk I/O =====
    save_captures: bool = True
    save_crops: bool = True
    captures_dir: str = Field("/tmp/proxeen_captures", description="Directory for screenshots")

    # ===== Rate Limiting =====
    max_vision_calls_per_minute: int = Field(30, ge=1)
    max_stt_calls_per_minute: int = Field(60, ge=1)
    rate_limit_tokens: int = Field(5, ge=1)
    rate_limit_refill_rate: float = Field(1.0, ge=0.1)

    # ===== Cost Budget =====
    daily_budget: float = Field(5.0, ge=0.1)
    hourly_budget: float = Field(1.0, ge=0.1)

    # ===== Context Management =====
    max_context_items: int = Field(20, ge=1)
    context_summary_interval: int = Field(50, ge=5)

    # ===== Server =====
    port: int = Field(8000, ge=1, le=65535)
    host: str = "127.0.0.1"
    cors_origins: str = "http://localhost:*,http://127.0.0.1:*"
    log_level: str = Field("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    log_file: str = "logs/assistant.log"
    usage_log_file: str = "logs/usage.log"

    # ===== Diagnostics =====
    diag_interval: float = Field(30.0, ge=5.0)

    # ===== Multi-Monitor =====
    multi_monitor_active_only: bool = True
    multi_monitor_description: bool = True

    # ===== Semantic Memory =====
    enable_semantic_memory: bool = True
    semantic_model: str = ""
    semantic_memory_db: str = "logs/semantic_memory.db"
    semantic_max_memories: int = Field(5000, ge=100)
    semantic_recall_k: int = Field(3, ge=1)
    semantic_threshold: float = Field(0.3, ge=0.0, le=1.0)
    semantic_compress_hours: float = Field(1.0, ge=0.1)

    # ===== Action Templates =====
    enable_action_templates: bool = True
    action_templates_db: str = "logs/action_templates.db"
    action_auto_approve_after: int = Field(3, ge=0)

    # ===== OCR Post-Processing =====
    enable_ocr_post_process: bool = True
    ocr_spell_check: bool = True
    ocr_spell_dict: str = ""
    ocr_max_edit_distance: int = Field(2, ge=1, le=5)

    # ===== Predictive Pre-Fetching =====
    enable_predictive: bool = True
    predictive_threshold: float = Field(0.6, ge=0.0, le=1.0)
    predictive_max_history: int = Field(1000, ge=10)
    predictive_prefetch_ttl: float = Field(10.0, ge=1.0)
    predictive_min_obs: int = Field(3, ge=1)

    # ===== Event Sourcing =====
    enable_event_store: bool = True
    event_store_db: str = "logs/events.db"

    # ===== Circuit Breaker (Tier 1) =====
    analyze_circuit_threshold: int = Field(5, ge=1)
    analyze_circuit_reset: float = Field(60.0, ge=5.0)
    analyze_max_retries: int = Field(2, ge=0)

    @field_validator("optimization_priority")
    @classmethod
    def validate_optimization_priority(cls, v):
        valid = {"auto", "budget", "speed", "quality"}
        if v not in valid:
            raise ValueError(f"optimization_priority must be one of {valid}, got '{v}'")
        return v

    @field_validator("hardware_profile")
    @classmethod
    def validate_hardware_profile(cls, v):
        valid = {"auto", "gpu_high", "gpu_low", "cpu_only"}
        if v not in valid:
            raise ValueError(f"hardware_profile must be one of {valid}, got '{v}'")
        return v

    @field_validator("prefer_local_ocr")
    @classmethod
    def validate_prefer_local_ocr(cls, v):
        valid = {"auto", "always", "never"}
        if v not in valid:
            raise ValueError(f"prefer_local_ocr must be one of {valid}, got '{v}'")
        return v

    @field_validator("prefer_local_llm")
    @classmethod
    def validate_prefer_local_llm(cls, v):
        valid = {"auto", "always", "never"}
        if v not in valid:
            raise ValueError(f"prefer_local_llm must be one of {valid}, got '{v}'")
        return v

    @field_validator("analysis_mode")
    @classmethod
    def validate_analysis_mode(cls, v):
        valid = {"vision_only", "ocr_only", "hybrid", "ocr_plus_vision"}
        if v not in valid:
            raise ValueError(f"analysis_mode must be one of {valid}, got '{v}'")
        return v

    @field_validator("ocr_engine")
    @classmethod
    def validate_ocr_engine(cls, v):
        valid = {"paddleocr", "easyocr", "tesseract", "vlm_ocr"}
        if v not in valid:
            raise ValueError(f"ocr_engine must be one of {valid}, got '{v}'")
        return v

    @field_validator("tts_engine")
    @classmethod
    def validate_tts_engine(cls, v):
        valid = {
            "auto",
            "piper",
            "pico2wave",
            "rhvoice",
            "flite",
            "spd-say",
            "festival",
            "espeak-ng",
            "espeak",
        }
        if v not in valid:
            raise ValueError(f"tts_engine must be one of {valid}, got '{v}'")
        return v

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS comma-separated string into list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ocr_languages_list(self) -> List[str]:
        """Parse OCR_LANGUAGES comma-separated string into list."""
        return [l.strip() for l in self.ocr_languages.split(",") if l.strip()]

    model_config = {
        "env_file": os.path.join(os.path.dirname(__file__), ".env"),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached application settings.

    First call reads from .env + env vars and validates.
    Subsequent calls return the cached instance.
    """
    return Settings()


def reload_settings() -> Settings:
    """Force reload settings (clears cache)."""
    get_settings.cache_clear()
    return get_settings()
