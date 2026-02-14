"""Tests for the Skill system — SkillRouter, individual skills, detection, options, execution."""
import asyncio
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.base import BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult, SkillMatch, OptionRisk
from skills import SkillRouter
from skills.shell_command import ShellCommandSkill
from skills.error_fixer import ErrorFixerSkill
from skills.translation import TranslationSkill, detect_language
from skills.tts import TTSSkill, detect_tts_engines
from skills.voice_command import VoiceCommandSkill
from skills.url_handler import URLHandlerSkill


def _ctx(**kwargs):
    return SkillContext(**{"locale": "pl", **kwargs})


# ===== SkillRouter =====

class TestSkillRouter:
    def test_init(self):
        router = SkillRouter()
        assert len(router.get_skill_names()) >= 5

    def test_analyze_shell_command(self):
        router = SkillRouter()
        matches = router.analyze("git status --short", _ctx())
        assert len(matches) >= 1
        assert matches[0].skill_name == "shell_command"
        assert matches[0].confidence > 0.5
        assert len(matches[0].options) >= 2

    def test_analyze_error(self):
        router = SkillRouter()
        matches = router.analyze("ModuleNotFoundError: No module named 'flask'", _ctx())
        # Should match both error_fixer and possibly shell_command
        skills = [m.skill_name for m in matches]
        assert "error_fixer" in skills

    def test_analyze_url(self):
        router = SkillRouter()
        matches = router.analyze("Check https://docs.python.org/3/", _ctx())
        skills = [m.skill_name for m in matches]
        assert "url_handler" in skills

    def test_analyze_english_text(self):
        router = SkillRouter()
        text = "The quick brown fox jumps over the lazy dog and the weather is nice today"
        matches = router.analyze(text, _ctx(locale="pl"))
        skills = [m.skill_name for m in matches]
        assert "translation" in skills

    def test_analyze_returns_sorted_by_confidence(self):
        router = SkillRouter()
        matches = router.analyze("git push origin main", _ctx())
        for i in range(len(matches) - 1):
            assert matches[i].confidence >= matches[i + 1].confidence

    def test_analyze_empty_text(self):
        router = SkillRouter()
        matches = router.analyze("", _ctx())
        assert matches == []

    @pytest.mark.asyncio
    async def test_execute_copy(self):
        router = SkillRouter()
        result = await router.execute("shell_command", "ls -la", "copy", _ctx())
        assert result.success
        assert result.clipboard_text == "ls -la"

    @pytest.mark.asyncio
    async def test_execute_unknown_skill(self):
        router = SkillRouter()
        result = await router.execute("nonexistent", "text", "opt", _ctx())
        assert not result.success

    def test_get_stats(self):
        router = SkillRouter()
        stats = router.get_stats()
        assert stats["total_skills"] >= 5
        assert isinstance(stats["skills"], list)

    def test_register_custom_skill(self):
        router = SkillRouter()
        initial = len(router.get_skill_names())

        class CustomSkill(BaseSkill):
            name = "custom_test"
            category = SkillCategory.CUSTOM
            priority = 10
            def detect(self, text, ctx): return 0.0
            def get_options(self, text, ctx): return []
            async def execute(self, text, option_id, ctx): return SkillResult()

        router.register_skill(CustomSkill())
        assert len(router.get_skill_names()) == initial + 1


# ===== ShellCommandSkill =====

class TestShellCommandSkill:
    def test_detect_git(self):
        skill = ShellCommandSkill()
        assert skill.detect("git push origin main", _ctx()) > 0.5

    def test_detect_docker(self):
        skill = ShellCommandSkill()
        assert skill.detect("docker compose up -d", _ctx()) > 0.5

    def test_detect_pip(self):
        skill = ShellCommandSkill()
        assert skill.detect("pip install flask", _ctx()) > 0.5

    def test_detect_sudo(self):
        skill = ShellCommandSkill()
        assert skill.detect("sudo apt update", _ctx()) > 0.5

    def test_no_detect_plain_text(self):
        skill = ShellCommandSkill()
        assert skill.detect("Hello world this is a test", _ctx()) == 0.0

    def test_options_include_run_and_copy(self):
        skill = ShellCommandSkill()
        options = skill.get_options("git status", _ctx())
        ids = [o.id for o in options]
        assert "run_cwd" in ids
        assert "copy" in ids

    def test_risk_assessment_sudo(self):
        skill = ShellCommandSkill()
        options = skill.get_options("sudo rm -rf /tmp/old", _ctx())
        run_opt = next(o for o in options if o.id == "run_cwd")
        assert run_opt.risk == OptionRisk.MEDIUM

    @pytest.mark.asyncio
    async def test_execute_copy(self):
        skill = ShellCommandSkill()
        result = await skill.execute("echo hello", "copy", _ctx())
        assert result.success
        assert result.clipboard_text == "echo hello"

    def test_multiline_script_option(self):
        skill = ShellCommandSkill()
        script = "echo step1\necho step2\necho step3"
        options = skill.get_options(script, _ctx())
        ids = [o.id for o in options]
        assert "save_script" in ids


# ===== ErrorFixerSkill =====

class TestErrorFixerSkill:
    def test_detect_module_not_found(self):
        skill = ErrorFixerSkill()
        assert skill.detect("ModuleNotFoundError: No module named 'requests'", _ctx()) > 0.5

    def test_detect_node_module(self):
        skill = ErrorFixerSkill()
        assert skill.detect("Cannot find module 'express'", _ctx()) > 0.5

    def test_detect_git_error(self):
        skill = ErrorFixerSkill()
        assert skill.detect("fatal: unable to push to remote", _ctx()) > 0.5

    def test_detect_no_space(self):
        skill = ErrorFixerSkill()
        assert skill.detect("No space left on device", _ctx()) > 0.5

    def test_no_detect_clean_text(self):
        skill = ErrorFixerSkill()
        assert skill.detect("Everything is working fine", _ctx()) == 0.0

    def test_options_include_fix(self):
        skill = ErrorFixerSkill()
        options = skill.get_options("ModuleNotFoundError: No module named 'flask'", _ctx())
        ids = [o.id for o in options]
        assert "fix" in ids

    def test_fix_command_expansion(self):
        skill = ErrorFixerSkill()
        options = skill.get_options("ModuleNotFoundError: No module named 'flask'", _ctx())
        fix_opt = next(o for o in options if o.id == "fix")
        assert "pip install flask" in fix_opt.data.get("command", "")

    @pytest.mark.asyncio
    async def test_execute_copy_fix(self):
        skill = ErrorFixerSkill()
        result = await skill.execute(
            "ModuleNotFoundError: No module named 'flask'", "copy_fix", _ctx()
        )
        assert result.success
        assert "pip install flask" in result.clipboard_text

    @pytest.mark.asyncio
    async def test_execute_search(self):
        skill = ErrorFixerSkill()
        result = await skill.execute("TypeError: bad operand", "search", _ctx())
        assert result.success
        assert result.open_url.startswith("https://")


# ===== TranslationSkill =====

class TestTranslationSkill:
    def test_detect_language_english(self):
        assert detect_language("The quick brown fox jumps over the lazy dog and the weather is nice today with some clouds in the sky") == "en"

    def test_detect_language_german(self):
        assert detect_language("Der schnelle braune Fuchs ist nicht auf dem Tisch") == "de"

    def test_detect_language_polish(self):
        assert detect_language("To jest bardzo ważna informacja dla tego projektu") == "pl"

    def test_detect_language_short_text(self):
        assert detect_language("hi") == "unknown"

    def test_skill_detect_foreign(self):
        skill = TranslationSkill()
        text = "The weather is nice today and the birds are singing in the garden with their beautiful voices"
        assert skill.detect(text, _ctx(locale="pl")) > 0.5

    def test_skill_no_detect_native(self):
        skill = TranslationSkill()
        text = "To jest bardzo ważna informacja dla tego projektu"
        assert skill.detect(text, _ctx(locale="pl")) == 0.0

    def test_options_include_translate(self):
        skill = TranslationSkill()
        text = "The quick brown fox jumps over the lazy dog and runs away from here"
        options = skill.get_options(text, _ctx(locale="pl"))
        ids = [o.id for o in options]
        assert "translate_pl" in ids

    @pytest.mark.asyncio
    async def test_execute_copy(self):
        skill = TranslationSkill()
        result = await skill.execute("Hello world", "copy", _ctx())
        assert result.success
        assert result.clipboard_text == "Hello world"


# ===== TTSSkill =====

class TestTTSSkill:
    def test_detect_available_engines(self):
        engines = detect_tts_engines()
        # At least espeak should be available on most Linux
        assert isinstance(engines, list)

    def test_skill_detect_native_text(self):
        skill = TTSSkill()
        text = "To jest bardzo ważna informacja dla tego projektu"
        conf = skill.detect(text, _ctx(locale="pl"))
        # Confidence depends on TTS engine availability
        if skill._engine:
            assert conf > 0
        else:
            assert conf == 0.0

    def test_skill_no_detect_short(self):
        skill = TTSSkill()
        assert skill.detect("ok", _ctx()) == 0.0

    def test_options_include_speak(self):
        skill = TTSSkill()
        if not skill._engine:
            pytest.skip("No TTS engine available")
        text = "To jest testowy tekst do odczytania na głos"
        options = skill.get_options(text, _ctx())
        ids = [o.id for o in options]
        assert "speak" in ids


# ===== VoiceCommandSkill =====

class TestVoiceCommandSkill:
    def test_detect_translate_command(self):
        skill = VoiceCommandSkill()
        assert skill.detect("test", _ctx(latest_transcript="przetłumacz")) > 0.5

    def test_detect_run_command(self):
        skill = VoiceCommandSkill()
        assert skill.detect("test", _ctx(latest_transcript="uruchom to")) > 0.5

    def test_detect_copy_command(self):
        skill = VoiceCommandSkill()
        assert skill.detect("test", _ctx(latest_transcript="kopiuj")) > 0.5

    def test_no_detect_without_transcript(self):
        skill = VoiceCommandSkill()
        assert skill.detect("test", _ctx(latest_transcript="")) == 0.0

    def test_no_detect_unrecognized(self):
        skill = VoiceCommandSkill()
        assert skill.detect("test", _ctx(latest_transcript="pogoda jest ładna")) == 0.0

    def test_options_include_voice_action(self):
        skill = VoiceCommandSkill()
        options = skill.get_options("some text", _ctx(latest_transcript="kopiuj"))
        ids = [o.id for o in options]
        assert "copy" in ids
        assert "cancel" in ids

    @pytest.mark.asyncio
    async def test_execute_copy(self):
        skill = VoiceCommandSkill()
        result = await skill.execute("hello world", "copy", _ctx())
        assert result.success
        assert result.clipboard_text == "hello world"

    @pytest.mark.asyncio
    async def test_execute_cancel(self):
        skill = VoiceCommandSkill()
        result = await skill.execute("text", "cancel", _ctx())
        assert result.success

    @pytest.mark.asyncio
    async def test_execute_search(self):
        skill = VoiceCommandSkill()
        result = await skill.execute("flask tutorial", "search", _ctx())
        assert result.success
        assert result.open_url.startswith("https://")

    @pytest.mark.asyncio
    async def test_execute_save(self):
        skill = VoiceCommandSkill()
        result = await skill.execute("important text", "save", _ctx())
        assert result.success
        assert result.clipboard_text  # path to saved file


# ===== URLHandlerSkill =====

class TestURLHandlerSkill:
    def test_detect_url(self):
        skill = URLHandlerSkill()
        assert skill.detect("Visit https://example.com", _ctx()) > 0.5

    def test_detect_email(self):
        skill = URLHandlerSkill()
        assert skill.detect("Contact user@example.com", _ctx()) > 0.5

    def test_detect_path(self):
        skill = URLHandlerSkill()
        assert skill.detect("/home/user/file.txt", _ctx()) > 0.5

    def test_detect_ip(self):
        skill = URLHandlerSkill()
        assert skill.detect("Server at 192.168.1.1:8080", _ctx()) > 0.5

    def test_no_detect_plain(self):
        skill = URLHandlerSkill()
        assert skill.detect("just plain text here", _ctx()) == 0.0

    def test_options_url(self):
        skill = URLHandlerSkill()
        options = skill.get_options("Visit https://example.com", _ctx())
        ids = [o.id for o in options]
        assert "open_url" in ids
        assert "copy" in ids

    @pytest.mark.asyncio
    async def test_execute_copy(self):
        skill = URLHandlerSkill()
        result = await skill.execute("https://example.com", "copy", _ctx())
        assert result.success
        assert "example.com" in result.clipboard_text

    @pytest.mark.asyncio
    async def test_execute_open_url(self):
        skill = URLHandlerSkill()
        result = await skill.execute("Visit https://example.com/docs", "open_url", _ctx())
        assert result.success
        assert result.open_url == "https://example.com/docs"


# ===== SkillMatch / SkillOption serialization =====

class TestSerialization:
    def test_skill_option_to_dict(self):
        opt = SkillOption(id="test", label="Test", icon="🔧", risk=OptionRisk.LOW)
        d = opt.to_dict()
        assert d["id"] == "test"
        assert d["risk"] == "low"

    def test_skill_result_to_dict(self):
        r = SkillResult(success=True, message="OK", clipboard_text="hello", output="world")
        d = r.to_dict()
        assert d["success"] is True
        assert d["clipboard_text"] == "hello"
        assert d["output"] == "world"

    def test_skill_match_to_dict(self):
        m = SkillMatch(
            skill_name="test", category=SkillCategory.COMMAND,
            confidence=0.9, label="Test", icon="🔧",
            options=[SkillOption(id="a", label="A")],
            extracted_text="hello",
        )
        d = m.to_dict()
        assert d["skill"] == "test"
        assert d["confidence"] == 0.9
        assert len(d["options"]) == 1

    def test_skill_context_defaults(self):
        ctx = SkillContext()
        assert ctx.locale == "pl"
        assert ctx.timestamp > 0
