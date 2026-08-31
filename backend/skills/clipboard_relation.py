"""
ClipboardRelationSkill — Detect intent from relationship between selection and clipboard.

Analyzes the *pair* (selected_text, clipboard_content) to infer what the user
wants to do. This goes beyond single-text classification by looking at how
the two texts relate to each other.

Priority: 80 (between ErrorFixer=85 and URLHandler=70)

Intent Catalog (16 detectors, sorted by confidence score):

  Score  Intent               Selection example       Clipboard example
  ─────  ───────────────────  ─────────────────────   ──────────────────────────
  0.95   already_copied       any text                same text (>90% similar)
  0.92   error_file_match     app.py                  Traceback mentioning app.py
  0.88   complement_cmd       flask                   ModuleNotFoundError: flask
  0.86   stack_trace_symbol   handle_request          stack trace with handle_request
  0.85   ip_conn_error        192.168.1.100           connection refused
  0.84   env_var_missing      API_KEY                 API_KEY is not set
  0.83   docker_error         a1b2c3d4e5f6            docker error for container
  0.82   git_diff_ref         abc1234                 diff --git a/file.py ...
  0.78   cross_language       Polish text             English text
  0.76   env_var_match        DB_URL=postgres://...   config referencing DB_URL
  0.73   config_key_match     server.port             config block with server.port
  0.72   json_pair            {"a": 1}                {"b": 2}
  0.70   url_pair             github.com/repo1        github.com/repo2
  0.70   git_compare          main                    develop
  0.68   regex_test           ^\\d{3}-\\d{4}$           test data
  0.65   save_to_path         /tmp/out.txt            long content to save

  Variable-score intents:
  0.6-0.8  code_similarity    def hello(): ...        def hello(): ... (similar)
  0.4-0.7  diff_fragments     text fragment A         text fragment B (similar)
  0.55     ip_pair            10.0.0.1                10.0.0.2
  0.58     docker_context     FROM python:3.11        docker build -t myapp .

Signal extraction methods:
- Language detection: 5 languages (en/pl/de/fr/es) + Cyrillic/CJK script
- Text similarity: SequenceMatcher ratio (0.0–1.0, capped at 500 chars)
- Domain extraction: URL parsing for same-domain detection
- Pattern matching: 12 compiled regexes (URL, path, package, error, code,
  JSON, git ref/diff, IP, env var, docker, config key, stack frame, regex)
"""
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from skills.base import (
    BaseSkill,
    SkillCategory,
    SkillContext,
    SkillOption,
    SkillResult,
    OptionRisk,
)

import structlog

logger = structlog.get_logger()

# ── Language detection (lightweight, reused from TranslationSkill patterns) ──

_LANG_WORDS: Dict[str, Set[str]] = {
    "en": {"the", "is", "are", "was", "have", "has", "been", "will", "with", "from",
            "this", "that", "which", "about", "your", "they", "their", "what", "where",
            "over", "for", "not", "but", "you", "all", "can", "her", "one", "our",
            "out", "day", "had", "hot", "how", "its", "may", "old", "see", "now",
            "way", "who", "did", "get", "let", "say", "she", "too", "use", "just",
            "than", "them", "then", "very", "when", "come", "each", "make", "like",
            "long", "look", "many", "some", "time", "been", "would", "could", "other",
            "into", "more", "also", "back", "after", "work", "first", "even", "new",
            "because", "good", "give", "most", "only", "tell", "before", "between",
            "quick", "brown", "fox", "jumps", "lazy", "dog"},
    "pl": {"jest", "nie", "się", "jak", "ale", "czy", "już", "tak", "lub", "dla",
            "tym", "jego", "też", "tylko", "przez", "może", "bardzo", "jeszcze"},
    "de": {"der", "die", "das", "und", "ist", "ein", "eine", "nicht", "auf", "mit"},
    "fr": {"les", "des", "une", "est", "dans", "pour", "que", "pas", "sur", "avec"},
    "es": {"los", "las", "una", "del", "que", "por", "con", "para", "como", "más"},
}

_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]")

# ── Pattern constants ──

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:^|[\s\"'])([/~][\w./-]+|[A-Z]:\\[\w.\\/-]+)", re.MULTILINE)
_PACKAGE_RE = re.compile(r"^[\w][\w.-]{1,60}$")
_ERROR_RE = re.compile(
    r"(error|exception|traceback|failed|fatal|ModuleNotFoundError|ImportError|Cannot find module)",
    re.IGNORECASE,
)
_FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')
_CODE_RE = re.compile(r"(def |class |import |function |const |let |var |fn |pub )")
_JSON_RE = re.compile(r'^\s*[{\[]', re.MULTILINE)
_GIT_REF_RE = re.compile(r'^[0-9a-f]{7,40}$|^(refs/|HEAD|origin/|main|master|develop)', re.MULTILINE)
_GIT_DIFF_RE = re.compile(r'^(diff --git|@@\s|[+-]{3}\s)', re.MULTILINE)
_IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[0-9a-fA-F:]{6,39}\b')
_HOST_PORT_RE = re.compile(r'\b[\w.-]+:\d{2,5}\b')
_ENV_VAR_RE = re.compile(r'^[A-Z][A-Z0-9_]{2,}=', re.MULTILINE)
_ENV_REF_RE = re.compile(r'\$\{?[A-Z][A-Z0-9_]{2,}\}?|\b[A-Z][A-Z0-9_]{2,}\b')
_DOCKER_RE = re.compile(r'(docker|container|image|Dockerfile|docker-compose|ENTRYPOINT|FROM\s+\w)', re.IGNORECASE)
_CONFIG_KEY_RE = re.compile(r'^[\w.-]+\s*[:=]\s*', re.MULTILINE)
_STACK_FRAME_RE = re.compile(r'at\s+[\w.$]+\(|File "[^"]+", line \d+|\w+\.\w+:\d+', re.MULTILINE)
_REGEX_RE = re.compile(r'[\^$*+?{}\[\]|\\].*[\^$*+?{}\[\]|\\]')


def _detect_lang(text: str) -> str:
    """Lightweight language detection → lang code or 'unknown'."""
    if _CYRILLIC_RE.search(text):
        return "ru"
    if _CJK_RE.search(text):
        return "zh"
    words = set(re.findall(r"\b\w{2,}\b", text.lower()))
    best, best_score = "unknown", 0
    for lang, hints in _LANG_WORDS.items():
        score = len(words & hints)
        if score > best_score:
            best, best_score = lang, score
    return best if best_score >= 2 else "unknown"


def _detect_lang_pair(text_a: str, text_b: str) -> Tuple[str, str]:
    """Detect languages of both texts. Returns (lang_a, lang_b)."""
    return _detect_lang(text_a), _detect_lang(text_b)


def _text_similarity(a: str, b: str) -> float:
    """Quick similarity ratio 0.0–1.0 between two texts."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:500], b[:500]).ratio()


def _extract_domain(text: str) -> str:
    """Extract domain from first URL in text, or empty string."""
    m = _URL_RE.search(text)
    if m:
        try:
            return urlparse(m.group()).netloc.lower()
        except Exception:
            pass
    return ""


# ── Intent dataclass ──

class _Intent:
    """Internal scored intent."""
    __slots__ = ("name", "score", "label", "icon", "options_fn")

    def __init__(self, name: str, score: float, label: str, icon: str, options_fn=None):
        self.name = name
        self.score = score
        self.label = label
        self.icon = icon
        self.options_fn = options_fn


class ClipboardRelationSkill(BaseSkill):
    """Detect intent from the relationship between selected text and clipboard content."""

    name = "clipboard_relation"
    category = SkillCategory.CLIPBOARD
    icon = "📎"
    priority = 80
    _OPTION_DISPATCH: Dict[str, str] = {
        "copy_both": "_execute_copy_both_option",
        "show_diff": "_execute_show_diff_option",
        "replace_clipboard": "_execute_replace_clipboard_option",
        "translate_pair": "_execute_translate_pair_option",
        "install_package": "_execute_install_package_option",
        "open_error_file": "_execute_open_error_file_option",
        "save_to_file": "_execute_save_to_file_option",
        "git_show": "_execute_git_show_option",
        "git_diff_range": "_execute_git_diff_range_option",
        "ping_host": "_execute_ping_host_option",
        "check_port": "_execute_check_port_option",
        "docker_logs": "_execute_docker_logs_option",
        "docker_inspect": "_execute_docker_inspect_option",
        "env_export": "_execute_env_export_option",
        "regex_match": "_execute_regex_match_option",
        "json_diff": "_execute_json_diff_option",
        "search_pair": "_execute_search_pair_option",
    }

    def detect(self, text: str, ctx: SkillContext) -> float:
        if not ctx.clipboard_top:
            return 0.0
        intent = self._best_intent(text, ctx)
        return intent.score if intent else 0.0

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        intent = self._best_intent(text, ctx)
        if not intent:
            return []
        if intent.options_fn:
            return intent.options_fn(text, ctx)
        return self._default_options(text, ctx, intent)

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        clipboard = ctx.clipboard_top or ""
        handler_name = self._OPTION_DISPATCH.get(option_id)
        if not handler_name:
            return SkillResult(success=False, error=f"Unknown option: {option_id}")
        handler = getattr(self, handler_name)
        return await handler(text, clipboard, ctx)

    async def _execute_copy_both_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        combined = f"--- Zaznaczenie ---\n{text}\n\n--- Schowek ---\n{clipboard}"
        return SkillResult(success=True, message="📋 Skopiowano oba fragmenty", clipboard_text=combined)

    async def _execute_show_diff_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        diff = self._make_diff(text, clipboard)
        return SkillResult(success=True, message="📊 Porównanie fragmentów", output=diff, clipboard_text=diff)

    async def _execute_replace_clipboard_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        return SkillResult(success=True, message="📋 Zastąpiono schowek zaznaczeniem", clipboard_text=text)

    async def _execute_translate_pair_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        return SkillResult(
            success=True,
            message="🌐 Para do tłumaczenia skopiowana",
            clipboard_text=f"{text}\n---\n{clipboard}",
        )

    async def _execute_install_package_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        pkg = text.strip().split()[0] if text.strip() else ""
        cmd = f"pip install {pkg}"
        import subprocess
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60, cwd=ctx.cwd or None)
            output = (result.stdout + result.stderr)[:2000]
            ok = result.returncode == 0
            return SkillResult(success=ok, message=f"{'✅' if ok else '❌'} {cmd}", output=output)
        except Exception as e:
            return SkillResult(success=False, error=str(e))

    async def _execute_open_error_file_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        m = _FILE_LINE_RE.search(clipboard)
        if m:
            filepath, line = m.group(1), m.group(2)
            return SkillResult(success=True, message=f"📂 Otwórz {filepath}:{line}", clipboard_text=f"{filepath}:{line}")
        return SkillResult(success=False, error="Nie znaleziono pliku w schowku")

    async def _execute_save_to_file_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        path = text.strip()
        try:
            with open(path, "w") as f:
                f.write(clipboard)
            return SkillResult(success=True, message=f"💾 Zapisano do {path}")
        except Exception as e:
            return SkillResult(success=False, error=str(e))

    async def _execute_git_show_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        ref = text.strip()[:40]
        return await self._run_cmd(f"git show --stat {ref}", ctx.cwd, f"🔀 git show {ref}")

    async def _execute_git_diff_range_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        ref_a = text.strip()[:40]
        ref_b = clipboard.strip()[:40]
        return await self._run_cmd(f"git diff --stat {ref_a}..{ref_b}", ctx.cwd, f"🔀 git diff {ref_a}..{ref_b}")

    async def _execute_ping_host_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        host = re.sub(r':\d+$', '', text.strip().split()[0])
        return await self._run_cmd(f"ping -c 3 -W 2 {host}", None, f"🌐 ping {host}")

    async def _execute_check_port_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        m = _HOST_PORT_RE.search(text)
        if m:
            target = m.group()
            host, port = target.rsplit(":", 1)
            return await self._run_cmd(
                f"timeout 3 bash -c 'echo > /dev/tcp/{host}/{port}' 2>&1 && echo 'Port {port} OPEN' || echo 'Port {port} CLOSED'",
                None, f"🌐 Sprawdzam {target}",
            )
        return SkillResult(success=False, error="Nie znaleziono host:port")

    async def _execute_docker_logs_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        container = text.strip()[:64]
        return await self._run_cmd(f"docker logs --tail 30 {container}", None, f"🐳 docker logs {container}")

    async def _execute_docker_inspect_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        container = text.strip()[:64]
        return await self._run_cmd(f"docker inspect --format '{{{{.State.Status}}}}' {container}", None, f"🐳 docker inspect {container}")

    async def _execute_env_export_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        line = text.strip()
        if "=" in line:
            return SkillResult(success=True, message="⚙️ Skopiowano export", clipboard_text=f"export {line}")
        return SkillResult(success=True, message="⚙️ Skopiowano export", clipboard_text=f"export {line}=")

    async def _execute_regex_match_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        return self._execute_regex_match(text, clipboard)

    async def _execute_json_diff_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        return self._execute_json_diff(text, clipboard)

    async def _execute_search_pair_option(self, text: str, clipboard: str, ctx: SkillContext) -> SkillResult:
        query = f"{text[:40]} {clipboard[:40]}".strip()
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        return SkillResult(success=True, message="🔍 Szukam kontekstu", open_url=url)

    @staticmethod
    async def _run_cmd(cmd: str, cwd: Optional[str], label: str) -> SkillResult:
        """Run a shell command and return the result."""
        import subprocess
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=15, cwd=cwd or None,
            )
            output = (result.stdout + result.stderr)[:2000]
            ok = result.returncode == 0
            return SkillResult(
                success=ok,
                message=f"{'✅' if ok else '❌'} {label}",
                output=output,
                clipboard_text=output.strip() if output.strip() else cmd,
            )
        except subprocess.TimeoutExpired:
            return SkillResult(success=False, message=f"⏱️ Timeout: {label}", error="timeout")
        except Exception as e:
            return SkillResult(success=False, error=str(e))

    @staticmethod
    def _execute_regex_match(text: str, clipboard: str) -> SkillResult:
        """Test regex (from one side) against data (from the other)."""
        sel_is_regex = bool(_REGEX_RE.search(text)) and len(text.strip()) < 200
        pattern_str = text.strip() if sel_is_regex else clipboard.strip()
        test_data = clipboard if sel_is_regex else text
        try:
            compiled = re.compile(pattern_str, re.MULTILINE)
            matches = compiled.findall(test_data)
            if matches:
                match_list = "\n".join(str(m) for m in matches[:20])
                return SkillResult(
                    success=True,
                    message=f"🔣 {len(matches)} dopasowań",
                    output=f"Pattern: {pattern_str}\n\nMatches ({len(matches)}):\n{match_list}",
                    clipboard_text=match_list,
                )
            return SkillResult(
                success=True,
                message="🔣 Brak dopasowań",
                output=f"Pattern: {pattern_str}\n\nNo matches in:\n{test_data[:200]}",
            )
        except re.error as e:
            return SkillResult(success=False, error=f"Regex error: {e}")

    @staticmethod
    def _execute_json_diff(text: str, clipboard: str) -> SkillResult:
        """Pretty-print and diff two JSON objects."""
        import json
        try:
            obj_a = json.loads(text)
            obj_b = json.loads(clipboard)
        except json.JSONDecodeError as e:
            return SkillResult(success=False, error=f"JSON parse error: {e}")

        pretty_a = json.dumps(obj_a, indent=2, ensure_ascii=False, sort_keys=True)
        pretty_b = json.dumps(obj_b, indent=2, ensure_ascii=False, sort_keys=True)

        if pretty_a == pretty_b:
            return SkillResult(success=True, message="📋 JSON identyczne (po normalizacji)", output=pretty_a, clipboard_text=pretty_a)

        lines_a = pretty_a.splitlines()
        lines_b = pretty_b.splitlines()
        output = ["--- Zaznaczenie (JSON)", "+++ Schowek (JSON)", ""]
        sm = SequenceMatcher(None, lines_a, lines_b)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for line in lines_a[i1:i2]:
                    output.append(f"  {line}")
            elif tag == "replace":
                for line in lines_a[i1:i2]:
                    output.append(f"- {line}")
                for line in lines_b[j1:j2]:
                    output.append(f"+ {line}")
            elif tag == "delete":
                for line in lines_a[i1:i2]:
                    output.append(f"- {line}")
            elif tag == "insert":
                for line in lines_b[j1:j2]:
                    output.append(f"+ {line}")
        diff_text = "\n".join(output[:150])
        return SkillResult(success=True, message="📊 JSON diff", output=diff_text, clipboard_text=diff_text)

    # ── Intent detection engine ──

    def _best_intent(self, text: str, ctx: SkillContext) -> Optional[_Intent]:
        """Score all intents and return the best one."""
        clipboard = ctx.clipboard_top
        if not clipboard:
            return None

        intents = [
            self._check_already_copied(text, clipboard),
            self._check_error_file_match(text, clipboard),
            self._check_cross_language(text, clipboard, ctx),
            self._check_complement_cmd(text, clipboard),
            self._check_url_pair(text, clipboard),
            self._check_save_to_path(text, clipboard),
            self._check_code_similarity(text, clipboard),
            self._check_diff_fragments(text, clipboard),
            # ── Expanded intent detectors ──
            self._check_json_pair(text, clipboard),
            self._check_git_context(text, clipboard),
            self._check_ip_host(text, clipboard),
            self._check_env_var(text, clipboard),
            self._check_docker_context(text, clipboard),
            self._check_config_key_value(text, clipboard),
            self._check_stack_trace_context(text, clipboard),
            self._check_regex_test(text, clipboard),
        ]

        valid = [i for i in intents if i and i.score > 0]
        if not valid:
            return None
        return max(valid, key=lambda i: i.score)

    def _check_already_copied(self, text: str, clipboard: str) -> Optional[_Intent]:
        sim = _text_similarity(text.strip(), clipboard.strip())
        if sim > 0.9:
            return _Intent("already_copied", 0.95, "Już w schowku", "✅")
        return None

    def _check_error_file_match(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is a file path/name, clipboard has traceback mentioning that file."""
        clean = text.strip().split("\n")[0].strip().strip("'\"")
        # Reject IP addresses (e.g. 192.168.1.100)
        if re.match(r'^\d{1,3}(\.\d{1,3}){2,}$', clean):
            return None
        # Accept full paths OR bare filenames with extension (e.g. app.py, main.rs)
        is_path = bool(_PATH_RE.search(clean))
        is_filename = bool(re.match(r"^[\w./-]+\.\w{1,10}$", clean))
        if not is_path and not is_filename:
            return None
        if not _ERROR_RE.search(clipboard):
            return None
        basename = clean.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if basename and basename in clipboard:
            return _Intent(
                "error_file_match", 0.92,
                f"Błąd w pliku {basename}", "🔧",
                options_fn=self._error_file_options,
            )
        return None

    def _check_cross_language(self, text: str, clipboard: str, ctx: SkillContext) -> Optional[_Intent]:
        lang_sel = _detect_lang(text)
        lang_clip = _detect_lang(clipboard)
        if lang_sel == "unknown" or lang_clip == "unknown":
            return None
        if lang_sel != lang_clip:
            return _Intent(
                "cross_language", 0.78,
                f"Para językowa: {lang_sel} ↔ {lang_clip}", "🌐",
                options_fn=self._translate_options,
            )
        return None

    def _check_complement_cmd(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is a package name, clipboard has install error for it."""
        clean = text.strip()
        if not _PACKAGE_RE.match(clean):
            return None
        if not _ERROR_RE.search(clipboard):
            return None
        # Exclude things that look like non-package identifiers
        if re.match(r'^[A-Z][A-Z0-9_]{2,}$', clean):
            return None  # ENV_VAR style → handled by _check_env_var
        if re.match(r'^[a-f0-9]{7,}$', clean):
            return None  # git hash or docker ID
        if _IP_RE.match(clean):
            return None  # IP address
        if '_' in clean:
            return None  # snake_case → function/variable name, not a package
        if re.match(r'^[a-z]+[A-Z]', clean):
            return None  # camelCase → function/class name
        if clean.lower() in clipboard.lower():
            return _Intent(
                "complement_cmd", 0.88,
                f"Zainstaluj brakujący: {clean}", "📦",
                options_fn=self._install_options,
            )
        return None

    def _check_url_pair(self, text: str, clipboard: str) -> Optional[_Intent]:
        dom_sel = _extract_domain(text)
        dom_clip = _extract_domain(clipboard)
        if dom_sel and dom_clip:
            if dom_sel == dom_clip:
                return _Intent("url_pair", 0.7, f"Dwa URL z {dom_sel}", "🔗")
            return _Intent("url_pair", 0.5, "Dwa różne URL", "🔗")
        return None

    def _check_save_to_path(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is a file path, clipboard has content to save."""
        if not _PATH_RE.match(text.strip()):
            return None
        if len(clipboard) > 20 and not _PATH_RE.match(clipboard.strip()):
            return _Intent(
                "save_to_path", 0.65,
                f"Zapisz schowek do {text.strip()[:40]}", "💾",
                options_fn=self._save_options,
            )
        return None

    def _check_code_similarity(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Both look like code and are somewhat similar → refactor/replace."""
        if not _CODE_RE.search(text) or not _CODE_RE.search(clipboard):
            return None
        sim = _text_similarity(text, clipboard)
        if 0.3 < sim < 0.9:
            return _Intent(
                "code_similarity", 0.6 + sim * 0.2,
                f"Podobny kod (dopasowanie {sim:.0%})", "🔄",
            )
        return None

    def _check_diff_fragments(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Both are text fragments that are somewhat similar → show diff."""
        sim = _text_similarity(text, clipboard)
        if 0.2 < sim < 0.9 and len(text) > 20 and len(clipboard) > 20:
            return _Intent("diff_fragments", 0.4 + sim * 0.3, f"Porównaj fragmenty ({sim:.0%})", "📊")
        return None

    # ── Expanded intent detectors ──

    def _check_json_pair(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Both look like JSON → compare/merge/validate."""
        t_json = bool(_JSON_RE.search(text)) and ('{' in text or '[' in text)
        c_json = bool(_JSON_RE.search(clipboard)) and ('{' in clipboard or '[' in clipboard)
        if not t_json or not c_json:
            return None
        sim = _text_similarity(text, clipboard)
        if sim > 0.9:
            return None  # already_copied handles this
        return _Intent(
            "json_pair", 0.72,
            f"Dwa obiekty JSON (podobieństwo {sim:.0%})", "📋",
            options_fn=self._json_options,
        )

    def _check_git_context(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is a git ref/hash, clipboard has diff or commit context."""
        sel_is_ref = bool(_GIT_REF_RE.search(text.strip()))
        clip_has_diff = bool(_GIT_DIFF_RE.search(clipboard))
        clip_has_ref = bool(_GIT_REF_RE.search(clipboard.strip()))
        if sel_is_ref and clip_has_diff:
            return _Intent(
                "git_diff_ref", 0.82,
                f"Git ref + diff", "🔀",
                options_fn=self._git_options,
            )
        if sel_is_ref and clip_has_ref and text.strip() != clipboard.strip():
            return _Intent(
                "git_compare", 0.70,
                "Porównaj dwa commity/branche", "🔀",
                options_fn=self._git_compare_options,
            )
        return None

    def _check_ip_host(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is IP/host, clipboard has connection error or config."""
        sel_ip = bool(_IP_RE.search(text)) or bool(_HOST_PORT_RE.search(text))
        clip_ip = bool(_IP_RE.search(clipboard)) or bool(_HOST_PORT_RE.search(clipboard))
        if not sel_ip:
            return None
        has_conn_error = bool(re.search(r'(connection refused|timeout|unreachable|ECONNREFUSED|no route)', clipboard, re.IGNORECASE))
        if has_conn_error:
            return _Intent(
                "ip_conn_error", 0.85,
                f"Błąd połączenia z {text.strip()[:30]}", "🌐",
                options_fn=self._ip_error_options,
            )
        if clip_ip:
            return _Intent(
                "ip_pair", 0.55,
                "Dwa adresy sieciowe", "🌐",
            )
        return None

    def _check_env_var(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is env var name/value, clipboard has config or error referencing it."""
        sel_is_env = bool(_ENV_VAR_RE.search(text))
        clip_refs_env = bool(_ENV_REF_RE.search(clipboard))
        if sel_is_env and clip_refs_env:
            var_name = text.strip().split('=')[0].strip()
            if var_name.upper() in clipboard.upper():
                return _Intent(
                    "env_var_match", 0.76,
                    f"Zmienna {var_name} w kontekście", "⚙️",
                    options_fn=self._env_options,
                )
        # Selection is a var name, clipboard has "not set" / "undefined" error
        clean = text.strip()
        if re.match(r'^[A-Z][A-Z0-9_]{2,}$', clean):
            if re.search(r'(not set|undefined|missing|required)', clipboard, re.IGNORECASE) and clean in clipboard:
                return _Intent(
                    "env_var_missing", 0.84,
                    f"Brakująca zmienna: {clean}", "⚙️",
                    options_fn=self._env_missing_options,
                )
        return None

    def _check_docker_context(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection + clipboard both reference Docker → container management."""
        sel_docker = bool(_DOCKER_RE.search(text))
        clip_docker = bool(_DOCKER_RE.search(clipboard))
        if not sel_docker and not clip_docker:
            return None
        # Selection is container ID/name, clipboard has docker error
        if re.match(r'^[a-f0-9]{12,64}$', text.strip()) and _ERROR_RE.search(clipboard) and clip_docker:
            return _Intent(
                "docker_error", 0.83,
                f"Błąd kontenera {text.strip()[:12]}", "🐳",
                options_fn=self._docker_options,
            )
        if sel_docker and clip_docker:
            return _Intent(
                "docker_context", 0.58,
                "Kontekst Docker", "🐳",
            )
        return None

    def _check_config_key_value(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is a config key, clipboard has config block or error about that key."""
        clean = text.strip()
        # Single config key (e.g. "DATABASE_URL" or "server.port")
        is_config_key = bool(re.match(r'^[\w][-\w.]{1,60}$', clean))
        if not is_config_key:
            return None
        clip_has_config = bool(_CONFIG_KEY_RE.search(clipboard))
        key_in_clip = clean.lower() in clipboard.lower()
        if clip_has_config and key_in_clip:
            return _Intent(
                "config_key_match", 0.73,
                f"Klucz konfiguracji: {clean}", "🔧",
                options_fn=self._config_options,
            )
        return None

    def _check_stack_trace_context(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection is a function/class name, clipboard has stack trace mentioning it."""
        clean = text.strip()
        # Function or class name pattern (e.g. "MyClass.my_method" or "handle_request")
        is_symbol = bool(re.match(r'^[A-Za-z_][\w.]{1,80}$', clean))
        if not is_symbol:
            return None
        clip_has_stack = bool(_STACK_FRAME_RE.search(clipboard))
        if not clip_has_stack:
            return None
        if clean in clipboard:
            return _Intent(
                "stack_trace_symbol", 0.86,
                f"Symbol {clean} w stack trace", "🔍",
                options_fn=self._stack_trace_options,
            )
        return None

    def _check_regex_test(self, text: str, clipboard: str) -> Optional[_Intent]:
        """Selection looks like a regex, clipboard has test data (or vice versa)."""
        sel_is_regex = bool(_REGEX_RE.search(text)) and len(text.strip()) < 200
        clip_is_regex = bool(_REGEX_RE.search(clipboard)) and len(clipboard.strip()) < 200
        if sel_is_regex and not clip_is_regex and len(clipboard) > 5:
            # Selection = regex, clipboard = test data
            return _Intent(
                "regex_test", 0.68,
                "Testuj regex na danych ze schowka", "🔣",
                options_fn=self._regex_options,
            )
        if clip_is_regex and not sel_is_regex and len(text) > 5:
            # Clipboard = regex, selection = test data
            return _Intent(
                "regex_test", 0.68,
                "Testuj regex ze schowka na zaznaczeniu", "🔣",
                options_fn=self._regex_options,
            )
        return None

    # ── Option builders ──

    def _default_options(self, text: str, ctx: SkillContext, intent: _Intent) -> List[SkillOption]:
        options = []
        if intent.name == "already_copied":
            options.append(SkillOption(
                id="replace_clipboard", label="🔄 Zastąp schowek nowym zaznaczeniem",
                icon="🔄", data={"extracted": text[:100]},
            ))
        elif intent.name in ("code_similarity", "diff_fragments"):
            options.append(SkillOption(
                id="show_diff", label="📊 Pokaż różnice",
                icon="📊", data={"extracted": text[:100]},
            ))
            options.append(SkillOption(
                id="replace_clipboard", label="🔄 Zastąp schowek zaznaczeniem",
                icon="🔄", data={"extracted": text[:100]},
            ))
        elif intent.name == "url_pair":
            options.append(SkillOption(
                id="copy_both", label="📋 Kopiuj oba URL",
                icon="📋", data={"extracted": text[:100]},
            ))

        # Always offer generic options
        options.append(SkillOption(
            id="copy_both", label="📋 Kopiuj oba fragmenty",
            icon="📋", data={"extracted": text[:100]},
        ))
        options.append(SkillOption(
            id="search_pair", label="🔍 Szukaj kontekstu online",
            icon="🔍", data={"extracted": text[:100]},
        ))
        return options

    def _error_file_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        m = _FILE_LINE_RE.search(ctx.clipboard_top)
        line_info = f":{m.group(2)}" if m else ""
        path = text.strip().split("\n")[0].strip().strip("'\"")
        return [
            SkillOption(
                id="open_error_file", label=f"📂 Otwórz {path}{line_info}",
                icon="📂", description="Otwórz plik na linii z błędu",
                data={"extracted": f"{path}{line_info}"},
            ),
            SkillOption(
                id="copy_both", label="📋 Kopiuj ścieżkę + błąd",
                icon="📋", data={"extracted": text[:100]},
            ),
            SkillOption(
                id="search_pair", label="🔍 Szukaj rozwiązania",
                icon="🔍", data={"extracted": text[:100]},
            ),
        ]

    def _translate_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        return [
            SkillOption(
                id="translate_pair", label="🌐 Kopiuj parę do tłumaczenia",
                icon="🌐", description="Zaznaczenie + schowek jako para językowa",
                data={"extracted": text[:100]},
            ),
            SkillOption(
                id="copy_both", label="📋 Kopiuj oba fragmenty",
                icon="📋", data={"extracted": text[:100]},
            ),
        ]

    def _install_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        pkg = text.strip().split()[0] if text.strip() else ""
        return [
            SkillOption(
                id="install_package", label=f"📦 pip install {pkg}",
                icon="📦", description=f"Zainstaluj brakujący pakiet {pkg}",
                risk=OptionRisk.LOW,
                data={"command": f"pip install {pkg}", "extracted": pkg},
            ),
            SkillOption(
                id="search_pair", label=f"🔍 Szukaj {pkg} online",
                icon="🔍", data={"extracted": pkg},
            ),
        ]

    def _json_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        return [
            SkillOption(
                id="json_diff", label="📊 Porównaj JSON (pretty diff)",
                icon="📊", description="Normalizuj i pokaż różnice między obiektami JSON",
                data={"extracted": text[:100]},
            ),
            SkillOption(
                id="copy_both", label="📋 Kopiuj oba JSON",
                icon="📋", data={"extracted": text[:100]},
            ),
            SkillOption(
                id="search_pair", label="🔍 Szukaj schematu",
                icon="🔍", data={"extracted": text[:100]},
            ),
        ]

    def _git_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        ref = text.strip()[:40]
        return [
            SkillOption(
                id="git_show", label=f"🔀 git show {ref}",
                icon="🔀", description="Pokaż szczegóły commita",
                risk=OptionRisk.SAFE,
                data={"command": f"git show --stat {ref}", "extracted": ref},
            ),
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj ref {ref} + diff",
                icon="📋", data={"extracted": ref},
            ),
            SkillOption(
                id="search_pair", label="🔍 Szukaj commita online",
                icon="🔍", data={"extracted": ref},
            ),
        ]

    def _git_compare_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        ref_a = text.strip()[:20]
        ref_b = ctx.clipboard_top.strip()[:20]
        return [
            SkillOption(
                id="git_diff_range", label=f"🔀 git diff {ref_a}..{ref_b}",
                icon="🔀", description="Porównaj dwa commity/branche",
                risk=OptionRisk.SAFE,
                data={"command": f"git diff --stat {ref_a}..{ref_b}", "extracted": f"{ref_a}..{ref_b}"},
            ),
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj {ref_a}..{ref_b}",
                icon="📋", data={"extracted": f"{ref_a}..{ref_b}"},
            ),
            SkillOption(
                id="search_pair", label="🔍 Porównaj online",
                icon="🔍", data={"extracted": f"{ref_a} {ref_b}"},
            ),
        ]

    def _ip_error_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        host = text.strip()[:40]
        options = [
            SkillOption(
                id="ping_host", label=f"🌐 ping {host}",
                icon="🌐", description="Sprawdź dostępność hosta",
                risk=OptionRisk.SAFE,
                data={"command": f"ping -c 3 {host}", "extracted": host},
            ),
        ]
        if _HOST_PORT_RE.search(text):
            options.append(SkillOption(
                id="check_port", label=f"🔌 Sprawdź port {host}",
                icon="🔌", description="Sprawdź czy port jest otwarty",
                risk=OptionRisk.SAFE,
                data={"extracted": host},
            ))
        options.extend([
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj {host} + błąd",
                icon="📋", data={"extracted": host},
            ),
            SkillOption(
                id="search_pair", label="🔍 Diagnozuj połączenie",
                icon="🔍", data={"extracted": host},
            ),
        ])
        return options

    def _env_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        var = text.strip().split('=')[0].strip()
        return [
            SkillOption(
                id="env_export", label=f"⚙️ export {var}=...",
                icon="⚙️", description="Skopiuj jako polecenie export",
                data={"extracted": var},
            ),
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj {var} + kontekst",
                icon="📋", data={"extracted": var},
            ),
            SkillOption(
                id="search_pair", label=f"🔍 Szukaj {var} w dokumentacji",
                icon="🔍", data={"extracted": var},
            ),
        ]

    def _env_missing_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        var = text.strip()
        return [
            SkillOption(
                id="env_export", label=f"⚙️ export {var}=",
                icon="⚙️", description="Skopiuj szablon export",
                data={"extracted": var},
            ),
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj {var} + błąd",
                icon="📋", data={"extracted": var},
            ),
            SkillOption(
                id="search_pair", label=f"🔍 Jak ustawić {var}",
                icon="🔍", data={"extracted": f"{var} environment variable"},
            ),
        ]

    def _docker_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        container = text.strip()[:12]
        return [
            SkillOption(
                id="docker_logs", label=f"🐳 docker logs {container}",
                icon="🐳", description="Pokaż ostatnie 30 linii logów",
                risk=OptionRisk.SAFE,
                data={"command": f"docker logs --tail 30 {container}", "extracted": container},
            ),
            SkillOption(
                id="docker_inspect", label=f"🐳 docker inspect {container}",
                icon="🐳", description="Sprawdź status kontenera",
                risk=OptionRisk.SAFE,
                data={"extracted": container},
            ),
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj kontener + błąd",
                icon="📋", data={"extracted": container},
            ),
            SkillOption(
                id="search_pair", label="🔍 Szukaj rozwiązania Docker",
                icon="🔍", data={"extracted": container},
            ),
        ]

    def _config_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        key = text.strip()
        return [
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj {key} + config",
                icon="📋", data={"extracted": key},
            ),
            SkillOption(
                id="search_pair", label=f"🔍 Szukaj {key} w docs",
                icon="🔍", data={"extracted": key},
            ),
        ]

    def _stack_trace_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        symbol = text.strip()
        return [
            SkillOption(
                id="copy_both", label=f"📋 Kopiuj {symbol} + stack trace",
                icon="📋", data={"extracted": symbol},
            ),
            SkillOption(
                id="search_pair", label=f"🔍 Szukaj {symbol} + błąd",
                icon="🔍", data={"extracted": symbol},
            ),
        ]

    def _regex_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        return [
            SkillOption(
                id="regex_match", label="🔣 Testuj regex lokalnie",
                icon="🔣", description="Uruchom regex na danych i pokaż dopasowania",
                data={"extracted": text[:100]},
            ),
            SkillOption(
                id="copy_both", label="📋 Kopiuj regex + dane testowe",
                icon="📋", data={"extracted": text[:100]},
            ),
            SkillOption(
                id="search_pair", label="🔍 Testuj regex online (regex101)",
                icon="🔍", data={"extracted": text[:100]},
            ),
        ]

    def _save_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        path = text.strip()[:60]
        return [
            SkillOption(
                id="save_to_file", label=f"💾 Zapisz schowek → {path}",
                icon="💾", description="Zapisz zawartość schowka do wskazanego pliku",
                risk=OptionRisk.MEDIUM,
                data={"extracted": path},
            ),
            SkillOption(
                id="copy_both", label="📋 Kopiuj ścieżkę + zawartość",
                icon="📋", data={"extracted": text[:100]},
            ),
        ]

    # ── Helpers ──

    @staticmethod
    def _make_diff(text_a: str, text_b: str) -> str:
        """Simple line-by-line diff display."""
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()
        output = ["--- Zaznaczenie", "+++ Schowek", ""]
        sm = SequenceMatcher(None, lines_a, lines_b)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for line in lines_a[i1:i2]:
                    output.append(f"  {line}")
            elif tag == "replace":
                for line in lines_a[i1:i2]:
                    output.append(f"- {line}")
                for line in lines_b[j1:j2]:
                    output.append(f"+ {line}")
            elif tag == "delete":
                for line in lines_a[i1:i2]:
                    output.append(f"- {line}")
            elif tag == "insert":
                for line in lines_b[j1:j2]:
                    output.append(f"+ {line}")
        return "\n".join(output[:100])  # cap output

    def _label(self, text: str, ctx: SkillContext) -> str:
        intent = self._best_intent(text, ctx)
        return intent.label if intent else "Relacja ze schowkiem"
