"""
TranslationSkill — Detect non-native language text, offer translation.

Detection:
- Uses character frequency + common word heuristics (no external API for detect)
- Supports: English, German, French, Spanish, Russian, Ukrainian, Chinese, Japanese
- If text is in user's native language (locale), this skill does NOT trigger

Options:
- Translate to native language
- Translate to English
- Copy original
"""
import re
from typing import List

from skills.base import (
    BaseSkill, SkillCategory, SkillContext, SkillOption, SkillResult, OptionRisk,
)

# Language detection heuristics: (lang_code, common_words, name_pl, name_en)
_LANG_HINTS = [
    ("en", {"the", "is", "are", "was", "were", "have", "has", "been", "will", "would",
            "could", "should", "with", "from", "this", "that", "which", "about", "into",
            "your", "they", "their", "what", "when", "where", "there", "here", "some"},
     "angielski", "English"),
    ("de", {"der", "die", "das", "und", "ist", "ein", "eine", "nicht", "auf", "mit",
            "sich", "des", "den", "für", "von", "werden", "wird", "sind", "haben", "nach"},
     "niemiecki", "German"),
    ("fr", {"les", "des", "une", "est", "dans", "pour", "que", "pas", "sur", "avec",
            "sont", "ont", "mais", "plus", "tout", "cette", "elle", "nous", "vous", "leur"},
     "francuski", "French"),
    ("es", {"los", "las", "una", "del", "que", "por", "con", "para", "como", "más",
            "pero", "sus", "esto", "esta", "todo", "desde", "hace", "hasta", "puede", "muy"},
     "hiszpański", "Spanish"),
    ("ru", {"что", "это", "как", "все", "они", "быть", "для", "его", "уже", "так",
            "или", "при", "если", "был", "она", "мне", "нет", "мой", "вот", "ещё"},
     "rosyjski", "Russian"),
    ("uk", {"що", "це", "як", "все", "вони", "для", "його", "вже", "так", "або",
            "при", "якщо", "був", "вона", "мені", "ні", "мій", "ось", "ще", "але"},
     "ukraiński", "Ukrainian"),
    ("pl", {"jest", "nie", "się", "jak", "ale", "czy", "już", "tak", "lub", "dla",
            "tym", "jego", "też", "tylko", "przez", "może", "bardzo", "jeszcze", "gdzie", "kiedy"},
     "polski", "Polish"),
]

# CJK character ranges
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")
_CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04ff]")

# Minimum text length to attempt detection
_MIN_DETECT_LENGTH = 20
_MIN_WORD_COUNT = 4


def detect_language(text: str) -> str:
    """
    Simple language detection using word frequency heuristics.
    Returns ISO 639-1 code or "unknown".
    """
    text_lower = text.lower()
    words = set(re.findall(r"\b\w+\b", text_lower))

    if len(words) < _MIN_WORD_COUNT:
        return "unknown"

    # CJK detection
    cjk_chars = len(_CJK_PATTERN.findall(text))
    if cjk_chars > len(text) * 0.1:
        # Rough: Chinese vs Japanese (katakana/hiragana presence)
        if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
            return "ja"
        return "zh"

    # Score each language by common word overlap
    best_lang = "unknown"
    best_score = 0

    for lang_code, common_words, _, _ in _LANG_HINTS:
        overlap = len(words & common_words)
        # Normalize by number of common words checked
        score = overlap / max(len(words), 1)
        if score > best_score and overlap >= 2:
            best_score = score
            best_lang = lang_code

    return best_lang if best_score > 0.05 else "unknown"


def _lang_name(code: str, pl: bool = True) -> str:
    """Get human-readable language name."""
    for lang_code, _, name_pl, name_en in _LANG_HINTS:
        if lang_code == code:
            return name_pl if pl else name_en
    names = {"zh": ("chiński", "Chinese"), "ja": ("japoński", "Japanese")}
    if code in names:
        return names[code][0 if pl else 1]
    return code


class TranslationSkill(BaseSkill):
    name = "translation"
    category = SkillCategory.LANGUAGE
    icon = "🌐"
    priority = 60

    def detect(self, text: str, ctx: SkillContext) -> float:
        if len(text.strip()) < _MIN_DETECT_LENGTH:
            return 0.0

        lang = detect_language(text)
        if lang == "unknown":
            return 0.0

        # Don't trigger for native language
        if lang == ctx.locale:
            return 0.0

        # Higher confidence for well-known languages
        return 0.75

    def get_options(self, text: str, ctx: SkillContext) -> List[SkillOption]:
        lang = detect_language(text)
        lang_name = _lang_name(lang)
        native_name = _lang_name(ctx.locale)

        options = [
            SkillOption(
                id=f"translate_{ctx.locale}",
                label=f"🌐 Przetłumacz na {native_name}",
                icon="🌐",
                description=f"Wykryto język: {lang_name}",
                data={"source_lang": lang, "target_lang": ctx.locale, "extracted": text[:100]},
            ),
        ]

        # If native is not English, also offer English translation
        if ctx.locale != "en" and lang != "en":
            options.append(SkillOption(
                id="translate_en",
                label="🇬🇧 Translate to English",
                icon="🇬🇧",
                data={"source_lang": lang, "target_lang": "en", "extracted": text[:100]},
            ))

        options.append(SkillOption(
            id="copy",
            label="📋 Kopiuj oryginał",
            icon="📋",
            data={"extracted": text[:100]},
        ))

        return options

    async def execute(self, text: str, option_id: str, ctx: SkillContext) -> SkillResult:
        if option_id == "copy":
            return SkillResult(success=True, message="📋 Skopiowano", clipboard_text=text)

        if option_id.startswith("translate_"):
            target = option_id.replace("translate_", "")
            source = detect_language(text)

            # Try using system translate tools
            translated = await self._translate(text, source, target)
            if translated:
                return SkillResult(
                    success=True,
                    message=f"🌐 Przetłumaczono ({_lang_name(source)} → {_lang_name(target)})",
                    output=translated,
                    clipboard_text=translated,
                )
            else:
                return SkillResult(
                    success=False,
                    message="❌ Tłumaczenie niedostępne — brak narzędzia",
                    error="No translation backend available. Install: pip install deep-translator",
                )

        return SkillResult(success=False, error=f"Unknown option: {option_id}")

    async def _translate(self, text: str, source: str, target: str) -> str:
        """Try translation via available backends."""
        # Try deep-translator (pip install deep-translator)
        try:
            from deep_translator import GoogleTranslator
            result = GoogleTranslator(source=source, target=target).translate(text[:5000])
            return result
        except ImportError:
            pass
        except Exception:
            pass

        # Try argos-translate (offline)
        try:
            import argostranslate.translate
            result = argostranslate.translate.translate(text[:5000], source, target)
            if result and result != text:
                return result
        except ImportError:
            pass
        except Exception:
            pass

        return ""

    def _label(self, text: str, ctx: SkillContext) -> str:
        lang = detect_language(text)
        return f"Tekst {_lang_name(lang)} ({len(text.split())} słów)"
