"""
Configuration service — .env read/write + audio device discovery.

Provides:
- EnvConfig: parse/update/save .env file preserving comments
- AudioDeviceScanner: discover PulseAudio/PipeWire sources & sinks via pactl + sounddevice
"""
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# ─── .env file management ───────────────────────────────────────────

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def _parse_env_file(path: str) -> Tuple[List[str], Dict[str, str]]:
    """
    Parse .env file into raw lines + key→value dict.
    Preserves comments and blank lines for faithful rewrite.
    """
    lines: List[str] = []
    values: Dict[str, str] = {}

    if not os.path.exists(path):
        return lines, values

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped)
            if match:
                values[match.group(1)] = match.group(2)

    return lines, values


def read_env() -> Dict[str, str]:
    """Read current .env values as dict."""
    _, values = _parse_env_file(ENV_PATH)
    return values


def update_env(updates: Dict[str, str]) -> Dict[str, str]:
    """
    Update .env file with new values, preserving comments and order.
    New keys are appended at the end.

    Returns the full config after update.
    """
    lines, current = _parse_env_file(ENV_PATH)

    updated_keys = set()

    # Update existing lines in-place
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped)
            if match and match.group(1) in updates:
                key = match.group(1)
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append new keys that weren't in the file
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"\n{key}={value}\n")

    # Write back
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Re-read to return accurate state
    _, final = _parse_env_file(ENV_PATH)

    # Also update os.environ so changes take effect immediately
    for key, value in updates.items():
        os.environ[key] = value

    logger.info("Config updated", keys=list(updates.keys()))
    return final


# ─── Audio device discovery ──────────────────────────────────────────

@dataclass
class AudioDevice:
    """Single audio device (source or sink)."""
    id: str
    name: str
    description: str
    driver: str
    sample_spec: str
    state: str
    device_type: str  # "source" | "sink"
    is_monitor: bool = False  # True for .monitor sources (speaker loopback)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "driver": self.driver,
            "sample_spec": self.sample_spec,
            "state": self.state,
            "device_type": self.device_type,
            "is_monitor": self.is_monitor,
        }


def _run_pactl(args: List[str]) -> Optional[str]:
    """Run pactl command, return stdout or None."""
    try:
        result = subprocess.run(
            ["pactl"] + args,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _parse_pactl_list(output: str, device_type: str) -> List[AudioDevice]:
    """Parse 'pactl list sources/sinks' verbose output."""
    devices: List[AudioDevice] = []
    current: Dict[str, str] = {}

    for line in output.splitlines():
        # New device block
        if line.startswith(("Source #", "Sink #")):
            if current.get("name"):
                devices.append(_build_device(current, device_type))
            current = {"id": line.split("#")[-1].strip()}
        elif ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key == "name":
                current["name"] = val
            elif key == "description":
                current["description"] = val
            elif key == "driver name":
                current["driver"] = val
            elif key == "sample specification":
                current["sample_spec"] = val
            elif key == "state":
                current["state"] = val

    # Last device
    if current.get("name"):
        devices.append(_build_device(current, device_type))

    return devices


def _build_device(d: Dict[str, str], device_type: str) -> AudioDevice:
    name = d.get("name", "")
    return AudioDevice(
        id=d.get("id", ""),
        name=name,
        description=d.get("description", name),
        driver=d.get("driver", ""),
        sample_spec=d.get("sample_spec", ""),
        state=d.get("state", "UNKNOWN"),
        device_type=device_type,
        is_monitor=name.endswith(".monitor"),
    )


def _discover_pactl() -> Tuple[List[AudioDevice], List[AudioDevice]]:
    """Discover audio devices via pactl (PulseAudio/PipeWire)."""
    sources: List[AudioDevice] = []
    sinks: List[AudioDevice] = []

    src_output = _run_pactl(["list", "sources"])
    if src_output:
        sources = _parse_pactl_list(src_output, "source")

    sink_output = _run_pactl(["list", "sinks"])
    if sink_output:
        sinks = _parse_pactl_list(sink_output, "sink")

    return sources, sinks


def _discover_sounddevice() -> List[dict]:
    """Discover audio devices via sounddevice (fallback)."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            result.append({
                "index": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "max_output_channels": d["max_output_channels"],
                "default_samplerate": d["default_samplerate"],
            })
        return result
    except Exception as e:
        logger.warning("sounddevice discovery failed", error=str(e))
        return []


def discover_audio_devices() -> dict:
    """
    Discover all audio devices.
    Returns structured dict with sources (inputs), sinks (outputs),
    monitors (speaker loopback for STT), and sounddevice fallback.
    """
    sources, sinks = _discover_pactl()

    # Classify sources
    microphones = [s for s in sources if not s.is_monitor]
    monitors = [s for s in sources if s.is_monitor]

    # sounddevice as fallback info
    sd_devices = _discover_sounddevice()

    # Current config from .env
    env = read_env()

    return {
        "microphones": [d.to_dict() for d in microphones],
        "monitors": [d.to_dict() for d in monitors],
        "speakers": [d.to_dict() for d in sinks],
        "sounddevice": sd_devices,
        "current": {
            "stt_input_device": env.get("STT_INPUT_DEVICE", ""),
            "stt_monitor_device": env.get("STT_MONITOR_DEVICE", ""),
            "audio_output_device": env.get("AUDIO_OUTPUT_DEVICE", ""),
        },
    }


# ─── Config schema (for UI grouping) ────────────────────────────────

CONFIG_SCHEMA = [
    {
        "group": "🔊 Audio / STT",
        "icon": "audio",
        "fields": [
            {"key": "STT_INPUT_DEVICE", "label": "Mikrofon (źródło wejściowe)", "type": "audio_source", "help": "Urządzenie nagrywania głosu dla STT. Puste = domyślne systemowe."},
            {"key": "STT_MONITOR_DEVICE", "label": "Monitor głośnika (loopback)", "type": "audio_monitor", "help": "Przechwytywanie dźwięku z głośnika do analizy STT (np. transkrypcja rozmów)."},
            {"key": "AUDIO_OUTPUT_DEVICE", "label": "Głośnik (wyjście audio)", "type": "audio_sink", "help": "Domyślne urządzenie wyjściowe audio."},
            {"key": "ENABLE_STT", "label": "Włącz STT", "type": "bool", "help": "Speech-to-Text (wymaga DEEPGRAM_API_KEY)."},
            {"key": "STT_LANGUAGE", "label": "Język STT", "type": "select", "options": ["pl", "en", "de", "fr", "es", "it", "pt", "nl", "ja", "ko", "zh"], "help": "Język rozpoznawania mowy."},
            {"key": "DEEPGRAM_MODEL", "label": "Model STT", "type": "select", "options": ["nova-3", "nova-2", "enhanced", "base"], "help": "Model Deepgram."},
            {"key": "DEEPGRAM_API_KEY", "label": "Deepgram API Key", "type": "password", "help": "Klucz API Deepgram (deepgram.com)."},
        ],
    },
    {
        "group": "🤖 Vision / AI Model",
        "icon": "ai",
        "fields": [
            {"key": "VISION_MODEL", "label": "Model Vision", "type": "text", "help": "Format: provider/model (np. ollama/llava, gemini/gemini-2.0-flash)."},
            {"key": "ANALYSIS_MODE", "label": "Tryb analizy", "type": "select", "options": ["vision_only", "ocr_only", "hybrid", "ocr_plus_vision"], "help": "hybrid = OCR→LLM (rekomendowany)."},
            {"key": "VISION_MAX_TOKENS", "label": "Max tokenów", "type": "number", "help": "Limit tokenów odpowiedzi."},
            {"key": "VISION_TEMPERATURE", "label": "Temperatura", "type": "number", "help": "0.0-1.0, niższa = bardziej deterministyczny."},
            {"key": "ENABLE_VISION", "label": "Włącz Vision AI", "type": "bool", "help": "Analiza wizualna ekranu."},
            {"key": "LITELLM_API_BASE", "label": "Custom API Base", "type": "text", "help": "URL dla LM Studio / vLLM (np. http://localhost:1234/v1)."},
        ],
    },
    {
        "group": "🔤 OCR",
        "icon": "ocr",
        "fields": [
            {"key": "OCR_ENGINE", "label": "Silnik OCR", "type": "select", "options": ["paddleocr", "easyocr", "tesseract"], "help": "PaddleOCR = najszybszy, EasyOCR = najdokładniejszy."},
            {"key": "OCR_LANGUAGES", "label": "Języki OCR", "type": "text", "help": "Oddzielone przecinkami (np. pl,en)."},
            {"key": "OCR_USE_GPU", "label": "GPU dla OCR", "type": "bool", "help": "Wymaga CUDA."},
            {"key": "ENABLE_OCR", "label": "Włącz OCR", "type": "bool", "help": "Pre-processing tekstu z ekranu."},
        ],
    },
    {
        "group": "🔑 Klucze API",
        "icon": "keys",
        "fields": [
            {"key": "GEMINI_API_KEY", "label": "Google Gemini", "type": "password", "help": "makersuite.google.com/app/apikey"},
            {"key": "OPENAI_API_KEY", "label": "OpenAI", "type": "password", "help": "platform.openai.com/api-keys"},
            {"key": "ANTHROPIC_API_KEY", "label": "Anthropic Claude", "type": "password", "help": "console.anthropic.com"},
            {"key": "GROQ_API_KEY", "label": "Groq", "type": "password", "help": "console.groq.com/keys"},
            {"key": "DEEPSEEK_API_KEY", "label": "DeepSeek", "type": "password", "help": "platform.deepseek.com/api_keys"},
            {"key": "MISTRAL_API_KEY", "label": "Mistral", "type": "password", "help": "console.mistral.ai/api-keys"},
        ],
    },
    {
        "group": "⚡ Wydajność",
        "icon": "performance",
        "fields": [
            {"key": "CHANGE_THRESHOLD", "label": "Próg zmian (1-20)", "type": "number", "help": "Niższy = więcej analiz. Rekomendacja: 8."},
            {"key": "MIN_CAPTURE_INTERVAL", "label": "Interwał capture (s)", "type": "number", "help": "Jak często robić zrzuty."},
            {"key": "IDLE_THRESHOLD", "label": "Próg idle (klatki)", "type": "number", "help": "Po ilu niezmiennych klatkach zmniejszyć częstotliwość."},
            {"key": "IDLE_INTERVAL", "label": "Interwał idle (s)", "type": "number", "help": "Interwał po wejściu w tryb idle."},
            {"key": "MAX_DIMENSION", "label": "Max wymiar (px)", "type": "number", "help": "Najdłuższy bok zrzutu. 1280 = rekomendacja."},
            {"key": "JPEG_QUALITY", "label": "Jakość JPEG (1-100)", "type": "number", "help": "60 = dobry balans."},
        ],
    },
    {
        "group": "🖥️ Funkcje",
        "icon": "features",
        "fields": [
            {"key": "ENABLE_WINDOW_AWARE", "label": "Window Awareness", "type": "bool", "help": "Detekcja aktywnego okna."},
            {"key": "ENABLE_SHELL_AGENT", "label": "Shell Agent", "type": "bool", "help": "Sugerowane akcje + wykonywanie komend."},
            {"key": "CAPTURE_MODE", "label": "Tryb capture", "type": "select", "options": ["fullscreen", "window"], "help": "fullscreen = cały ekran, window = aktywne okno."},
            {"key": "ENABLE_GIT_CONTEXT", "label": "Git Context", "type": "bool", "help": "Branch/status dla IDE/Terminal."},
            {"key": "AGENT_AUTO_EXECUTE", "label": "Auto-execute", "type": "bool", "help": "Auto-execute bezpiecznych komend."},
            {"key": "AGENT_TIMEOUT", "label": "Timeout agenta (s)", "type": "number", "help": "Timeout dla komend agenta."},
        ],
    },
    {
        "group": "🌐 Serwer",
        "icon": "server",
        "fields": [
            {"key": "PORT", "label": "Port", "type": "number", "help": "Port backendu."},
            {"key": "HOST", "label": "Host", "type": "text", "help": "0.0.0.0 = sieć, 127.0.0.1 = localhost."},
            {"key": "LOG_LEVEL", "label": "Log level", "type": "select", "options": ["DEBUG", "INFO", "WARNING", "ERROR"], "help": "Poziom logowania."},
            {"key": "DEBUG", "label": "Debug mode", "type": "bool", "help": "Verbose logging + auto-reload."},
        ],
    },
]


def get_config_with_schema() -> dict:
    """
    Return full config: current values + schema for UI rendering + audio devices.
    """
    values = read_env()
    audio = discover_audio_devices()

    return {
        "values": values,
        "schema": CONFIG_SCHEMA,
        "audio": audio,
        "env_path": ENV_PATH,
    }
