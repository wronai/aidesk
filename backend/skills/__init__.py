"""
Skills Package — Extensible text analysis and action system.

SkillRouter analyzes selected text, detects type, and returns
ranked skill matches with actionable popup options.

Architecture:
    SkillRouter.analyze(text, ctx) → List[SkillMatch]
    SkillRouter.execute(skill_name, text, option_id, ctx) → SkillResult

Adding a new skill:
    1. Create skills/my_skill.py with class MySkill(BaseSkill)
    2. Add to BUILTIN_SKILLS list below
    3. Done — SkillRouter auto-discovers it
"""
from typing import Dict, List, Optional

import nfo
import structlog

from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillMatch, SkillOption, SkillResult,
)

logger = structlog.get_logger()

# All built-in skills — add new skills here
BUILTIN_SKILLS: List[type] = []


def _load_builtins():
    """Import and register all built-in skill classes."""
    global BUILTIN_SKILLS
    from skills.shell_command import ShellCommandSkill
    from skills.error_fixer import ErrorFixerSkill
    from skills.clipboard_relation import ClipboardRelationSkill
    from skills.translation import TranslationSkill
    from skills.tts import TTSSkill
    from skills.voice_command import VoiceCommandSkill
    from skills.url_handler import URLHandlerSkill

    BUILTIN_SKILLS = [
        VoiceCommandSkill,       # priority 95 — voice overrides all
        ShellCommandSkill,       # priority 90
        ErrorFixerSkill,         # priority 85
        ClipboardRelationSkill,  # priority 80 — selection ↔ clipboard intent
        URLHandlerSkill,         # priority 70
        TranslationSkill,        # priority 60
        TTSSkill,                # priority 40
    ]


class SkillRouter:
    """
    Routes selected text to matching skills and returns ranked options.

    Flow:
    1. analyze() — detect all matching skills, rank by confidence
    2. Frontend shows popup with top skill options
    3. User clicks option → execute()
    """

    def __init__(self):
        if not BUILTIN_SKILLS:
            _load_builtins()

        self._skills: List[BaseSkill] = []
        for cls in BUILTIN_SKILLS:
            try:
                self._skills.append(cls())
            except Exception as e:
                logger.warning("Failed to init skill", skill=cls.__name__, error=str(e))

        # Sort by priority (highest first)
        self._skills.sort(key=lambda s: s.priority, reverse=True)
        self._skill_map: Dict[str, BaseSkill] = {s.name: s for s in self._skills}

        logger.info("SkillRouter initialized", skills=[s.name for s in self._skills])

    @nfo.log_call(level="INFO", sample_rate=0.5)
    def analyze(self, text: str, ctx: SkillContext) -> List[SkillMatch]:
        """
        Analyze text against all skills, return ranked matches.

        Returns all skills with confidence > 0, sorted by confidence descending.
        Typically the frontend shows the top 1-3 matches as popup options.
        """
        matches: List[SkillMatch] = []

        for skill in self._skills:
            try:
                match = skill.get_match(text, ctx)
                if match and match.confidence > 0:
                    matches.append(match)
            except Exception as e:
                logger.warning("Skill detection error", skill=skill.name, error=str(e))

        # Sort by confidence (highest first)
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches

    @nfo.log_call(level="INFO", sample_rate=0.5)
    async def execute(
        self,
        skill_name: str,
        text: str,
        option_id: str,
        ctx: SkillContext,
    ) -> SkillResult:
        """Execute a specific skill option chosen by the user."""
        skill = self._skill_map.get(skill_name)
        if not skill:
            return SkillResult(success=False, error=f"Unknown skill: {skill_name}")

        try:
            return await skill.execute(text, option_id, ctx)
        except Exception as e:
            logger.error("Skill execution error", skill=skill_name, option=option_id, error=str(e))
            return SkillResult(success=False, error=str(e))

    def register_skill(self, skill: BaseSkill):
        """Register a custom/plugin skill at runtime."""
        self._skills.append(skill)
        self._skills.sort(key=lambda s: s.priority, reverse=True)
        self._skill_map[skill.name] = skill
        logger.info("Custom skill registered", skill=skill.name)

    def get_skill_names(self) -> List[str]:
        return [s.name for s in self._skills]

    def get_stats(self) -> Dict:
        return {
            "total_skills": len(self._skills),
            "skills": [
                {"name": s.name, "category": s.category.value, "priority": s.priority, "icon": s.icon}
                for s in self._skills
            ],
        }
