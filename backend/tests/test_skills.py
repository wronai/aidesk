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

    def test_skill_context_clipboard_fields(self):
        ctx = SkillContext(clipboard_top="hello", clipboard_items=[{"text": "hello"}])
        assert ctx.clipboard_top == "hello"
        assert len(ctx.clipboard_items) == 1


# ===== ClipboardRelationSkill =====

from skills.clipboard_relation import ClipboardRelationSkill, _detect_lang, _text_similarity, _extract_domain


class TestClipboardRelationDetection:
    """Test intent detection from selection ↔ clipboard pairs."""

    def setup_method(self):
        self.skill = ClipboardRelationSkill()

    def test_no_clipboard_returns_zero(self):
        ctx = _ctx(clipboard_top="")
        assert self.skill.detect("some text", ctx) == 0.0

    def test_already_copied_exact(self):
        ctx = _ctx(clipboard_top="hello world")
        conf = self.skill.detect("hello world", ctx)
        assert conf > 0.9

    def test_already_copied_near_match(self):
        ctx = _ctx(clipboard_top="hello world!")
        conf = self.skill.detect("hello world!", ctx)
        assert conf > 0.9

    def test_error_file_match(self):
        traceback = '''Traceback (most recent call last):
  File "app.py", line 42, in main
    do_stuff()
TypeError: bad argument'''
        ctx = _ctx(clipboard_top=traceback)
        conf = self.skill.detect("app.py", ctx)
        assert conf > 0.8

    def test_error_file_no_match(self):
        ctx = _ctx(clipboard_top="Traceback: File \"other.py\", line 1")
        conf = self.skill.detect("app.py", ctx)
        assert conf == 0.0  # file not mentioned in error

    def test_cross_language_en_pl(self):
        ctx = _ctx(clipboard_top="The quick brown fox jumps over the lazy dog and they have been with this")
        conf = self.skill.detect("To jest prosty polski tekst ale nie tylko przez przypadek też bardzo", ctx)
        assert conf > 0.5

    def test_same_language_no_cross(self):
        ctx = _ctx(clipboard_top="The quick brown fox jumps over the lazy dog")
        conf = self.skill.detect("Another English sentence with common words here", ctx)
        # Should not trigger cross_language (both English)
        match = self.skill._best_intent("Another English sentence with common words here", ctx)
        if match:
            assert match.name != "cross_language"

    def test_complement_cmd_package(self):
        error = "ModuleNotFoundError: No module named 'flask'"
        ctx = _ctx(clipboard_top=error)
        conf = self.skill.detect("flask", ctx)
        assert conf > 0.8

    def test_complement_cmd_no_error(self):
        ctx = _ctx(clipboard_top="just some normal text")
        conf = self.skill.detect("flask", ctx)
        # No error in clipboard → no complement_cmd
        intent = self.skill._best_intent("flask", ctx)
        if intent:
            assert intent.name != "complement_cmd"

    def test_url_pair_same_domain(self):
        ctx = _ctx(clipboard_top="https://github.com/user/repo1")
        conf = self.skill.detect("https://github.com/user/repo2", ctx)
        assert conf > 0.5

    def test_url_pair_different_domain(self):
        ctx = _ctx(clipboard_top="https://google.com/search")
        conf = self.skill.detect("https://github.com/user/repo", ctx)
        assert conf > 0.3

    def test_save_to_path(self):
        ctx = _ctx(clipboard_top="This is a long content that should be saved to a file somewhere on disk")
        conf = self.skill.detect("/tmp/output.txt", ctx)
        assert conf > 0.5

    def test_code_similarity(self):
        code_a = "def hello():\n    print('hello')\n    return True"
        code_b = "def hello():\n    print('world')\n    return False"
        ctx = _ctx(clipboard_top=code_b)
        conf = self.skill.detect(code_a, ctx)
        assert conf > 0.5

    def test_diff_fragments(self):
        text_a = "The quick brown fox jumps over the lazy dog in the park"
        text_b = "The quick brown cat jumps over the lazy dog in the garden"
        ctx = _ctx(clipboard_top=text_b)
        conf = self.skill.detect(text_a, ctx)
        assert conf > 0.3


class TestClipboardRelationOptions:
    """Test that correct options are returned for each intent."""

    def setup_method(self):
        self.skill = ClipboardRelationSkill()

    def test_already_copied_options(self):
        ctx = _ctx(clipboard_top="hello world")
        options = self.skill.get_options("hello world", ctx)
        ids = [o.id for o in options]
        assert "replace_clipboard" in ids

    def test_error_file_options(self):
        tb = 'Traceback (most recent call last):\n  File "app.py", line 42, in main\n    do_stuff()\nTypeError: bad argument'
        ctx = _ctx(clipboard_top=tb)
        options = self.skill.get_options("app.py", ctx)
        ids = [o.id for o in options]
        assert "open_error_file" in ids

    def test_cross_language_options(self):
        ctx = _ctx(clipboard_top="The quick brown fox jumps over the lazy dog and they have been with this")
        options = self.skill.get_options("To jest prosty polski tekst ale nie tylko przez przypadek też bardzo", ctx)
        ids = [o.id for o in options]
        assert "translate_pair" in ids

    def test_install_options(self):
        ctx = _ctx(clipboard_top="ModuleNotFoundError: No module named 'requests'")
        options = self.skill.get_options("requests", ctx)
        ids = [o.id for o in options]
        assert "install_package" in ids


class TestClipboardRelationExecution:
    """Test skill execution for various option_ids."""

    def setup_method(self):
        self.skill = ClipboardRelationSkill()

    def test_copy_both(self):
        ctx = _ctx(clipboard_top="clipboard content")
        result = asyncio.get_event_loop().run_until_complete(
            self.skill.execute("selection text", "copy_both", ctx)
        )
        assert result.success
        assert "selection text" in result.clipboard_text
        assert "clipboard content" in result.clipboard_text

    def test_show_diff(self):
        ctx = _ctx(clipboard_top="line A\nline B")
        result = asyncio.get_event_loop().run_until_complete(
            self.skill.execute("line A\nline C", "show_diff", ctx)
        )
        assert result.success
        assert "---" in result.output

    def test_replace_clipboard(self):
        ctx = _ctx(clipboard_top="old")
        result = asyncio.get_event_loop().run_until_complete(
            self.skill.execute("new text", "replace_clipboard", ctx)
        )
        assert result.success
        assert result.clipboard_text == "new text"

    def test_translate_pair(self):
        ctx = _ctx(clipboard_top="Hello world")
        result = asyncio.get_event_loop().run_until_complete(
            self.skill.execute("Cześć świat", "translate_pair", ctx)
        )
        assert result.success
        assert "Cześć świat" in result.clipboard_text
        assert "Hello world" in result.clipboard_text

    def test_search_pair(self):
        ctx = _ctx(clipboard_top="some error")
        result = asyncio.get_event_loop().run_until_complete(
            self.skill.execute("flask", "search_pair", ctx)
        )
        assert result.success
        assert result.open_url
        assert "google" in result.open_url

    def test_unknown_option(self):
        ctx = _ctx(clipboard_top="x")
        result = asyncio.get_event_loop().run_until_complete(
            self.skill.execute("y", "nonexistent", ctx)
        )
        assert not result.success


class TestClipboardRelationHelpers:
    """Test helper functions."""

    def test_detect_lang_english(self):
        assert _detect_lang("The quick brown fox jumps over the lazy dog and they have been with this") == "en"

    def test_detect_lang_polish(self):
        assert _detect_lang("To jest prosty tekst ale nie tylko przez przypadek") == "pl"

    def test_detect_lang_cyrillic(self):
        assert _detect_lang("Привет мир") == "ru"

    def test_detect_lang_unknown(self):
        assert _detect_lang("xyz 123") == "unknown"

    def test_text_similarity_identical(self):
        assert _text_similarity("hello", "hello") == 1.0

    def test_text_similarity_empty(self):
        assert _text_similarity("", "hello") == 0.0

    def test_text_similarity_partial(self):
        sim = _text_similarity("hello world", "hello earth")
        assert 0.3 < sim < 0.9

    def test_extract_domain(self):
        assert _extract_domain("Visit https://github.com/user/repo") == "github.com"

    def test_extract_domain_none(self):
        assert _extract_domain("no url here") == ""


class TestClipboardRelationInRouter:
    """Test that ClipboardRelationSkill integrates with SkillRouter."""

    def test_router_includes_clipboard_relation(self):
        router = SkillRouter()
        assert "clipboard_relation" in router.get_skill_names()

    def test_router_detects_already_copied(self):
        router = SkillRouter()
        ctx = _ctx(clipboard_top="git status --short")
        matches = router.analyze("git status --short", ctx)
        names = [m.skill_name for m in matches]
        assert "clipboard_relation" in names

    def test_router_no_clipboard_no_match(self):
        router = SkillRouter()
        ctx = _ctx(clipboard_top="")
        matches = router.analyze("some random text", ctx)
        names = [m.skill_name for m in matches]
        assert "clipboard_relation" not in names


# ===== Expanded Intent Detectors =====

class TestClipboardRelationExpandedIntents:
    """Test the 8 new intent detectors added to ClipboardRelationSkill."""

    def setup_method(self):
        self.skill = ClipboardRelationSkill()

    # ── JSON pair ──

    def test_json_pair_both_json(self):
        sel = '{"name": "Alice", "age": 30}'
        clip = '{"name": "Bob", "age": 25}'
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "json_pair"
        assert intent.score > 0.5

    def test_json_pair_only_one_json(self):
        sel = '{"key": "value"}'
        clip = "just plain text"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        if intent:
            assert intent.name != "json_pair"

    def test_json_pair_options(self):
        sel = '{"a": 1}'
        clip = '{"b": 2}'
        ctx = _ctx(clipboard_top=clip)
        options = self.skill.get_options(sel, ctx)
        ids = [o.id for o in options]
        assert "show_diff" in ids

    # ── Git context ──

    def test_git_ref_with_diff(self):
        sel = "abc1234"
        clip = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "git_diff_ref"
        assert intent.score > 0.7

    def test_git_compare_two_refs(self):
        sel = "main"
        clip = "develop"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "git_compare"

    def test_git_ref_no_diff(self):
        sel = "abc1234"
        clip = "just some normal text without any git context"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        if intent:
            assert intent.name not in ("git_diff_ref", "git_compare")

    # ── IP / Host ──

    def test_ip_with_connection_error(self):
        sel = "192.168.1.100"
        clip = "Error: connection refused to 192.168.1.100:5432"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "ip_conn_error"
        assert intent.score > 0.8

    def test_ip_pair(self):
        sel = "10.0.0.1"
        clip = "10.0.0.2"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "ip_pair"

    def test_host_port_with_timeout(self):
        sel = "db.example.com:5432"
        clip = "Connection timeout after 30s to db.example.com:5432"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "ip_conn_error"

    # ── Env var ──

    def test_env_var_match(self):
        sel = "DATABASE_URL=postgres://localhost/mydb"
        clip = "Error: DATABASE_URL is not configured properly"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "env_var_match"

    def test_env_var_missing(self):
        sel = "API_KEY"
        clip = "KeyError: 'API_KEY' - environment variable not set"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "env_var_missing"
        assert intent.score > 0.8

    def test_env_var_no_reference(self):
        sel = "MY_VAR=hello"
        clip = "just some text without any env references"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        if intent:
            assert intent.name not in ("env_var_match", "env_var_missing")

    # ── Docker ──

    def test_docker_container_error(self):
        sel = "a1b2c3d4e5f6"
        clip = "docker: Error response from daemon: container a1b2c3d4e5f6 failed to start"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "docker_error"
        assert intent.score > 0.8

    def test_docker_context_both(self):
        sel = "FROM python:3.11-slim\nRUN pip install flask"
        clip = "docker build -t myapp .\ndocker run -p 8080:8080 myapp"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "docker_context"

    # ── Config key ──

    def test_config_key_in_config_block(self):
        sel = "server.port"
        clip = "server.host = 0.0.0.0\nserver.port = 8080\nserver.workers = 4"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "config_key_match"

    def test_config_key_not_in_clipboard(self):
        sel = "server.port"
        clip = "just some random text"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        if intent:
            assert intent.name != "config_key_match"

    # ── Stack trace context ──

    def test_stack_trace_symbol(self):
        sel = "handle_request"
        clip = '''Traceback (most recent call last):
  File "server.py", line 42, in handle_request
    result = process(data)
ValueError: invalid data'''
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "stack_trace_symbol"
        assert intent.score > 0.8

    def test_stack_trace_js_symbol(self):
        sel = "fetchData"
        clip = "TypeError: Cannot read property 'map' of undefined\n    at fetchData (app.js:15:3)"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "stack_trace_symbol"

    def test_stack_trace_no_match(self):
        sel = "myFunction"
        clip = "This is just a normal text without any stack trace"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        if intent:
            assert intent.name != "stack_trace_symbol"

    # ── Regex test ──

    def test_regex_selection_with_test_data(self):
        sel = r"^\d{3}-\d{3}-\d{4}$"
        clip = "555-123-4567\n800-555-0199\nhello world"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "regex_test"

    def test_regex_clipboard_with_test_data(self):
        sel = "test@example.com\nfoo@bar.org\nnot-an-email"
        clip = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        assert intent is not None
        assert intent.name == "regex_test"

    def test_regex_neither_is_regex(self):
        sel = "hello world"
        clip = "foo bar baz"
        ctx = _ctx(clipboard_top=clip)
        intent = self.skill._best_intent(sel, ctx)
        if intent:
            assert intent.name != "regex_test"


class TestExpandedIntentOptions:
    """Test that expanded intents return proper options."""

    def setup_method(self):
        self.skill = ClipboardRelationSkill()

    def test_git_ref_options(self):
        sel = "abc1234"
        clip = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py"
        ctx = _ctx(clipboard_top=clip)
        options = self.skill.get_options(sel, ctx)
        ids = [o.id for o in options]
        assert "copy_both" in ids

    def test_ip_error_options(self):
        sel = "192.168.1.1"
        clip = "ECONNREFUSED 192.168.1.1:3000"
        ctx = _ctx(clipboard_top=clip)
        options = self.skill.get_options(sel, ctx)
        ids = [o.id for o in options]
        assert "search_pair" in ids

    def test_env_missing_options(self):
        sel = "SECRET_KEY"
        clip = "Error: SECRET_KEY is not set"
        ctx = _ctx(clipboard_top=clip)
        options = self.skill.get_options(sel, ctx)
        ids = [o.id for o in options]
        assert "search_pair" in ids

    def test_stack_trace_options(self):
        sel = "process_data"
        clip = 'File "main.py", line 10, in process_data\nRuntimeError: fail'
        ctx = _ctx(clipboard_top=clip)
        options = self.skill.get_options(sel, ctx)
        ids = [o.id for o in options]
        assert "copy_both" in ids

    def test_regex_options(self):
        sel = r"^\d+$"
        clip = "123\nabc\n456"
        ctx = _ctx(clipboard_top=clip)
        options = self.skill.get_options(sel, ctx)
        ids = [o.id for o in options]
        assert "copy_both" in ids
