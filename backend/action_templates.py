"""
App-Specific Action Templates — learned action patterns from user approvals.

Provides:
- Per-app action templates that learn from user behavior
- Confidence scoring based on approval/rejection history
- Auto-execute promotion: actions approved N times become auto-executable
- Persistent storage in SQLite for cross-session learning
- Community-sharable template export/import (JSON)

Integrates with:
- ShellAgent (shell_agent.py) — enhances suggest_actions with learned patterns
- EventBus (event_bus.py) — subscribes to cmd.approve_action / cmd.execute_action
- PipelineContext (pipeline.py) — enriches agent_actions with confidence scores

Example learned workflow:
  User sees "ModuleNotFoundError: No module named 'requests'" in VS Code terminal
  → System suggests "pip install requests" (from shell_agent rules)
  → User approves 3 times for similar patterns
  → System auto-promotes to auto-execute for this user + app combo
"""
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import nfo
import structlog

from window_aware import AppCategory

logger = structlog.get_logger()


@dataclass
class ActionTemplate:
    """A learned action template with confidence tracking."""
    template_id: str
    app_category: str              # AppCategory value
    trigger_pattern: str           # Regex pattern that triggers this template
    command_template: str          # Command with {1}, {2} placeholders for regex groups
    description: str = ""
    risk_level: str = "medium"     # safe | low | medium | high

    # Learning metrics
    times_suggested: int = 0
    times_approved: int = 0
    times_rejected: int = 0
    times_executed: int = 0
    last_used: float = 0.0
    created_at: float = 0.0

    # Auto-execution threshold
    auto_approve_after: int = 3    # Auto-execute after N manual approvals
    is_auto_approved: bool = False # Promoted to auto-execute

    @property
    def confidence(self) -> float:
        """Compute confidence score (0.0 - 1.0) based on approval history."""
        total = self.times_approved + self.times_rejected
        if total == 0:
            return 0.5  # neutral prior
        return self.times_approved / total

    @property
    def should_auto_execute(self) -> bool:
        """Check if this template has earned auto-execute status."""
        return (
            self.is_auto_approved
            or (self.times_approved >= self.auto_approve_after
                and self.times_rejected == 0
                and self.risk_level in ("safe", "low", "medium"))
        )

    def to_dict(self) -> Dict:
        return {
            "template_id": self.template_id,
            "app_category": self.app_category,
            "trigger_pattern": self.trigger_pattern,
            "command_template": self.command_template,
            "description": self.description,
            "risk_level": self.risk_level,
            "confidence": round(self.confidence, 3),
            "times_suggested": self.times_suggested,
            "times_approved": self.times_approved,
            "times_rejected": self.times_rejected,
            "times_executed": self.times_executed,
            "should_auto_execute": self.should_auto_execute,
            "last_used": self.last_used,
        }


@dataclass
class ScoredAction:
    """An action suggestion with confidence from template matching."""
    template: ActionTemplate
    command: str                   # Resolved command (placeholders filled)
    match_groups: Tuple = ()       # Regex match groups
    confidence: float = 0.0
    auto_execute: bool = False

    def to_dict(self) -> Dict:
        return {
            "template_id": self.template.template_id,
            "command": self.command,
            "description": self.template.description,
            "risk_level": self.template.risk_level,
            "confidence": round(self.confidence, 3),
            "auto_execute": self.auto_execute,
            "app_category": self.template.app_category,
        }


class AppActionLibrary:
    """
    Per-app action template library that learns from user approvals.

    Templates are stored in SQLite and evolve over time:
    - New patterns start with 0.5 confidence (neutral)
    - Each approval increases confidence, each rejection decreases
    - After N approvals with 0 rejections → auto-execute promotion
    - Templates can be exported/imported as JSON for community sharing
    """

    # Built-in seed templates (shipped with the app)
    _SEED_TEMPLATES = [
        ActionTemplate(
            template_id="py_module_not_found",
            app_category="ide",
            trigger_pattern=r"ModuleNotFoundError:\s+No module named '(\w[\w.]*)'",
            command_template="pip install {1}",
            description="Zainstaluj brakujący moduł Python",
            risk_level="medium",
            auto_approve_after=3,
        ),
        ActionTemplate(
            template_id="py_module_not_found_terminal",
            app_category="terminal",
            trigger_pattern=r"ModuleNotFoundError:\s+No module named '(\w[\w.]*)'",
            command_template="pip install {1}",
            description="Zainstaluj brakujący moduł Python",
            risk_level="medium",
            auto_approve_after=3,
        ),
        ActionTemplate(
            template_id="node_module_missing",
            app_category="terminal",
            trigger_pattern=r"Cannot find module '([^']+)'",
            command_template="npm install {1}",
            description="Zainstaluj brakujący moduł Node.js",
            risk_level="medium",
            auto_approve_after=3,
        ),
        ActionTemplate(
            template_id="git_push_rejected",
            app_category="terminal",
            trigger_pattern=r"git push.*rejected|failed to push|non-fast-forward",
            command_template="git pull --rebase",
            description="Git push odrzucony — rebase i ponów",
            risk_level="medium",
            auto_approve_after=5,
        ),
        ActionTemplate(
            template_id="git_uncommitted",
            app_category="ide",
            trigger_pattern=r"Changes not staged|modified:\s+",
            command_template="git status --short",
            description="Sprawdź niezacommitowane zmiany",
            risk_level="safe",
            auto_approve_after=2,
        ),
        ActionTemplate(
            template_id="disk_full",
            app_category="terminal",
            trigger_pattern=r"No space left on device|ENOSPC",
            command_template="df -h && du -sh /tmp/* 2>/dev/null | sort -rh | head -10",
            description="Sprawdź miejsce na dysku",
            risk_level="safe",
            auto_approve_after=2,
        ),
        ActionTemplate(
            template_id="port_in_use",
            app_category="terminal",
            trigger_pattern=r"(?:Address already in use|EADDRINUSE).*?(?:port\s+)?(\d{2,5})",
            command_template="ss -tlnp | grep :{1}",
            description="Sprawdź co zajmuje port",
            risk_level="safe",
            auto_approve_after=2,
        ),
        ActionTemplate(
            template_id="pytest_failure",
            app_category="terminal",
            trigger_pattern=r"FAILED\s+([\w/]+\.py)::",
            command_template="pytest {1} -v --tb=short",
            description="Uruchom ponownie nieudany test",
            risk_level="low",
            auto_approve_after=3,
        ),
        ActionTemplate(
            template_id="docker_permission",
            app_category="terminal",
            trigger_pattern=r"docker.*permission denied|Cannot connect to.*Docker",
            command_template="groups | grep docker && docker info",
            description="Sprawdź uprawnienia Docker",
            risk_level="safe",
            auto_approve_after=2,
        ),
        ActionTemplate(
            template_id="rust_compile_error",
            app_category="terminal",
            trigger_pattern=r"error\[E\d+\]:",
            command_template="cargo check 2>&1 | head -30",
            description="Sprawdź błędy kompilacji Rust",
            risk_level="safe",
            auto_approve_after=2,
        ),
    ]

    def __init__(
        self,
        db_path: str = "logs/action_templates.db",
        auto_approve_default: int = 3,
        enabled: bool = True,
    ):
        """
        Args:
            db_path: SQLite database path for persistent template storage
            auto_approve_default: Default N for auto-execute promotion
            enabled: Enable/disable template learning
        """
        self.db_path = db_path
        self.auto_approve_default = auto_approve_default
        self.enabled = enabled

        self._templates: Dict[str, ActionTemplate] = {}
        self._db: Optional[sqlite3.Connection] = None

        # Stats
        self.total_suggestions = 0
        self.total_approvals = 0
        self.total_rejections = 0
        self.total_auto_executions = 0

        if enabled:
            self._init_db()
            self._load_templates()
            self._seed_defaults()

        logger.info(
            "AppActionLibrary initialized",
            enabled=enabled,
            templates=len(self._templates),
            db_path=db_path,
        )

    # ── DB ────────────────────────────────────────────────────────────

    def _init_db(self):
        """Initialize SQLite database."""
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)
        self._db = sqlite3.connect(self.db_path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                template_id TEXT PRIMARY KEY,
                app_category TEXT NOT NULL,
                trigger_pattern TEXT NOT NULL,
                command_template TEXT NOT NULL,
                description TEXT DEFAULT '',
                risk_level TEXT DEFAULT 'medium',
                times_suggested INTEGER DEFAULT 0,
                times_approved INTEGER DEFAULT 0,
                times_rejected INTEGER DEFAULT 0,
                times_executed INTEGER DEFAULT 0,
                auto_approve_after INTEGER DEFAULT 3,
                is_auto_approved INTEGER DEFAULT 0,
                last_used REAL DEFAULT 0,
                created_at REAL DEFAULT 0
            )
        """)
        self._db.commit()

    def _load_templates(self):
        """Load all templates from DB."""
        if not self._db:
            return
        try:
            cursor = self._db.execute("SELECT * FROM templates")
            for row in cursor:
                t = ActionTemplate(
                    template_id=row[0],
                    app_category=row[1],
                    trigger_pattern=row[2],
                    command_template=row[3],
                    description=row[4] or "",
                    risk_level=row[5] or "medium",
                    times_suggested=row[6] or 0,
                    times_approved=row[7] or 0,
                    times_rejected=row[8] or 0,
                    times_executed=row[9] or 0,
                    auto_approve_after=row[10] or 3,
                    is_auto_approved=bool(row[11]),
                    last_used=row[12] or 0.0,
                    created_at=row[13] or 0.0,
                )
                self._templates[t.template_id] = t
        except Exception as e:
            logger.warning("Failed to load templates", error=str(e))

    def _seed_defaults(self):
        """Add built-in templates if not already present."""
        now = time.time()
        for seed in self._SEED_TEMPLATES:
            if seed.template_id not in self._templates:
                # Clone class-level seed to avoid mutating shared objects across
                # library instances/tests (which can leak approval counters).
                seed_copy = replace(
                    seed,
                    times_suggested=0,
                    times_approved=0,
                    times_rejected=0,
                    times_executed=0,
                    is_auto_approved=False,
                    last_used=0.0,
                    created_at=now,
                    auto_approve_after=seed.auto_approve_after or self.auto_approve_default,
                )
                self._templates[seed_copy.template_id] = seed_copy
                self._persist_template(seed_copy)

    def _persist_template(self, t: ActionTemplate):
        """Save a single template to DB."""
        if not self._db:
            return
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO templates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.template_id, t.app_category, t.trigger_pattern,
                    t.command_template, t.description, t.risk_level,
                    t.times_suggested, t.times_approved, t.times_rejected,
                    t.times_executed, t.auto_approve_after,
                    int(t.is_auto_approved), t.last_used, t.created_at,
                ),
            )
            self._db.commit()
        except Exception as e:
            logger.warning("Failed to persist template", template_id=t.template_id, error=str(e))

    # ── Suggestion Engine ─────────────────────────────────────────────

    def suggest_with_confidence(
        self,
        text: str,
        app_category: str,
        cwd: Optional[str] = None,
    ) -> List[ScoredAction]:
        """
        Match text against all templates and return scored actions.

        Args:
            text: Detected text (OCR + analysis)
            app_category: Current AppCategory value
            cwd: Working directory for command context

        Returns:
            List of ScoredAction sorted by confidence (highest first)
        """
        if not self.enabled:
            return []

        self.total_suggestions += 1
        results = []

        for t in self._templates.values():
            # Filter by app category
            if t.app_category != app_category and t.app_category != "any":
                continue

            # Try pattern match
            match = re.search(t.trigger_pattern, text, re.IGNORECASE)
            if not match:
                continue

            # Resolve command template
            command = t.command_template
            for i, group in enumerate(match.groups(), 1):
                if group:
                    command = command.replace(f"{{{i}}}", group)

            # Handle default values like {match_1:-8000}
            command = re.sub(r'\{match_\d+:-(\w+)\}', r'\1', command)

            # Update suggestion count
            t.times_suggested += 1
            t.last_used = time.time()

            results.append(ScoredAction(
                template=t,
                command=command,
                match_groups=match.groups(),
                confidence=t.confidence,
                auto_execute=t.should_auto_execute,
            ))

        # Sort by confidence (highest first)
        results.sort(key=lambda a: a.confidence, reverse=True)

        # Persist updated counts
        for r in results:
            self._persist_template(r.template)

        return results

    # ── Learning from user feedback ──────────────────────────────────

    @nfo.log_call(level="INFO")
    def learn_from_approval(self, template_id: str):
        """
        Record that user approved an action from this template.
        May promote to auto-execute if threshold reached.

        Args:
            template_id: Template that was approved
        """
        t = self._templates.get(template_id)
        if not t:
            return

        t.times_approved += 1
        t.last_used = time.time()
        self.total_approvals += 1

        # Check auto-execute promotion
        if t.should_auto_execute and not t.is_auto_approved:
            t.is_auto_approved = True
            logger.info(
                "Template auto-promoted",
                template_id=template_id,
                approvals=t.times_approved,
                confidence=t.confidence,
            )

        self._persist_template(t)

    @nfo.log_call(level="INFO")
    def learn_from_rejection(self, template_id: str):
        """
        Record that user rejected/dismissed an action from this template.

        Args:
            template_id: Template that was rejected
        """
        t = self._templates.get(template_id)
        if not t:
            return

        t.times_rejected += 1
        t.last_used = time.time()
        self.total_rejections += 1

        # Revoke auto-execute if confidence drops
        if t.is_auto_approved and t.confidence < 0.7:
            t.is_auto_approved = False
            logger.info(
                "Template auto-execute revoked",
                template_id=template_id,
                confidence=t.confidence,
            )

        self._persist_template(t)

    @nfo.log_call(level="INFO")
    def learn_from_execution(self, template_id: str):
        """
        Record that an action from this template was executed.

        Args:
            template_id: Template whose action was executed
        """
        t = self._templates.get(template_id)
        if not t:
            return

        t.times_executed += 1
        t.last_used = time.time()

        if t.should_auto_execute:
            self.total_auto_executions += 1

        self._persist_template(t)

    # ── Template management ──────────────────────────────────────────

    def add_template(
        self,
        template_id: str,
        app_category: str,
        trigger_pattern: str,
        command_template: str,
        description: str = "",
        risk_level: str = "medium",
    ) -> ActionTemplate:
        """Add a new custom template."""
        t = ActionTemplate(
            template_id=template_id,
            app_category=app_category,
            trigger_pattern=trigger_pattern,
            command_template=command_template,
            description=description,
            risk_level=risk_level,
            auto_approve_after=self.auto_approve_default,
            created_at=time.time(),
        )
        self._templates[template_id] = t
        self._persist_template(t)
        logger.info("Template added", template_id=template_id)
        return t

    def remove_template(self, template_id: str) -> bool:
        """Remove a template."""
        if template_id not in self._templates:
            return False
        del self._templates[template_id]
        if self._db:
            self._db.execute("DELETE FROM templates WHERE template_id = ?", (template_id,))
            self._db.commit()
        return True

    def get_template(self, template_id: str) -> Optional[ActionTemplate]:
        """Get a template by ID."""
        return self._templates.get(template_id)

    def get_templates_for_app(self, app_category: str) -> List[ActionTemplate]:
        """Get all templates for a specific app category."""
        return [
            t for t in self._templates.values()
            if t.app_category == app_category or t.app_category == "any"
        ]

    # ── Export/Import for community sharing ──────────────────────────

    def export_templates(self, include_stats: bool = False) -> str:
        """Export all templates as JSON string."""
        templates = []
        for t in self._templates.values():
            d = {
                "template_id": t.template_id,
                "app_category": t.app_category,
                "trigger_pattern": t.trigger_pattern,
                "command_template": t.command_template,
                "description": t.description,
                "risk_level": t.risk_level,
                "auto_approve_after": t.auto_approve_after,
            }
            if include_stats:
                d.update({
                    "times_approved": t.times_approved,
                    "times_rejected": t.times_rejected,
                    "confidence": t.confidence,
                })
            templates.append(d)
        return json.dumps({"version": 1, "templates": templates}, indent=2)

    def import_templates(self, json_str: str, overwrite: bool = False) -> int:
        """
        Import templates from JSON string.

        Args:
            json_str: JSON export string
            overwrite: If True, overwrite existing templates

        Returns:
            Number of templates imported
        """
        try:
            data = json.loads(json_str)
            templates = data.get("templates", [])
            imported = 0

            for d in templates:
                tid = d.get("template_id", "")
                if not tid:
                    continue
                if tid in self._templates and not overwrite:
                    continue

                self.add_template(
                    template_id=tid,
                    app_category=d.get("app_category", "unknown"),
                    trigger_pattern=d.get("trigger_pattern", ""),
                    command_template=d.get("command_template", ""),
                    description=d.get("description", ""),
                    risk_level=d.get("risk_level", "medium"),
                )
                imported += 1

            logger.info("Templates imported", count=imported)
            return imported

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Template import failed", error=str(e))
            return 0

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get action template statistics."""
        auto_count = sum(1 for t in self._templates.values() if t.should_auto_execute)
        return {
            "enabled": self.enabled,
            "total_templates": len(self._templates),
            "auto_execute_templates": auto_count,
            "total_suggestions": self.total_suggestions,
            "total_approvals": self.total_approvals,
            "total_rejections": self.total_rejections,
            "total_auto_executions": self.total_auto_executions,
            "top_templates": [
                t.to_dict() for t in sorted(
                    self._templates.values(),
                    key=lambda t: t.times_approved,
                    reverse=True,
                )[:5]
            ],
        }

    def close(self):
        """Close database connection."""
        if self._db:
            self._db.close()
            self._db = None


@nfo.log_call(level="INFO")
def create_action_library_from_env(settings=None) -> AppActionLibrary:
    """Create AppActionLibrary from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return AppActionLibrary(
        db_path=settings.action_templates_db,
        auto_approve_default=settings.action_auto_approve_after,
        enabled=settings.enable_action_templates,
    )
