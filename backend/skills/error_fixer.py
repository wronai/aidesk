"""
ErrorFixerSkill — Detect errors/tracebacks and suggest fix commands.

Detects:
- Python tracebacks, ModuleNotFoundError, TypeError, etc.
- Node.js module errors
- Git errors (push rejected, merge conflicts)
- Build errors (make, cargo)
- Permission denied, disk space, connection refused

Options popup:
- Run suggested fix command
- Copy fix command
- Search error online
"""
import re
from typing import List, Optional

import nfo
from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult, OptionRisk,
)

# Error patterns → (regex, label, fix_command_template, search_query_template)
ERROR_RULES = [
    {
        "pattern": r"ModuleNotFoundError:\s+No module named ['\"]?(\w+)['\"]?",
        "label": "Brakujący moduł Python",
        "fix": "pip install {1}",
        "search": "python ModuleNotFoundError {1}",
    },
    {
        "pattern": r"Cannot find module ['\"]([^'\"]+)['\"]",
        "label": "Brakujący moduł Node.js",
        "fix": "npm install {1}",
        "search": "npm Cannot find module {1}",
    },
    {
        "pattern": r"(TypeError|ValueError|KeyError|AttributeError|NameError|IndexError):\s+(.+)",
        "label": "Wyjątek Python: {1}",
        "fix": "",
        "search": "python {1} {2}",
    },
    {
        "pattern": r"(Traceback \(most recent call last\):.*?)(?:\n\n|\Z)",
        "flags": re.DOTALL,
        "label": "Python Traceback",
        "fix": "",
        "search": "python {last_line}",
    },
    {
        "pattern": r"(?:error|fatal):\s+(.*?(?:push|pull|merge|rebase|checkout).*)",
        "label": "Błąd Git",
        "fix": "git status",
        "search": "git error {1}",
    },
    {
        "pattern": r"CONFLICT.*?Merge conflict in (.+)",
        "label": "Konflikt merge: {1}",
        "fix": "git diff --name-only --diff-filter=U",
        "search": "git merge conflict resolve",
    },
    {
        "pattern": r"permission denied|EACCES",
        "label": "Brak uprawnień",
        "fix": "",
        "search": "linux permission denied {0}",
    },
    {
        "pattern": r"No space left on device|ENOSPC",
        "label": "Brak miejsca na dysku",
        "fix": "df -h && du -sh /tmp/* 2>/dev/null | sort -rh | head -10",
        "search": "linux no space left on device",
    },
    {
        "pattern": r"Connection refused|ECONNREFUSED.*?(?:port\s+)?(\d+)?",
        "label": "Połączenie odrzucone",
        "fix": "ss -tlnp | grep {1:-8080}",
        "search": "connection refused port {1}",
    },
    {
        "pattern": r"command not found:\s*(\S+)",
        "label": "Komenda nie znaleziona: {1}",
        "fix": "which {1} || apt list --installed 2>/dev/null | grep {1}",
        "search": "install {1} linux",
    },
    {
        "pattern": r"make\[?\d?\]?:\s+\*\*\*.*Error",
        "label": "Błąd kompilacji make",
        "fix": "make 2>&1 | tail -20",
        "search": "make build error",
    },
    {
        "pattern": r"error\[E\d+\]|cargo.*error",
        "label": "Błąd Rust/Cargo",
        "fix": "cargo check 2>&1 | head -30",
        "search": "rust cargo error",
    },
]


class ErrorFixerSkill(BaseSkill):
    name = "error_fixer"
    category = SkillCategory.ERROR_FIX
    icon = "🔧"
    priority = 85

    def __init__(self):
        self._compiled = []
        for rule in ERROR_RULES:
            flags = rule.get("flags", re.IGNORECASE | re.MULTILINE)
            self._compiled.append((rule, re.compile(rule["pattern"], flags)))

    def detect(self, text: str, ctx: SkillContext) -> float:
        base = 0.0
        for rule, compiled in self._compiled:
            if compiled.search(text):
                base = 0.9
                break
        if base == 0.0:
            if re.search(r"\b(error|exception|failed|fatal|traceback)\b", text, re.IGNORECASE):
                base = 0.4

        # Boost if clipboard has related context (file path in error, or command that caused it)
        if base > 0 and ctx.clipboard_top:
            clip = ctx.clipboard_top.strip()
            # Clipboard is a file path mentioned in the error
            if re.match(r"^[\w./-]+\.\w{1,10}$", clip) and clip in text:
                base = min(base + 0.05, 1.0)
            # Clipboard is a command that might have caused the error
            elif re.match(r"^[\w][\w\s./-]{2,60}$", clip) and any(w in text for w in clip.split()[:3]):
                base = min(base + 0.03, 1.0)

        return base

    def _find_match(self, text: str):
        for rule, compiled in self._compiled:
            match = compiled.search(text)
            if match:
                return rule, match
        return None, None

    def _expand_template(self, template: str, match, text: str) -> str:
        if not template:
            return ""
        result = template.replace("{0}", match.group(0)[:100])
        for i, g in enumerate(match.groups(), 1):
            if g:
                result = result.replace(f"{{{i}}}", g[:80])
            else:
                # Handle default values like {1:-8080}
                result = re.sub(rf"\{{{i}:-([^}}]+)\}}", r"\1", result)
        # Remove remaining unfilled placeholders
        result = re.sub(r"\{\d+(?::-[^}]*)?\}", "", result)
        # {last_line} for tracebacks
        if "{last_line}" in result:
            lines = text.strip().split("\n")
            result = result.replace("{last_line}", lines[-1][:80] if lines else "")
        return result.strip()

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        rule, match = self._find_match(text)
        if not rule or not match:
            return [SkillOption(
                id="search",
                label="🔍 Szukaj rozwiązania online",
                icon="🔍",
                data={"extracted": text[:100]},
            )]

        label = self._expand_template(rule["label"], match, text)
        fix_cmd = self._expand_template(rule.get("fix", ""), match, text)
        search_q = self._expand_template(rule.get("search", ""), match, text)

        options = []

        if fix_cmd:
            options.append(SkillOption(
                id="fix",
                label=f"🔧 Napraw: {fix_cmd[:50]}",
                icon="🔧",
                description=f"Uruchom: {fix_cmd}",
                risk=OptionRisk.LOW,
                data={"command": fix_cmd, "extracted": fix_cmd},
            ))
            options.append(SkillOption(
                id="copy_fix",
                label="📋 Kopiuj komendę naprawczą",
                icon="📋",
                data={"command": fix_cmd, "extracted": fix_cmd},
            ))

        if search_q:
            options.append(SkillOption(
                id="search",
                label="🔍 Szukaj rozwiązania online",
                icon="🔍",
                data={"query": search_q, "extracted": text[:100]},
            ))

        return options

    _OPTION_DISPATCH = {
        "copy_fix": "_execute_copy_fix",
        "fix": "_execute_fix",
        "search": "_execute_search",
    }

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        handler_name = self._OPTION_DISPATCH.get(option_id)
        if not handler_name:
            return SkillResult(success=False, error=f"Unknown option: {option_id}")
        return await getattr(self, handler_name)(text, ctx)

    async def _execute_copy_fix(self, text: str, ctx: SkillContext) -> SkillResult:
        rule, match = self._find_match(text)
        if not rule or not match:
            return SkillResult(success=False, message="Brak dopasowanej regu\u0142y")
        fix_cmd = self._expand_template(rule.get("fix", ""), match, text)
        return SkillResult(success=True, message=f"📋 Skopiowano: `{fix_cmd}`", clipboard_text=fix_cmd)

    async def _execute_fix(self, text: str, ctx: SkillContext) -> SkillResult:
        rule, match = self._find_match(text)
        if not rule or not match:
            return SkillResult(success=False, message="Brak dopasowanej regu\u0142y")
        fix_cmd = self._expand_template(rule.get("fix", ""), match, text)
        if not fix_cmd:
            return SkillResult(success=False, message="Brak komendy naprawczej")

        import subprocess, os
        try:
            result = subprocess.run(
                fix_cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=ctx.cwd or None,
            )
            output = (result.stdout + result.stderr)[:2000]
            ok = result.returncode == 0
            return SkillResult(
                success=ok,
                message=f"{'\u2705' if ok else '\u274c'} {fix_cmd} (exit: {result.returncode})",
                output=output,
                clipboard_text=output.strip(),
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))

    async def _execute_search(self, text: str, ctx: SkillContext) -> SkillResult:
        rule, match = self._find_match(text)
        if rule and match:
            query = self._expand_template(rule.get("search", ""), match, text)
        else:
            query = text[:80]
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        return SkillResult(success=True, message=f"🔍 Szukam: {query[:50]}", open_url=url)

    def _label(self, text: str, ctx: SkillContext) -> str:
        rule, match = self._find_match(text)
        if rule and match:
            return self._expand_template(rule["label"], match, text)
        return "Wykryto b\u0142\u0105d"
