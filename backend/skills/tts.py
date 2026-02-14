"""
TTSSkill — Detect native language text, offer text-to-speech.

Auto-detects available TTS engines on the system:
1. piper (neural, best quality, offline)
2. pico2wave (small-footprint, clear voice, offline)
3. RHVoice / flite (offline alternatives)
4. spd-say (speech-dispatcher, common on GNOME)
5. festival (medium quality)
6. espeak-ng/espeak (basic fallback, only when explicitly selected)

Falls back to whatever is installed. Generates audio and plays it.
"""
import asyncio
import os
import shlex
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

import nfo
import structlog

from settings import get_settings
from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult,
)

logger = structlog.get_logger()

# TTS engines in preference order: (name, check_command, speak_command_template)
# {text} = text to speak, {file} = output wav path, {lang} = language code
_TTS_ENGINES = [
    {
        "name": "piper",
        "check": "piper --help",
        "speak": "echo {text} | piper --model {piper_model}{speed} --output_file {file}",
        "quality": "neural",
        "tier": "high",
    },
    {
        "name": "pico2wave",
        "check": "pico2wave --help",
        "speak": "pico2wave -l {lang} -w {file} {text}",
        "quality": "high",
        "tier": "high",
    },
    {
        "name": "rhvoice",
        "check": "RHVoice-client",
        "speak_direct": "echo {text} | RHVoice-client",
        "quality": "high",
        "tier": "high",
    },
    {
        "name": "flite",
        "check": "flite --help",
        "speak": "flite -t {text} -o {file}",
        "quality": "medium",
        "tier": "high",
    },
    {
        "name": "spd-say",
        "check": "spd-say --version",
        "speak_direct": "spd-say -l {lang}{speed} -- {text}",
        "quality": "system",
        "tier": "high",
    },
    {
        "name": "festival",
        "check": "festival --version",
        "speak_direct": "echo {text} | festival --tts",
        "quality": "medium",
        "tier": "high",
    },
    {
        "name": "espeak-ng",
        "check": "espeak-ng --version",
        "speak": "espeak-ng -v {lang}{speed} -w {file} {text}",
        "quality": "basic",
        "tier": "basic",
    },
    {
        "name": "espeak",
        "check": "espeak --version",
        "speak": "espeak -v {lang}{speed} -w {file} {text}",
        "quality": "basic",
        "tier": "basic",
    },
]

_AUDIO_PLAYERS = [
    {"check": "aplay", "play": "aplay {file}"},
    {"check": "paplay", "play": "paplay {file}"},
    {"check": "ffplay", "play": "ffplay -nodisp -autoexit -loglevel error {file}"},
]

# Language code mapping for most engines
_GENERIC_LANGS = {
    "pl": "pl", "en": "en", "de": "de", "fr": "fr", "es": "es",
    "ru": "ru", "uk": "uk", "it": "it", "pt": "pt", "nl": "nl",
    "cs": "cs", "sk": "sk", "ja": "ja", "zh": "zh",
}


# pico2wave language mapping
_PICO_LANGS = {
    "de": "de-DE",
    "en": "en-US",
    "es": "es-ES",
    "fr": "fr-FR",
    "it": "it-IT",
}


def _resolve_piper_model(configured: str) -> str:
    """Return path to a piper .onnx model, if available."""
    if configured:
        explicit = os.path.expanduser(configured)
        if os.path.isfile(explicit):
            return explicit
        logger.warning("Configured piper model not found", path=explicit)

    search_dirs = [
        "~/.local/share/piper",
        "/usr/local/share/piper",
        "/usr/share/piper",
    ]
    for search_dir in search_dirs:
        root_dir = os.path.expanduser(search_dir)
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for file_name in files:
                if file_name.endswith(".onnx"):
                    return os.path.join(root, file_name)
    return ""


def _map_lang(engine_name: str, locale: str) -> str:
    base = (locale or "en").split("-")[0].lower()
    if engine_name == "pico2wave":
        return _PICO_LANGS.get(base, "en-US")
    return _GENERIC_LANGS.get(base, "en")


def _build_speed_flag(engine_name: str, slow: bool) -> str:
    if not slow:
        return ""
    if engine_name == "piper":
        return " --length_scale 1.25"
    if engine_name in {"espeak-ng", "espeak"}:
        return " -s 135"
    if engine_name == "spd-say":
        return " -r -40"
    return ""


def _pick_play_command(file_path: str) -> Optional[str]:
    quoted = shlex.quote(file_path)
    for player in _AUDIO_PLAYERS:
        if shutil.which(player["check"]):
            return player["play"].format(file=quoted)
    return None


async def _run_shell(cmd: str, timeout: float = 30.0) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    out = stdout.decode("utf-8", "ignore") if stdout else ""
    err = stderr.decode("utf-8", "ignore") if stderr else ""
    return proc.returncode, out, err


def _format_engine_command(template: str, values: Dict[str, str]) -> str:
    cmd = template
    for key, value in values.items():
        cmd = cmd.replace(f"{{{key}}}", value)
    return cmd


def detect_tts_engines() -> List[dict]:
    """Detect available TTS engines on the system."""
    settings = get_settings()
    piper_model = _resolve_piper_model(settings.tts_piper_model)

    available = []
    for engine in _TTS_ENGINES:
        check_cmd = engine["check"].split()[0]
        if not shutil.which(check_cmd):
            continue

        candidate = dict(engine)
        if candidate["name"] == "piper":
            if not piper_model:
                logger.debug("Skipping piper: no model found", hint="Set TTS_PIPER_MODEL")
                continue
            candidate["piper_model"] = piper_model

        available.append(candidate)

    return available


def get_best_tts_engine(preferred: str = "auto") -> Optional[dict]:
    """Get best available TTS engine, honoring explicit config selection."""
    engines = detect_tts_engines()
    if not engines:
        return None

    if preferred and preferred != "auto":
        for engine in engines:
            if engine["name"] == preferred:
                return engine
        logger.warning(
            "Configured TTS engine not available",
            configured=preferred,
            available=[e["name"] for e in engines],
        )
        # Fallback to auto selection if configured engine is missing.

    for engine in engines:
        if engine.get("tier") != "basic":
            return engine

    # In auto mode we intentionally avoid espeak/espeak-ng for dictation quality.
    return None


class TTSSkill(BaseSkill):
    name = "tts"
    category = SkillCategory.LANGUAGE
    icon = "🔊"
    priority = 40  # Lower than translation — only for native language

    def __init__(self):
        settings = get_settings()
        self._preferred_engine = settings.tts_engine
        self._engine = get_best_tts_engine(self._preferred_engine)
        if self._engine:
            logger.info(
                "TTS engine detected",
                preferred=self._preferred_engine,
                engine=self._engine["name"],
                quality=self._engine["quality"],
            )
        else:
            logger.info(
                "No suitable TTS engine available",
                preferred=self._preferred_engine,
                available=[e["name"] for e in detect_tts_engines()],
            )

    def detect(self, text: str, ctx: SkillContext) -> float:
        if not self._engine:
            return 0.0
        if len(text.strip()) < 10:
            return 0.0

        # Detect language
        from skills.translation import detect_language
        lang = detect_language(text)

        # TTS is most useful for native language text (read aloud)
        if lang == ctx.locale or lang == "unknown":
            word_count = len(text.split())
            if word_count >= 3:
                return 0.5  # Moderate confidence — user may want TTS
        return 0.0

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        engine_name = self._engine["name"] if self._engine else "brak"
        quality = self._engine["quality"] if self._engine else ""
        word_count = len(text.split())

        options = [
            SkillOption(
                id="speak",
                label=f"🔊 Przeczytaj na głos ({engine_name})",
                icon="🔊",
                description=f"{word_count} słów • silnik: {engine_name} ({quality})",
                data={"extracted": text[:100]},
            ),
            SkillOption(
                id="speak_slow",
                label="🐢 Przeczytaj powoli",
                icon="🐢",
                description="Wolniejsze tempo mowy",
                data={"extracted": text[:100]},
            ),
        ]

        # If multiple engines available, offer explicit alternatives.
        available = detect_tts_engines()
        if len(available) > 1:
            for eng in available:
                if self._engine and eng["name"] == self._engine["name"]:
                    continue
                options.append(SkillOption(
                    id=f"speak_{eng['name']}",
                    label=f"🔊 Użyj {eng['name']} ({eng['quality']})",
                    icon="🔊",
                    data={"engine": eng["name"], "extracted": text[:100]},
                ))

        return options

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        engine, err = self._resolve_engine(option_id)
        if err:
            return err

        lang = _map_lang(engine["name"], ctx.locale)
        slow = option_id == "speak_slow"
        speed = _build_speed_flag(engine["name"], slow)
        escaped = shlex.quote(text[:2000])
        cmd_data = {
            "text": escaped,
            "lang": lang,
            "speed": speed,
            "piper_model": shlex.quote(engine.get("piper_model", "")),
        }

        try:
            if "speak_direct" in engine:
                return await self._speak_direct(engine, cmd_data)
            return await self._speak_file_based(engine, cmd_data)
        except asyncio.TimeoutError:
            return SkillResult(success=False, message="⏱️ TTS timeout", error="timeout")
        except Exception as e:
            return SkillResult(success=False, message=f"❌ TTS error: {e}", error=str(e))

    def _resolve_engine(self, option_id: str):
        """Resolve TTS engine for the given option_id. Returns (engine, None) or (None, SkillResult)."""
        if not self._engine:
            return None, SkillResult(
                success=False,
                message=(
                    "❌ Brak wysokiej jakości silnika TTS. "
                    "Zainstaluj np.: piper / pico2wave / spd-say / rhvoice / festival"
                ),
                error="No TTS engine",
            )

        if option_id.startswith("speak_") and option_id != "speak_slow":
            engine_name = option_id.replace("speak_", "")
            for eng in detect_tts_engines():
                if eng["name"] == engine_name:
                    return eng, None
            return None, SkillResult(
                success=False,
                message=f"❌ Silnik TTS '{engine_name}' nie jest dostępny",
                error="Engine unavailable",
            )

        return self._engine, None

    async def _speak_direct(self, engine: dict, cmd_data: dict) -> SkillResult:
        """Execute TTS engine that speaks directly (no file output)."""
        cmd = _format_engine_command(engine["speak_direct"], cmd_data)
        rc, _out, err = await _run_shell(cmd)
        if rc != 0:
            return SkillResult(success=False, message="❌ TTS execution failed", error=err[:500])
        return SkillResult(success=True, message=f"🔊 Odczytano ({engine['name']})")

    async def _speak_file_based(self, engine: dict, cmd_data: dict) -> SkillResult:
        """Execute TTS engine that writes to wav file, then play."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name

        cmd_data["file"] = shlex.quote(wav_path)
        cmd = _format_engine_command(engine["speak"], cmd_data)

        rc, _out, err = await _run_shell(cmd)
        if rc != 0:
            return SkillResult(success=False, message="❌ TTS execution failed", error=err[:500])

        if not (os.path.exists(wav_path) and os.path.getsize(wav_path) > 0):
            return SkillResult(success=False, message="❌ TTS nie wygenerował dźwięku", error="Empty wav")

        play_cmd = _pick_play_command(wav_path)
        if not play_cmd:
            return SkillResult(
                success=True,
                message=f"🔊 Wygenerowano audio ({engine['name']}); brak odtwarzacza systemowego",
                audio_file=wav_path,
            )

        play_rc, _play_out, play_err = await _run_shell(play_cmd, timeout=30.0)
        if play_rc != 0:
            return SkillResult(
                success=True,
                message=f"⚠️ Wygenerowano audio ({engine['name']}), ale odtwarzanie nie powiodło się",
                audio_file=wav_path,
                error=play_err[:500],
            )

        return SkillResult(
            success=True,
            message=f"🔊 Odczytano ({engine['name']})",
            audio_file=wav_path,
        )

    def _label(self, text: str, ctx: SkillContext) -> str:
        word_count = len(text.split())
        engine = self._engine["name"] if self._engine else "brak"
        return f"Czytaj na głos ({word_count} słów, {engine})"
