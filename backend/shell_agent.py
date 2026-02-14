"""
Shell Agent - Safe command execution engine for AI Desktop Assistant.

Provides:
- Whitelisted safe commands (read-only by default)
- Action suggestions based on detected screen content
- Command execution with timeout and output capture
- Audit log of all executed commands
- Clipboard integration for code snippets
"""
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import structlog

from window_aware import AppCategory

logger = structlog.get_logger()


class ActionRisk(str, Enum):
    """Risk level for agent actions."""
    SAFE = "safe"          # Read-only, no side effects
    LOW = "low"            # Minor side effects (clipboard, notifications)
    MEDIUM = "medium"      # Modifies files, git operations
    HIGH = "high"          # System changes, installs, network
    DANGEROUS = "dangerous" # Never auto-execute


@dataclass
class AgentAction:
    """A suggested or executed agent action."""
    action_id: str
    description: str
    command: str
    risk: ActionRisk
    category: AppCategory
    trigger_pattern: str = ""
    output: str = ""
    exit_code: int = -1
    executed: bool = False
    approved: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "action_id": self.action_id,
            "description": self.description,
            "command": self.command,
            "risk": self.risk.value,
            "category": self.category.value,
            "trigger_pattern": self.trigger_pattern,
            "output": self.output[:500] if self.output else "",
            "exit_code": self.exit_code,
            "executed": self.executed,
            "approved": self.approved,
            "timestamp": self.timestamp,
        }


# ===== Safe command whitelist =====
# Commands that can be auto-executed without user approval

SAFE_COMMANDS = {
    # Git (read-only)
    "git status", "git log", "git branch", "git diff", "git stash list",
    "git remote -v", "git tag", "git show",
    # System info
    "uname -a", "hostname", "whoami", "id", "date", "uptime",
    "free -h", "df -h", "lsblk", "lscpu",
    # Process info
    "ps aux", "top -bn1", "htop -t",
    # Network (read-only)
    "ip addr", "ip route", "ss -tlnp", "ping -c1",
    # File info (read-only)
    "ls", "find", "cat", "head", "tail", "wc", "file", "stat",
    "du -sh", "tree",
    # Development
    "python --version", "python3 --version", "node --version",
    "npm --version", "cargo --version", "go version",
    "rustc --version", "gcc --version", "make --version",
    "docker ps", "docker images",
    # Package info
    "pip list", "pip show", "npm list", "dpkg -l", "apt list",
}

# Commands that need user approval but are useful
APPROVAL_COMMANDS = {
    # Git (write)
    "git pull", "git push", "git commit", "git add", "git stash",
    "git checkout", "git merge", "git rebase",
    # File operations
    "mkdir", "touch", "cp", "mv",
    # Development
    "pip install", "npm install", "make", "cargo build",
    "docker run", "docker-compose up",
    # System
    "systemctl status", "journalctl",
}

# NEVER execute these
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",       # rm -rf /
    r"mkfs\.",              # format disk
    r"dd\s+if=",            # disk overwrite
    r":\(\)\{",             # fork bomb
    r"chmod\s+777\s+/",    # chmod 777 /
    r"curl.*\|\s*(ba)?sh",  # curl | bash
    r"wget.*\|\s*(ba)?sh",  # wget | bash
    r"eval\s+",             # eval
    r">\s*/dev/sd",         # write to raw device
    r"shutdown|reboot|halt|poweroff",
    r"passwd|useradd|userdel|groupadd",
]


# ===== Pattern → Action mappings =====
# Detected text patterns → suggested commands

ACTION_RULES: List[Dict] = [
    # Git errors
    {
        "pattern": r"git push.*rejected|failed to push|non-fast-forward",
        "description": "Git push odrzucony — synchronizuj z remote",
        "command": "git pull --rebase",
        "risk": ActionRisk.MEDIUM,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    {
        "pattern": r"Your branch is behind.*by (\d+) commit",
        "description": "Branch jest za remote — pobierz zmiany",
        "command": "git pull",
        "risk": ActionRisk.MEDIUM,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    {
        "pattern": r"Changes not staged|modified:\s+",
        "description": "Niezacommitowane zmiany — pokaż status",
        "command": "git status --short",
        "risk": ActionRisk.SAFE,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    {
        "pattern": r"merge conflict|CONFLICT|Merge conflict",
        "description": "Konflikt merge — pokaż pliki z konfliktami",
        "command": "git diff --name-only --diff-filter=U",
        "risk": ActionRisk.SAFE,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    # Python errors
    {
        "pattern": r"ModuleNotFoundError:\s+No module named '?(\w+)'?",
        "description": "Brakujący moduł Python — zainstaluj",
        "command": "pip install {match_1}",
        "risk": ActionRisk.MEDIUM,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    {
        "pattern": r"SyntaxError:|IndentationError:",
        "description": "Błąd składni Python — sprawdź plik",
        "command": "python -m py_compile {file}",
        "risk": ActionRisk.SAFE,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    # Node.js errors
    {
        "pattern": r"Cannot find module '([^']+)'",
        "description": "Brakujący moduł Node.js — zainstaluj",
        "command": "npm install {match_1}",
        "risk": ActionRisk.MEDIUM,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    # Docker
    {
        "pattern": r"docker.*permission denied|Cannot connect to.*Docker",
        "description": "Docker wymaga uprawnień — sprawdź grupę",
        "command": "groups | grep docker && docker info",
        "risk": ActionRisk.SAFE,
        "categories": [AppCategory.TERMINAL, AppCategory.IDE, AppCategory.SYSTEM],
    },
    # Disk space
    {
        "pattern": r"No space left on device|ENOSPC|brak miejsca",
        "description": "Brak miejsca na dysku — analiza użycia",
        "command": "df -h && du -sh /tmp/* 2>/dev/null | sort -rh | head -10",
        "risk": ActionRisk.SAFE,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL, AppCategory.FILE_MANAGER, AppCategory.SYSTEM],
    },
    # Build errors
    {
        "pattern": r"make\[?\d?\]?:\s+\*\*\*.*Error",
        "description": "Błąd kompilacji make — pokaż szczegóły",
        "command": "make 2>&1 | tail -20",
        "risk": ActionRisk.LOW,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL],
    },
    # Service issues
    {
        "pattern": r"Connection refused|ECONNREFUSED|port (\d+)",
        "description": "Połączenie odrzucone — sprawdź usługę",
        "command": "ss -tlnp | grep {match_1:-8000}",
        "risk": ActionRisk.SAFE,
        "categories": [AppCategory.IDE, AppCategory.TERMINAL, AppCategory.BROWSER, AppCategory.SYSTEM],
    },
]


class ShellAgent:
    """
    Safe shell command execution engine.
    
    Auto-executes only SAFE commands.
    MEDIUM+ risk requires explicit user approval via API.
    """

    def __init__(
        self,
        auto_execute_safe: bool = True,
        max_output_length: int = 2000,
        command_timeout: float = 10.0,
        max_history: int = 200,
        cwd: Optional[str] = None,
    ):
        self.auto_execute_safe = auto_execute_safe
        self.max_output_length = max_output_length
        self.command_timeout = command_timeout
        self.max_history = max_history
        self.default_cwd = cwd or os.path.expanduser("~")

        # History and stats
        self.history: List[AgentAction] = []
        self.pending_actions: Dict[str, AgentAction] = {}
        self.total_suggestions = 0
        self.total_executions = 0
        self.total_blocked = 0

        logger.info(
            "ShellAgent initialized",
            auto_execute_safe=auto_execute_safe,
            timeout=command_timeout,
        )

    def suggest_actions(
        self,
        detected_text: str,
        category: AppCategory,
        cwd: Optional[str] = None,
    ) -> List[AgentAction]:
        """
        Analyze detected text and suggest actions based on pattern matching.
        
        Args:
            detected_text: Text from OCR/analysis
            category: Current app category
            cwd: Working directory context
            
        Returns:
            List of suggested AgentAction objects
        """
        actions = []

        for rule in ACTION_RULES:
            # Check if rule applies to current category
            if category not in rule["categories"]:
                continue

            match = re.search(rule["pattern"], detected_text, re.IGNORECASE)
            if not match:
                continue

            # Build command with captured groups
            command = rule["command"]
            for i, group in enumerate(match.groups(), 1):
                if group:
                    command = command.replace(f"{{match_{i}}}", group)
            # Clean up unreplaced placeholders
            command = re.sub(r"\{match_\d+(?::-[^}]*)?\}", "", command)
            command = re.sub(r"\{file\}", "", command)

            action_id = f"action_{int(time.time() * 1000)}_{len(actions)}"
            action = AgentAction(
                action_id=action_id,
                description=rule["description"],
                command=command.strip(),
                risk=rule["risk"],
                category=category,
                trigger_pattern=rule["pattern"],
                timestamp=time.time(),
            )

            actions.append(action)
            self.pending_actions[action_id] = action

        self.total_suggestions += len(actions)

        if actions:
            logger.info(
                "Agent actions suggested",
                count=len(actions),
                category=category.value,
                actions=[a.description for a in actions],
            )

        return actions

    def execute_action(
        self,
        action_id: str,
        cwd: Optional[str] = None,
        force: bool = False,
    ) -> AgentAction:
        """
        Execute a previously suggested action.
        
        Args:
            action_id: ID of the action to execute
            cwd: Working directory override
            force: Skip risk check (use with caution)
            
        Returns:
            Updated AgentAction with output
        """
        action = self.pending_actions.get(action_id)
        if not action:
            raise ValueError(f"Action not found: {action_id}")

        # Safety check
        if self._is_blocked(action.command):
            action.output = "⛔ Komenda zablokowana ze względów bezpieczeństwa"
            self.total_blocked += 1
            logger.warning("Blocked dangerous command", command=action.command)
            return action

        # Risk check
        if not force and action.risk in (ActionRisk.HIGH, ActionRisk.DANGEROUS):
            action.output = "⚠️ Komenda wymaga ręcznego zatwierdzenia (wysokie ryzyko)"
            return action

        if not force and action.risk == ActionRisk.MEDIUM and not action.approved:
            action.output = "⏳ Oczekuje na zatwierdzenie użytkownika"
            return action

        # Execute
        return self._execute(action, cwd)

    def execute_safe(self, command: str, cwd: Optional[str] = None) -> AgentAction:
        """
        Execute a safe (read-only) command directly.
        Validates against whitelist.
        """
        action = AgentAction(
            action_id=f"direct_{int(time.time() * 1000)}",
            description=f"Direct: {command}",
            command=command,
            risk=ActionRisk.SAFE,
            category=AppCategory.UNKNOWN,
            timestamp=time.time(),
        )

        if not self._is_safe(command):
            action.output = "⚠️ Komenda nie jest na liście bezpiecznych"
            action.risk = ActionRisk.MEDIUM
            return action

        return self._execute(action, cwd)

    def approve_action(self, action_id: str) -> bool:
        """Mark an action as approved by user."""
        action = self.pending_actions.get(action_id)
        if not action:
            return False
        action.approved = True
        logger.info("Action approved", action_id=action_id, command=action.command)
        return True

    def _execute(self, action: AgentAction, cwd: Optional[str] = None) -> AgentAction:
        """Internal command execution."""
        work_dir = cwd or self.default_cwd

        try:
            result = subprocess.run(
                action.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                cwd=work_dir,
                env={**os.environ, "LANG": "C.UTF-8"},
            )

            action.output = result.stdout[:self.max_output_length]
            if result.stderr:
                action.output += f"\n--- stderr ---\n{result.stderr[:500]}"
            action.exit_code = result.returncode
            action.executed = True
            self.total_executions += 1

            logger.info(
                "Command executed",
                command=action.command,
                exit_code=action.exit_code,
                output_length=len(action.output),
            )

        except subprocess.TimeoutExpired:
            action.output = f"⏱️ Timeout po {self.command_timeout}s"
            action.exit_code = -1
            action.executed = True
        except Exception as e:
            action.output = f"❌ Błąd: {str(e)}"
            action.exit_code = -1
            action.executed = True
            logger.error("Command execution failed", command=action.command, error=str(e))

        # Add to history
        self.history.append(action)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        # Remove from pending
        self.pending_actions.pop(action.action_id, None)

        return action

    @staticmethod
    def _is_blocked(command: str) -> bool:
        """Check if command matches any blocked pattern."""
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _is_safe(command: str) -> bool:
        """Check if command is in the safe whitelist."""
        cmd_base = command.split()[0] if command.split() else ""
        # Check exact match
        if command in SAFE_COMMANDS:
            return True
        # Check prefix match (e.g., "git status" matches "git status --short")
        for safe_cmd in SAFE_COMMANDS:
            if command.startswith(safe_cmd):
                return True
        # Check base command
        if cmd_base in {"ls", "cat", "head", "tail", "wc", "file", "stat",
                         "find", "tree", "du", "df", "free", "uptime", "date",
                         "whoami", "hostname", "uname", "id", "lscpu", "lsblk"}:
            return True
        return False

    def get_clipboard_command(self, text: str) -> str:
        """Generate command to copy text to clipboard (Linux)."""
        # Escape for shell
        escaped = shlex.quote(text)
        return f"echo {escaped} | xclip -selection clipboard"

    def get_pending_actions(self) -> List[Dict]:
        """Get all pending (unapproved) actions."""
        return [a.to_dict() for a in self.pending_actions.values()]

    def get_history(self, n: int = 20) -> List[Dict]:
        """Get recent action history."""
        return [a.to_dict() for a in self.history[-n:]]

    def get_stats(self) -> Dict:
        """Get ShellAgent statistics."""
        return {
            "total_suggestions": self.total_suggestions,
            "total_executions": self.total_executions,
            "total_blocked": self.total_blocked,
            "pending_actions": len(self.pending_actions),
            "history_size": len(self.history),
            "auto_execute_safe": self.auto_execute_safe,
        }


def create_shell_agent_from_env(settings=None) -> ShellAgent:
    """Create ShellAgent from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    if not settings.enable_shell_agent:
        return None

    return ShellAgent(
        auto_execute_safe=settings.agent_auto_execute,
        max_output_length=settings.agent_max_output,
        command_timeout=settings.agent_timeout,
    )
