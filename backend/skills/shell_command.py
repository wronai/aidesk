"""
ShellCommandSkill — Detect shell commands, offer run locations and popup options.

Detects:
- Lines starting with common shell commands (git, docker, pip, npm, etc.)
- sudo commands
- Pipe chains
- Multi-line scripts

Options popup:
- Run in current terminal CWD
- Run in home directory
- Copy to clipboard
- Edit before running
"""
import re
import shlex
from typing import List, Optional

import nfo
from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult, OptionRisk,
)

# Patterns that indicate shell commands
_CMD_PREFIXES = (
    "git", "docker", "docker-compose", "podman",
    "pip", "pip3", "python", "python3",
    "npm", "npx", "yarn", "pnpm", "node", "bun", "deno",
    "cargo", "rustc", "go", "make", "cmake", "gcc", "g++",
    "apt", "apt-get", "yum", "dnf", "pacman", "brew", "snap", "flatpak",
    "systemctl", "journalctl", "service",
    "curl", "wget", "ssh", "scp", "rsync",
    "ls", "cd", "cat", "grep", "find", "sed", "awk", "sort", "head", "tail",
    "mkdir", "rm", "cp", "mv", "chmod", "chown", "ln",
    "tar", "zip", "unzip", "gzip",
    "kill", "pkill", "ps", "top", "htop",
    "echo", "export", "source", "alias",
)

_CMD_PATTERN = re.compile(
    r"^\s*(?:sudo\s+)?(?:" + "|".join(re.escape(c) for c in _CMD_PREFIXES) + r")\b",
    re.IGNORECASE | re.MULTILINE,
)

_PIPE_PATTERN = re.compile(r"\||\&\&|\|\|")
_SHEBANG_PATTERN = re.compile(r"^#!\s*/(?:usr/)?bin/")

# Dangerous command patterns
_DANGEROUS = [
    re.compile(r"rm\s+-rf\s+/\s*$"),
    re.compile(r"mkfs\."),
    re.compile(r"dd\s+if="),
    re.compile(r":\(\)\{"),
    re.compile(r"chmod\s+777\s+/"),
    re.compile(r">\s*/dev/sd"),
]


class ShellCommandSkill(BaseSkill):
    name = "shell_command"
    category = SkillCategory.COMMAND
    icon = "🖥️"
    priority = 90

    def detect(self, text: str, ctx: SkillContext) -> float:
        text = text.strip()
        if not text:
            return 0.0

        base = 0.0

        # Strong signal: starts with known command
        if _CMD_PATTERN.match(text):
            base = 0.9
        # Shebang script
        elif _SHEBANG_PATTERN.match(text):
            base = 0.85
        else:
            # Pipe chain anywhere
            lines = text.split("\n")
            if len(lines) <= 5 and any(_PIPE_PATTERN.search(l) for l in lines):
                if _CMD_PATTERN.search(text):
                    base = 0.8
            # Single short line that looks like a command
            elif len(lines) == 1 and len(text) < 200 and " " in text:
                if _CMD_PATTERN.search(text):
                    base = 0.7

        if base == 0.0:
            return 0.0

        # Clipboard context boost: if clipboard has an error and the command
        # looks like a fix (e.g. "pip install X" when clipboard has ModuleNotFoundError)
        if ctx.clipboard_top and self._clipboard_relates_to_cmd(text, ctx.clipboard_top):
            base = min(base + 0.05, 0.95)

        return base

    @staticmethod
    def _clipboard_relates_to_cmd(cmd: str, clipboard: str) -> bool:
        """Check if clipboard error context is related to the command."""
        clip_lower = clipboard.lower()
        has_error = any(kw in clip_lower for kw in (
            "error", "exception", "traceback", "failed", "not found",
            "permission denied", "command not found", "no such file",
        ))
        if not has_error:
            return False
        # Check if any word from the command appears in the error
        cmd_words = set(cmd.lower().split())
        clip_words = set(clip_lower.split())
        return len(cmd_words & clip_words) >= 1

    def _assess_risk(self, text: str) -> OptionRisk:
        for pat in _DANGEROUS:
            if pat.search(text):
                return OptionRisk.HIGH
        if text.strip().startswith("sudo "):
            return OptionRisk.MEDIUM
        if any(kw in text.lower() for kw in ("install", "rm ", "mv ", "chmod", "chown")):
            return OptionRisk.LOW
        return OptionRisk.SAFE

    def _extract_command(self, text: str) -> str:
        """Extract the core command (first meaningful line or full text)."""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip() and not l.strip().startswith("#")]
        return lines[0] if lines else text.strip()

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        cmd = self._extract_command(text)
        risk = self._assess_risk(cmd)
        cwd = ctx.cwd or "~"

        options = [
            SkillOption(
                id="run_cwd",
                label=f"▶ Uruchom w {cwd}",
                icon="🖥️",
                description=f"Wykonaj: {cmd[:60]}",
                risk=risk,
                data={"command": cmd, "cwd": ctx.cwd, "extracted": cmd},
            ),
            SkillOption(
                id="run_home",
                label="▶ Uruchom w ~/",
                icon="🏠",
                description=f"Wykonaj w katalogu domowym",
                risk=risk,
                data={"command": cmd, "cwd": "~", "extracted": cmd},
            ),
            SkillOption(
                id="copy",
                label="📋 Kopiuj komendę",
                icon="📋",
                risk=OptionRisk.SAFE,
                data={"command": cmd, "extracted": cmd},
            ),
        ]

        # Multi-line script: offer save as script
        if "\n" in text.strip():
            options.append(SkillOption(
                id="save_script",
                label="💾 Zapisz jako skrypt",
                icon="💾",
                risk=OptionRisk.LOW,
                data={"command": text.strip(), "extracted": cmd},
            ))

        # Clipboard context: if clipboard has error, offer to copy command + error together
        if ctx.clipboard_top and self._clipboard_relates_to_cmd(cmd, ctx.clipboard_top):
            options.append(SkillOption(
                id="copy_with_error",
                label="📋 Kopiuj komendę + błąd ze schowka",
                icon="📋",
                description="Komenda + kontekst błędu do wklejenia w czat/issue",
                risk=OptionRisk.SAFE,
                data={"command": cmd, "extracted": cmd},
            ))

        return options

    _OPTION_DISPATCH = {
        "copy": "_execute_copy",
        "copy_with_error": "_execute_copy_with_error",
        "save_script": "_execute_save_script",
        "run_cwd": "_execute_run",
        "run_home": "_execute_run",
    }

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        handler_name = self._OPTION_DISPATCH.get(option_id)
        if not handler_name:
            return SkillResult(success=False, error=f"Unknown option: {option_id}")
        return await getattr(self, handler_name)(text, option_id, ctx)

    async def _execute_copy(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        cmd = self._extract_command(text)
        return SkillResult(
            success=True,
            message=f"📋 Skopiowano: `{cmd[:60]}`",
            clipboard_text=cmd,
        )

    async def _execute_copy_with_error(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        cmd = self._extract_command(text)
        error_ctx = ctx.clipboard_top or ""
        combined = f"Komenda:\n{cmd}\n\nBłąd:\n{error_ctx[:1000]}"
        return SkillResult(
            success=True,
            message="📋 Skopiowano komendę + kontekst błędu",
            clipboard_text=combined,
        )

    async def _execute_save_script(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        import tempfile, os
        path = os.path.join(tempfile.gettempdir(), "proxeen_script.sh")
        with open(path, "w") as f:
            f.write("#!/bin/bash\n" + text.strip() + "\n")
        os.chmod(path, 0o755)
        return SkillResult(
            success=True,
            message=f"💾 Skrypt zapisany: `{path}`",
            clipboard_text=path,
        )

    async def _execute_run(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        import subprocess, os
        cmd = self._extract_command(text)
        cwd = ctx.cwd or None if option_id == "run_cwd" else os.path.expanduser("~")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=15, cwd=cwd,
            )
            output = (result.stdout + result.stderr)[:2000]
            success = result.returncode == 0
            emoji = "✅" if success else "❌"
            return SkillResult(
                success=success,
                message=f"{emoji} Exit code: {result.returncode}",
                output=output,
                clipboard_text=output.strip() if output.strip() else cmd,
            )
        except subprocess.TimeoutExpired:
            return SkillResult(success=False, message="⏱️ Timeout (15s)", error="timeout")
        except Exception as e:
            return SkillResult(success=False, message=f"❌ {e}", error=str(e))

    def _label(self, text: str, ctx: SkillContext) -> str:
        cmd = self._extract_command(text)
        return f"Komenda: {cmd[:50]}"
