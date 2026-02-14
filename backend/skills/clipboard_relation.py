"""
ClipboardRelationSkill — Detect intent from relationship between selection and clipboard.

Analyzes the *pair* (selected_text, clipboard_content) to infer what the user
wants to do. This goes beyond single-text classification by looking at how
the two texts relate to each other.

Detected intents:
- already_copied: selection == clipboard (user re-selected what's already copied)
- error_file_match: selection is a file path, clipboard has error from that file
- cross_language: selection and clipboard are in different languages → translate pair
- code_similarity: both are code fragments → compare/replace/refactor
- complement_cmd: selection is a package name, clipboard has install error → install
- url_pair: both are URLs from same domain → compare pages
- save_to_path: selection is a file path, clipboard has content → save
- diff_fragments: both are similar code/text → show diff

Priority: 80 (between ErrorFixer=85 and URLHandler=70)
"""
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillMatch, SkillOption, SkillResult, OptionRisk,
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
        clipboard = ctx.clipboard_top

        if option_id == "copy_both":
            combined = f"--- Zaznaczenie ---\n{text}\n\n--- Schowek ---\n{clipboard}"
            return SkillResult(success=True, message="📋 Skopiowano oba fragmenty", clipboard_text=combined)

        if option_id == "show_diff":
            diff = self._make_diff(text, clipboard)
            return SkillResult(success=True, message="📊 Porównanie fragmentów", output=diff, clipboard_text=diff)

        if option_id == "replace_clipboard":
            return SkillResult(success=True, message="📋 Zastąpiono schowek zaznaczeniem", clipboard_text=text)

        if option_id == "translate_pair":
            return SkillResult(
                success=True,
                message="🌐 Para do tłumaczenia skopiowana",
                clipboard_text=f"{text}\n---\n{clipboard}",
            )

        if option_id == "install_package":
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

        if option_id == "open_error_file":
            m = _FILE_LINE_RE.search(ctx.clipboard_top)
            if m:
                filepath, line = m.group(1), m.group(2)
                return SkillResult(success=True, message=f"📂 Otwórz {filepath}:{line}", clipboard_text=f"{filepath}:{line}")
            return SkillResult(success=False, error="Nie znaleziono pliku w schowku")

        if option_id == "save_to_file":
            path = text.strip()
            try:
                with open(path, "w") as f:
                    f.write(clipboard)
                return SkillResult(success=True, message=f"💾 Zapisano do {path}")
            except Exception as e:
                return SkillResult(success=False, error=str(e))

        if option_id == "search_pair":
            query = f"{text[:40]} {clipboard[:40]}".strip()
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            return SkillResult(success=True, message=f"🔍 Szukam kontekstu", open_url=url)

        return SkillResult(success=False, error=f"Unknown option: {option_id}")

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
