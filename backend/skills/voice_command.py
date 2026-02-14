"""
VoiceCommandSkill — Detect speech commands issued during text selection.

Monitors latest_transcript from STT and matches against voice command patterns.
When user selects text AND says a command like "przetłumacz", "uruchom", "kopiuj",
the voice command takes priority and executes the appropriate skill.

Voice commands (Polish):
- "przetłumacz" / "translate" → trigger TranslationSkill
- "uruchom" / "wykonaj" / "run" → trigger ShellCommandSkill
- "przeczytaj" / "czytaj" / "read" → trigger TTSSkill
- "kopiuj" / "copy" → copy to clipboard
- "szukaj" / "search" → search online
- "otwórz" / "open" → open URL/path
"""
import re
import time
from typing import List, Optional

from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult, OptionRisk,
)

# Voice command patterns: (regex, action_id, label, icon)
VOICE_COMMANDS = [
    (r"\b(przetłumacz|translate|tłumacz)\b", "translate", "🌐 Przetłumacz", "🌐"),
    (r"\b(uruchom|wykonaj|run|exec|odpal)\b", "run", "▶ Uruchom", "▶"),
    (r"\b(przeczytaj|czytaj|read|mów|powiedz)\b", "speak", "🔊 Przeczytaj", "🔊"),
    (r"\b(kopiuj|copy|skopiuj)\b", "copy", "📋 Kopiuj", "📋"),
    (r"\b(szukaj|search|wyszukaj|google)\b", "search", "🔍 Szukaj", "🔍"),
    (r"\b(otwórz|open|otworz)\b", "open", "📂 Otwórz", "📂"),
    (r"\b(zapisz|save|zachowaj)\b", "save", "💾 Zapisz", "💾"),
    (r"\b(wyjaśnij|explain|opisz)\b", "explain", "💡 Wyjaśnij", "💡"),
]

_COMPILED_COMMANDS = [(re.compile(p, re.IGNORECASE), aid, label, icon) for p, aid, label, icon in VOICE_COMMANDS]

# Maximum age of transcript to consider (seconds)
_MAX_TRANSCRIPT_AGE = 10.0


class VoiceCommandSkill(BaseSkill):
    name = "voice_command"
    category = SkillCategory.VOICE
    icon = "🎤"
    priority = 95  # Highest — voice commands override other skills

    def detect(self, text: str, ctx: SkillContext) -> float:
        if not ctx.latest_transcript:
            return 0.0

        transcript = ctx.latest_transcript.lower().strip()
        if len(transcript) < 3:
            return 0.0

        # Check if any voice command matches
        for compiled, action_id, _, _ in _COMPILED_COMMANDS:
            if compiled.search(transcript):
                return 0.95  # Very high — voice command overrides

        return 0.0

    def _match_command(self, transcript: str) -> Optional[tuple]:
        """Find the matching voice command."""
        transcript = transcript.lower().strip()
        for compiled, action_id, label, icon in _COMPILED_COMMANDS:
            if compiled.search(transcript):
                return action_id, label, icon
        return None

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        match = self._match_command(ctx.latest_transcript)
        if not match:
            return []

        action_id, label, icon = match
        transcript = ctx.latest_transcript.strip()

        options = [
            SkillOption(
                id=action_id,
                label=f"🎤 {label} (głos: \"{transcript[:30]}\")",
                icon=icon,
                description=f"Komenda głosowa: {transcript}",
                data={"voice_command": action_id, "transcript": transcript, "extracted": text[:100]},
            ),
        ]

        # Always offer cancel
        options.append(SkillOption(
            id="cancel",
            label="✕ Anuluj komendę głosową",
            icon="✕",
            data={"extracted": text[:100]},
        ))

        return options

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        if option_id == "cancel":
            return SkillResult(success=True, message="✕ Komenda anulowana")

        if option_id == "copy":
            return SkillResult(success=True, message="📋 Skopiowano", clipboard_text=text)

        if option_id == "search":
            query = text[:80].replace(" ", "+")
            url = f"https://www.google.com/search?q={query}"
            return SkillResult(success=True, message=f"🔍 Szukam...", open_url=url, clipboard_text=text)

        if option_id == "save":
            import tempfile, os
            path = os.path.join(tempfile.gettempdir(), "aidesk_saved.txt")
            with open(path, "w") as f:
                f.write(text)
            return SkillResult(success=True, message=f"💾 Zapisano: {path}", clipboard_text=path)

        # Delegate to other skills
        if option_id == "translate":
            try:
                from skills.translation import TranslationSkill
                skill = TranslationSkill()
                return await skill.execute(text, f"translate_{ctx.locale}", ctx)
            except Exception as e:
                return SkillResult(success=False, error=str(e))

        if option_id == "run":
            try:
                from skills.shell_command import ShellCommandSkill
                skill = ShellCommandSkill()
                return await skill.execute(text, "run_cwd", ctx)
            except Exception as e:
                return SkillResult(success=False, error=str(e))

        if option_id == "speak":
            try:
                from skills.tts import TTSSkill
                skill = TTSSkill()
                return await skill.execute(text, "speak", ctx)
            except Exception as e:
                return SkillResult(success=False, error=str(e))

        if option_id == "open":
            import re as _re
            url_match = _re.search(r"https?://[^\s]+", text)
            if url_match:
                return SkillResult(success=True, message="📂 Otwieram...", open_url=url_match.group(0))
            path_match = _re.search(r"/[\w/.@+-]+", text)
            if path_match:
                return SkillResult(success=True, message=f"📂 Ścieżka: {path_match.group(0)}", clipboard_text=path_match.group(0))
            return SkillResult(success=False, message="❌ Nie znaleziono URL ani ścieżki")

        if option_id == "explain":
            return SkillResult(
                success=True,
                message=f"💡 Zaznaczony tekst ({len(text.split())} słów, {text.count(chr(10))+1} linii)",
                clipboard_text=text,
            )

        return SkillResult(success=False, error=f"Unknown option: {option_id}")

    def _label(self, text: str, ctx: SkillContext) -> str:
        match = self._match_command(ctx.latest_transcript)
        if match:
            _, label, _ = match
            return f"Komenda głosowa: {label}"
        return "Komenda głosowa"
