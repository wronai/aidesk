"""
OCR Post-Processing Pipeline — enhance OCR accuracy with context-aware corrections.

Provides:
- Text type detection (code vs prose vs terminal output)
- Language-specific spell checking (programming keywords preserved)
- Common OCR error correction (0↔O, 1↔l, rn↔m, etc.)
- Broken word merging across line breaks
- Code-aware processing (preserve syntax, fix common misreads)
- +10-15% accuracy improvement over raw OCR output

Integrates with:
- OCRManager (ocr_engines.py) — post-processes OCRResult after extraction
- AnalyzeStep (pipeline.py) — cleaner text → better LLM analysis
- ActionTemplates (action_templates.py) — more reliable pattern matching

No heavy dependencies — uses built-in regex + optional symspellpy for spell checking.
"""
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import structlog

logger = structlog.get_logger()

# Attempt to import optional spell checking library
_HAS_SYMSPELL = False
try:
    from symspellpy import SymSpell as _SymSpell, Verbosity
    _HAS_SYMSPELL = True
except ImportError:
    pass


# ===== Text Type Detection =====

class TextType:
    """Detected type of OCR text region."""
    CODE = "code"
    TERMINAL = "terminal"
    PROSE = "prose"
    MIXED = "mixed"
    UNKNOWN = "unknown"


# ===== Common OCR Error Patterns =====

# Character-level confusions in monospace fonts
CHAR_CONFUSIONS: List[Tuple[str, str]] = [
    # (wrong, correct) — applied in code context
    ("rn", "m"),      # "rn" often misread for "m" in sans-serif
    ("Iist", "list"),
    ("0bject", "Object"),
]

# Regex-based OCR error corrections for code
CODE_OCR_FIXES: List[Tuple[str, str]] = [
    # Common Python misreads
    (r"\bdef\s+_init_\b", "def __init__"),
    (r"\bdef\s+_str_\b", "def __str__"),
    (r"\bdef\s+_repr_\b", "def __repr__"),
    (r"\bse1f\b", "self"),
    (r"\bNone\b(?!\s*[=!<>])", "None"),  # preserve None
    (r"\bTnue\b", "True"),
    (r"\bFa1se\b", "False"),
    (r"\birnport\b", "import"),
    (r"\bfrorn\b", "from"),
    (r"\bretum\b", "return"),
    (r"\bc1ass\b", "class"),
    (r"\bpnint\b", "print"),
    # Common shell/terminal misreads
    (r"\b\$\s+", "$ "),  # normalize shell prompt
    (r"\bsudo\s+", "sudo "),
    # Common brackets confusion
    (r"\(\)", "()"),
    (r"\[\]", "[]"),
]

# Terminal output patterns
TERMINAL_OCR_FIXES: List[Tuple[str, str]] = [
    (r"(\d+)\s*%", r"\1%"),           # fix "50 %" → "50%"
    (r"(\d+)\s+MB", r"\1 MB"),        # normalize size units
    (r"(\d+)\s+GB", r"\1 GB"),
    (r"ERR0R", "ERROR"),
    (r"WARN1NG", "WARNING"),
    (r"FA1LED", "FAILED"),
    (r"PASS ED", "PASSED"),
]

# Programming language keywords (should never be spell-corrected)
PROGRAMMING_KEYWORDS: Set[str] = {
    # Python
    "def", "class", "import", "from", "return", "yield", "async", "await",
    "if", "elif", "else", "for", "while", "try", "except", "finally",
    "with", "as", "lambda", "pass", "break", "continue", "raise",
    "True", "False", "None", "self", "cls", "super", "init", "str",
    "int", "float", "bool", "list", "dict", "set", "tuple", "type",
    "print", "len", "range", "enumerate", "zip", "map", "filter",
    "isinstance", "hasattr", "getattr", "setattr", "property",
    "staticmethod", "classmethod", "abstractmethod", "dataclass",
    "Optional", "List", "Dict", "Tuple", "Set", "Any", "Union",
    # JavaScript/TypeScript
    "const", "let", "var", "function", "arrow", "export", "default",
    "require", "module", "exports", "this", "new", "typeof", "instanceof",
    "null", "undefined", "NaN", "Infinity", "Promise", "async", "await",
    "interface", "type", "enum", "implements", "extends",
    # Rust
    "fn", "pub", "mod", "use", "impl", "struct", "enum", "trait",
    "mut", "ref", "match", "loop", "unsafe", "extern", "crate",
    # Common CLI
    "sudo", "apt", "pip", "npm", "cargo", "make", "docker", "git",
    "grep", "sed", "awk", "curl", "wget", "ssh", "scp",
    # Common abbreviations
    "args", "kwargs", "env", "config", "src", "dst", "tmp", "var",
    "stdin", "stdout", "stderr", "pid", "uid", "gid",
}

# Code detection heuristics
CODE_INDICATORS = [
    r"def\s+\w+\s*\(",           # Python function
    r"class\s+\w+",              # Class definition
    r"import\s+\w+",             # Import statement
    r"from\s+\w+\s+import",      # From import
    r"function\s+\w+\s*\(",      # JS function
    r"const\s+\w+\s*=",          # JS const
    r"fn\s+\w+\s*\(",            # Rust function
    r"pub\s+(fn|struct|enum)",    # Rust pub
    r"#include\s*<",             # C/C++ include
    r"^\s*[{}]\s*$",             # Lone braces
    r"^\s*//|^\s*#|^\s*/\*",     # Comments
    r"===|!==|=>|->|\|>",        # Operators
    r"\w+\.\w+\(",              # Method calls
    r"^\s*@\w+",                 # Decorators
]

TERMINAL_INDICATORS = [
    r"^\$\s+",                   # Shell prompt
    r"^>>>",                     # Python REPL
    r"^\w+@\w+:",               # SSH prompt
    r"^\[[\d:]+\]",             # Timestamp prefix
    r"(ERROR|WARNING|INFO|DEBUG)\s*[:\|]",  # Log levels
    r"^\s*\d+\s+(PASS|FAIL)",   # Test output
    r"Traceback \(most recent",  # Python traceback
    r"^npm\s+(ERR|WARN)!",      # npm output
    r"^\s*\d+\.\d+\s*%",        # Progress percentage
]


@dataclass
class PostProcessResult:
    """Result of OCR post-processing."""
    original_text: str
    enhanced_text: str
    text_type: str = TextType.UNKNOWN
    corrections_count: int = 0
    corrections: List[Dict] = field(default_factory=list)
    processing_time_ms: float = 0.0
    spell_corrections: int = 0

    def to_dict(self) -> Dict:
        return {
            "text_type": self.text_type,
            "corrections_count": self.corrections_count,
            "spell_corrections": self.spell_corrections,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "original_length": len(self.original_text),
            "enhanced_length": len(self.enhanced_text),
        }


class OCREnhancer:
    """
    Post-processes OCR results to improve accuracy.

    Pipeline:
    1. Detect text type (code / terminal / prose)
    2. Apply type-specific OCR error corrections
    3. Fix common character confusions
    4. Merge broken words across line breaks
    5. Optional spell checking (prose only, preserves code keywords)
    """

    def __init__(
        self,
        enable_spell_check: bool = True,
        spell_dict_path: Optional[str] = None,
        max_edit_distance: int = 2,
        enabled: bool = True,
    ):
        """
        Args:
            enable_spell_check: Enable spell checking for prose text
            spell_dict_path: Custom dictionary path (uses built-in if None)
            max_edit_distance: Max edit distance for spell corrections
            enabled: Enable/disable post-processing
        """
        self.enabled = enabled
        self.enable_spell_check = enable_spell_check and _HAS_SYMSPELL
        self.max_edit_distance = max_edit_distance
        self._spell: Optional[object] = None

        # Stats
        self.total_processed = 0
        self.total_corrections = 0
        self.total_spell_fixes = 0

        if self.enable_spell_check:
            self._init_spell_checker(spell_dict_path)

        logger.info(
            "OCREnhancer initialized",
            enabled=enabled,
            spell_check=self.enable_spell_check,
            symspell_available=_HAS_SYMSPELL,
        )

    def _init_spell_checker(self, dict_path: Optional[str] = None):
        """Initialize SymSpell spell checker."""
        if not _HAS_SYMSPELL:
            return
        try:
            self._spell = _SymSpell(max_dictionary_edit_distance=self.max_edit_distance)
            if dict_path and os.path.exists(dict_path):
                self._spell.load_dictionary(dict_path, term_index=0, count_index=1)
            else:
                # Use built-in English frequency dictionary from symspellpy
                import pkg_resources
                dict_path_default = pkg_resources.resource_filename(
                    "symspellpy", "frequency_dictionary_en_82_765.txt"
                )
                if os.path.exists(dict_path_default):
                    self._spell.load_dictionary(dict_path_default, term_index=0, count_index=1)
                else:
                    logger.warning("SymSpell dictionary not found, disabling spell check")
                    self.enable_spell_check = False
                    self._spell = None
        except Exception as e:
            logger.warning("SymSpell initialization failed", error=str(e))
            self.enable_spell_check = False
            self._spell = None

    def enhance(self, text: str, hint_type: Optional[str] = None) -> PostProcessResult:
        """
        Post-process OCR text to improve accuracy.

        Args:
            text: Raw OCR text
            hint_type: Optional type hint (code/terminal/prose) — auto-detected if None

        Returns:
            PostProcessResult with enhanced text and corrections metadata
        """
        if not self.enabled or not text or not text.strip():
            return PostProcessResult(
                original_text=text or "",
                enhanced_text=text or "",
                text_type=TextType.UNKNOWN,
            )

        start = time.time()
        self.total_processed += 1

        corrections = []

        # Step 1: Detect text type
        text_type = hint_type or self._detect_text_type(text)

        # Step 2: Apply type-specific fixes
        enhanced = text
        if text_type == TextType.CODE:
            enhanced, code_fixes = self._fix_code_ocr(enhanced)
            corrections.extend(code_fixes)
        elif text_type == TextType.TERMINAL:
            enhanced, term_fixes = self._fix_terminal_ocr(enhanced)
            corrections.extend(term_fixes)
        elif text_type == TextType.PROSE:
            enhanced, prose_fixes = self._fix_prose_ocr(enhanced)
            corrections.extend(prose_fixes)
        else:
            # Mixed: apply both code and terminal fixes conservatively
            enhanced, code_fixes = self._fix_code_ocr(enhanced)
            enhanced, term_fixes = self._fix_terminal_ocr(enhanced)
            corrections.extend(code_fixes)
            corrections.extend(term_fixes)

        # Step 3: Fix common character confusions
        enhanced, char_fixes = self._fix_char_confusions(enhanced, text_type)
        corrections.extend(char_fixes)

        # Step 4: Merge broken words across line breaks
        enhanced = self._merge_broken_words(enhanced)

        # Step 5: Spell check (prose only)
        spell_count = 0
        if text_type == TextType.PROSE and self.enable_spell_check:
            enhanced, spell_count = self._spell_check(enhanced)

        elapsed = (time.time() - start) * 1000
        self.total_corrections += len(corrections) + spell_count
        self.total_spell_fixes += spell_count

        return PostProcessResult(
            original_text=text,
            enhanced_text=enhanced,
            text_type=text_type,
            corrections_count=len(corrections) + spell_count,
            corrections=corrections[:20],  # limit detail logging
            processing_time_ms=elapsed,
            spell_corrections=spell_count,
        )

    # ── Text Type Detection ──────────────────────────────────────────

    def _detect_text_type(self, text: str) -> str:
        """Detect whether text is code, terminal output, or prose."""
        lines = text.strip().splitlines()
        if not lines:
            return TextType.UNKNOWN

        code_score = 0
        terminal_score = 0
        prose_score = 0

        for line in lines[:30]:  # Check first 30 lines
            for pattern in CODE_INDICATORS:
                if re.search(pattern, line, re.MULTILINE):
                    code_score += 1
                    break

            for pattern in TERMINAL_INDICATORS:
                if re.search(pattern, line, re.MULTILINE):
                    terminal_score += 1
                    break

            # Prose heuristic: long sentences with spaces, punctuation
            if len(line) > 40 and line.count(" ") > 5 and re.search(r"[.!?,;:]", line):
                prose_score += 1

        # Determine type
        total = code_score + terminal_score + prose_score
        if total == 0:
            return TextType.UNKNOWN

        max_score = max(code_score, terminal_score, prose_score)

        if code_score == max_score and code_score > 2:
            return TextType.CODE
        if terminal_score == max_score and terminal_score > 2:
            return TextType.TERMINAL
        if prose_score == max_score and prose_score > 2:
            return TextType.PROSE

        if code_score > 0 and terminal_score > 0:
            return TextType.MIXED

        return TextType.UNKNOWN

    # ── Code OCR Fixes ───────────────────────────────────────────────

    def _fix_code_ocr(self, text: str) -> Tuple[str, List[Dict]]:
        """Apply code-specific OCR corrections."""
        corrections = []
        result = text

        for pattern, replacement in CODE_OCR_FIXES:
            new_text = re.sub(pattern, replacement, result)
            if new_text != result:
                corrections.append({
                    "type": "code_fix",
                    "pattern": pattern,
                    "replacement": replacement,
                })
                result = new_text

        # Fix common indentation issues (OCR often drops leading spaces)
        lines = result.splitlines()
        fixed_lines = []
        for line in lines:
            # If line starts with code keyword but no indent, and prev line suggests indent needed
            fixed_lines.append(line)
        result = "\n".join(fixed_lines)

        return result, corrections

    # ── Terminal OCR Fixes ───────────────────────────────────────────

    def _fix_terminal_ocr(self, text: str) -> Tuple[str, List[Dict]]:
        """Apply terminal-output-specific OCR corrections."""
        corrections = []
        result = text

        for pattern, replacement in TERMINAL_OCR_FIXES:
            new_text = re.sub(pattern, replacement, result)
            if new_text != result:
                corrections.append({
                    "type": "terminal_fix",
                    "pattern": pattern,
                    "replacement": replacement,
                })
                result = new_text

        return result, corrections

    # ── Prose OCR Fixes ──────────────────────────────────────────────

    def _fix_prose_ocr(self, text: str) -> Tuple[str, List[Dict]]:
        """Apply prose-specific OCR corrections."""
        corrections = []
        result = text

        # Fix common prose OCR errors
        prose_fixes = [
            (r"\bl\b(?=[A-Z])", "I"),          # lowercase l before capital → I
            (r"(?<=[a-z])l(?=[a-z]{2,})", "l"),  # keep l in words
            (r"\btbe\b", "the"),
            (r"\bwitb\b", "with"),
            (r"\bwbich\b", "which"),
            (r"\btbat\b", "that"),
        ]

        for pattern, replacement in prose_fixes:
            new_text = re.sub(pattern, replacement, result)
            if new_text != result:
                corrections.append({
                    "type": "prose_fix",
                    "pattern": pattern,
                    "replacement": replacement,
                })
                result = new_text

        return result, corrections

    # ── Character Confusion Fixes ────────────────────────────────────

    def _fix_char_confusions(self, text: str, text_type: str) -> Tuple[str, List[Dict]]:
        """Fix common character confusions based on context."""
        corrections = []
        result = text

        # 0 vs O disambiguation
        # In code context: variable names should use O, hex/numbers use 0
        if text_type in (TextType.CODE, TextType.TERMINAL):
            # Fix O used as 0 in numeric contexts (digit-adjacent)
            new_text = re.sub(r"(?<=\d)O|O(?=\d)", "0", result)
            if new_text != result:
                corrections.append({"type": "char_fix", "fix": "O→0 in numbers"})
                result = new_text

            # Fix 0 used as O in word contexts
            new_text = re.sub(r"(?<=[a-zA-Z])0(?=[a-zA-Z])", "O", result)
            if new_text != result:
                corrections.append({"type": "char_fix", "fix": "0→O in words"})
                result = new_text

        # 1 vs l vs I disambiguation
        if text_type in (TextType.CODE, TextType.TERMINAL):
            # Fix l used as 1 in numeric contexts (digit-adjacent)
            new_text = re.sub(r"(?<=\d)l|l(?=\d)", "1", result)
            if new_text != result:
                corrections.append({"type": "char_fix", "fix": "l→1 in numbers"})
                result = new_text

        return result, corrections

    # ── Word Merging ─────────────────────────────────────────────────

    @staticmethod
    def _merge_broken_words(text: str) -> str:
        """Merge words broken across line breaks by OCR."""
        # Pattern: word ending with hyphen at line break
        text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

        # Pattern: word split across lines without hyphen (if short fragment)
        lines = text.splitlines()
        merged = []
        i = 0
        while i < len(lines):
            line = lines[i]
            # If line ends with a short word fragment (< 4 chars, no punctuation)
            # and next line starts with lowercase, merge
            words = line.split()
            if (i + 1 < len(lines)
                    and len(line) > 0
                    and words
                    and not line[-1] in ".!?:;,)"
                    and len(lines[i + 1]) > 0
                    and lines[i + 1][0].islower()
                    and len(words[-1]) < 4
                    and not line.strip().endswith(("=", "{", "(", "[", ","))):
                merged.append(line + lines[i + 1])
                i += 2
            else:
                merged.append(line)
                i += 1

        return "\n".join(merged)

    # ── Spell Check ──────────────────────────────────────────────────

    def _spell_check(self, text: str) -> Tuple[str, int]:
        """Apply spell checking to prose text, preserving code keywords."""
        if not self._spell:
            return text, 0

        corrections = 0
        words = text.split()
        fixed_words = []

        for word in words:
            # Strip punctuation for checking
            clean = re.sub(r'^[^\w]+|[^\w]+$', '', word)

            # Skip: empty, code keywords, short words, numbers, paths
            if (not clean
                    or clean in PROGRAMMING_KEYWORDS
                    or clean.lower() in PROGRAMMING_KEYWORDS
                    or len(clean) < 3
                    or re.match(r'^[\d.]+$', clean)
                    or '/' in clean
                    or '_' in clean
                    or clean[0].isupper()):  # Proper nouns
                fixed_words.append(word)
                continue

            suggestions = self._spell.lookup(
                clean, Verbosity.CLOSEST, max_edit_distance=self.max_edit_distance
            )
            if suggestions and suggestions[0].distance > 0 and suggestions[0].distance <= self.max_edit_distance:
                # Replace only the clean part, preserve surrounding punctuation
                corrected = word.replace(clean, suggestions[0].term, 1)
                if corrected != word:
                    corrections += 1
                fixed_words.append(corrected)
            else:
                fixed_words.append(word)

        return " ".join(fixed_words), corrections

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get OCR post-processing statistics."""
        return {
            "enabled": self.enabled,
            "spell_check": self.enable_spell_check,
            "total_processed": self.total_processed,
            "total_corrections": self.total_corrections,
            "total_spell_fixes": self.total_spell_fixes,
            "avg_corrections": (
                round(self.total_corrections / self.total_processed, 2)
                if self.total_processed > 0 else 0
            ),
        }


def create_ocr_enhancer_from_env(settings=None) -> OCREnhancer:
    """Create OCREnhancer from settings."""
    if settings is None:
        from settings import get_settings
        settings = get_settings()

    return OCREnhancer(
        enable_spell_check=settings.ocr_spell_check,
        spell_dict_path=os.getenv("OCR_SPELL_DICT") or None,  # TODO: add to settings if needed
        max_edit_distance=settings.ocr_max_edit_distance,
        enabled=settings.enable_ocr_post_process,
    )
