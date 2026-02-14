"""
URLHandlerSkill — Detect URLs, file paths, IPs. Offer open/copy actions.
"""
import re
from typing import List

from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult, OptionRisk,
)

_URL_RE = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)
_PATH_RE = re.compile(r"(/(?:home|tmp|var|etc|opt|usr|root)/[\w/.@+-]+)")
_IP_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)")
_EMAIL_RE = re.compile(r"([\w.+-]+@[\w-]+\.[\w.-]+)")


class URLHandlerSkill(BaseSkill):
    name = "url_handler"
    category = SkillCategory.NAVIGATION
    icon = "🔗"
    priority = 70

    def detect(self, text: str, ctx: SkillContext) -> float:
        if _URL_RE.search(text):
            return 0.85
        if _EMAIL_RE.search(text):
            return 0.8
        if _PATH_RE.search(text):
            return 0.7
        if _IP_RE.search(text):
            return 0.65
        return 0.0

    def _extract(self, text: str):
        """Extract the primary navigable item."""
        m = _URL_RE.search(text)
        if m:
            return "url", m.group(1)
        m = _EMAIL_RE.search(text)
        if m:
            return "email", m.group(1)
        m = _PATH_RE.search(text)
        if m:
            return "path", m.group(1)
        m = _IP_RE.search(text)
        if m:
            return "ip", m.group(1)
        return None, None

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        kind, value = self._extract(text)
        if not kind:
            return []

        options = []
        if kind == "url":
            options.append(SkillOption(
                id="open_url", label=f"🌐 Otwórz URL", icon="🌐",
                description=value[:60], data={"url": value, "extracted": value},
            ))
        elif kind == "email":
            options.append(SkillOption(
                id="open_email", label=f"📧 Napisz email", icon="📧",
                description=value, data={"email": value, "extracted": value},
            ))
        elif kind == "path":
            options.append(SkillOption(
                id="open_path", label=f"📂 Otwórz ścieżkę", icon="📂",
                description=value[:60], data={"path": value, "extracted": value},
            ))
        elif kind == "ip":
            options.append(SkillOption(
                id="ping", label=f"🌐 Ping {value}", icon="🌐",
                description=f"ping -c3 {value}", risk=OptionRisk.SAFE,
                data={"ip": value, "extracted": value},
            ))

        options.append(SkillOption(
            id="copy", label="📋 Kopiuj", icon="📋",
            data={"text": value, "extracted": value},
        ))
        return options

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        kind, value = self._extract(text)

        if option_id == "copy":
            return SkillResult(success=True, message=f"📋 Skopiowano: {value}", clipboard_text=value or text)

        if option_id == "open_url":
            return SkillResult(success=True, message=f"🌐 Otwieram URL", open_url=value)

        if option_id == "open_email":
            return SkillResult(success=True, message=f"📧 {value}", open_url=f"mailto:{value}", clipboard_text=value)

        if option_id == "open_path":
            return SkillResult(success=True, message=f"📂 {value}", clipboard_text=value)

        if option_id == "ping":
            import subprocess
            try:
                result = subprocess.run(
                    ["ping", "-c", "3", value.split(":")[0]],
                    capture_output=True, text=True, timeout=10,
                )
                output = (result.stdout + result.stderr)[:1000]
                ok = result.returncode == 0
                return SkillResult(success=ok, message=f"{'✅' if ok else '❌'} ping {value}", output=output)
            except Exception as e:
                return SkillResult(success=False, error=str(e))

        return SkillResult(success=False, error=f"Unknown option: {option_id}")

    def _label(self, text: str, ctx: SkillContext) -> str:
        kind, value = self._extract(text)
        labels = {"url": "URL", "email": "Email", "path": "Ścieżka", "ip": "Adres IP"}
        return f"{labels.get(kind, 'Link')}: {value[:40]}" if value else "Link"
