"""
Per-application analysis profiles for AI Desktop Assistant.

Each profile defines:
- Custom system prompt tailored to the app category
- ROI detection hints (what to focus on in screenshots)
- Suggested actions for common patterns
- Priority keywords for change detection
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import structlog

from window_aware import AppCategory

logger = structlog.get_logger()


@dataclass
class AppProfile:
    """Analysis profile for a specific application category."""
    category: AppCategory
    name: str
    emoji: str
    system_prompt_addon: str
    focus_keywords: List[str] = field(default_factory=list)
    action_patterns: Dict[str, str] = field(default_factory=dict)
    priority_boost: float = 1.0  # multiplier for change detection sensitivity


# ===== Per-App System Prompt Addons =====
# These are appended to the base SYSTEM_PROMPT in analyzer.py

PROFILES: Dict[AppCategory, AppProfile] = {
    AppCategory.IDE: AppProfile(
        category=AppCategory.IDE,
        name="IDE / Edytor kodu",
        emoji="💻",
        system_prompt_addon="""
Kontekst: Użytkownik pracuje w IDE/edytorze kodu.
Dodatkowe instrukcje:
- Wykrywaj błędy składniowe, ostrzeżenia, czerwone podkreślenia
- Sugeruj refactoring jeśli widzisz powtórzony kod
- Wykrywaj TODO/FIXME/HACK komentarze
- Jeśli widoczny jest terminal z błędem, zasugeruj fix
- Zwracaj uwagę na import statements i brakujące zależności
- Wykrywaj anti-patterns (np. nested callbacks, god classes)
- Jeśli widoczny jest git diff, podsumuj zmiany
Priorytet: błędy > ostrzeżenia > sugestie > informacje""",
        focus_keywords=[
            "error", "warning", "exception", "traceback", "failed",
            "def ", "class ", "import ", "TODO", "FIXME", "HACK",
            "syntax", "undefined", "TypeError", "ValueError",
        ],
        action_patterns={
            r"TypeError|AttributeError|NameError": "Wykryto błąd Python — sprawdź typy i nazwy zmiennych",
            r"ModuleNotFoundError|ImportError": "Brakujący moduł — uruchom: pip install {module}",
            r"git push.*rejected|failed to push": "Push odrzucony — spróbuj: git pull --rebase",
            r"merge conflict|CONFLICT": "Konflikt merge — rozwiąż konflikty w zaznaczonych plikach",
            r"eslint|prettier|flake8|pylint": "Linter wykrył problemy — napraw przed commitem",
        },
        priority_boost=1.5,
    ),

    AppCategory.TERMINAL: AppProfile(
        category=AppCategory.TERMINAL,
        name="Terminal",
        emoji="🖥️",
        system_prompt_addon="""
Kontekst: Użytkownik pracuje w terminalu.
Dodatkowe instrukcje:
- Wyjaśnij ostatnio uruchomione komendy i ich wynik
- Jeśli widoczny jest błąd, zasugeruj poprawną komendę
- Wykrywaj długo trwające procesy (kompilacja, testy)
- Sugeruj aliasy dla powtarzanych komend
- Rozpoznawaj Docker, systemd, make, cargo, npm, pip komendy
- Jeśli widoczny jest man page lub --help, podsumuj kluczowe opcje
Priorytet: błędy > exit codes > wyniki komend > sugestie""",
        focus_keywords=[
            "error", "failed", "permission denied", "not found",
            "command not found", "exit code", "segfault", "killed",
            "$", "#", ">>>", "make", "docker", "git", "npm", "pip",
        ],
        action_patterns={
            r"command not found": "Komenda nie znaleziona — zainstaluj pakiet lub sprawdź PATH",
            r"permission denied": "Brak uprawnień — użyj sudo lub zmień uprawnienia (chmod)",
            r"No space left on device": "Brak miejsca — sprawdź: df -h && du -sh /tmp/*",
            r"Connection refused|timeout": "Problem z połączeniem — sprawdź usługę i port",
            r"Segmentation fault": "Segfault — uruchom z gdb lub valgrind",
        },
        priority_boost=1.3,
    ),

    AppCategory.BROWSER: AppProfile(
        category=AppCategory.BROWSER,
        name="Przeglądarka",
        emoji="🌐",
        system_prompt_addon="""
Kontekst: Użytkownik przegląda internet.
Dodatkowe instrukcje:
- Podsumuj treść widocznej strony (max 2-3 zdania)
- Wykrywaj URL i kontekst strony (dokumentacja, forum, artykuł, social media)
- Jeśli to dokumentacja API/techniczna, wyciągnij kluczowe informacje
- Jeśli to StackOverflow/forum, podsumuj pytanie i najlepszą odpowiedź
- Wykrywaj formularze, alerty, popupy wymagające uwagi
- Jeśli to GitHub/GitLab, podsumuj PR/issue/code review
Priorytet: alerty > formularze > treść strony""",
        focus_keywords=[
            "http", "www", "search", "login", "password", "submit",
            "error", "404", "500", "stackoverflow", "github",
            "documentation", "API", "tutorial",
        ],
        action_patterns={
            r"stackoverflow\.com|stackexchange": "StackOverflow — podsumowanie Q&A",
            r"github\.com.*pull": "GitHub PR — podsumowanie zmian",
            r"github\.com.*issues": "GitHub Issue — status i kontekst",
            r"docs\.|documentation|readme": "Dokumentacja — wyciągam kluczowe sekcje",
        },
        priority_boost=0.8,
    ),

    AppCategory.EMAIL: AppProfile(
        category=AppCategory.EMAIL,
        name="Email",
        emoji="📧",
        system_prompt_addon="""
Kontekst: Użytkownik czyta/pisze email.
Dodatkowe instrukcje:
- NIE czytaj treści prywatnych maili (RODO/privacy)
- Wykrywaj liczbę nieprzeczytanych wiadomości
- Sugeruj priorytetyzację (ważne vs spam vs newsletter)
- Jeśli użytkownik pisze maila, sugeruj ulepszenia tonu/struktury
- Wykrywaj załączniki i linki
Priorytet: nieprzeczytane > draft > skrzynka odbiorcza""",
        focus_keywords=[
            "inbox", "unread", "draft", "sent", "reply", "forward",
            "attachment", "urgent", "important",
        ],
        action_patterns={
            r"\d+ unread|\d+ nieprzeczytanych": "Nieprzeczytane wiadomości — priorytetyzacja",
            r"draft|szkic": "Szkic emaila — sugestie ulepszenia",
        },
        priority_boost=0.7,
    ),

    AppCategory.CHAT: AppProfile(
        category=AppCategory.CHAT,
        name="Komunikator",
        emoji="💬",
        system_prompt_addon="""
Kontekst: Użytkownik korzysta z komunikatora.
Dodatkowe instrukcje:
- NIE czytaj treści prywatnych wiadomości (privacy)
- Wykrywaj aktywność: spotkanie, czat grupowy, DM
- Jeśli widoczny jest screen share lub prezentacja, podsumuj treść
- Wykrywaj linki i udostępnione pliki
Priorytet: spotkanie > ważne wiadomości > ogólna aktywność""",
        focus_keywords=[
            "meeting", "call", "screen share", "channel", "thread",
        ],
        action_patterns={},
        priority_boost=0.5,
    ),

    AppCategory.OFFICE: AppProfile(
        category=AppCategory.OFFICE,
        name="Biurowy",
        emoji="📄",
        system_prompt_addon="""
Kontekst: Użytkownik pracuje z dokumentem biurowym.
Dodatkowe instrukcje:
- Wykrywaj typ dokumentu (tekst, arkusz, prezentacja, PDF)
- Sugeruj formatowanie i strukturę
- Jeśli arkusz kalkulacyjny, wykrywaj formuły i dane
- Jeśli prezentacja, podsumuj slajd
Priorytet: błędy formatowania > treść > sugestie""",
        focus_keywords=[
            "page", "slide", "sheet", "cell", "formula", "table",
        ],
        action_patterns={},
        priority_boost=0.6,
    ),

    AppCategory.MEDIA: AppProfile(
        category=AppCategory.MEDIA,
        name="Media",
        emoji="🎨",
        system_prompt_addon="""
Kontekst: Użytkownik korzysta z aplikacji multimedialnej.
Dodatkowe instrukcje:
- Wykrywaj typ: odtwarzacz, edytor graficzny, edytor wideo, DAW
- Dla edytorów: sugeruj narzędzia i skróty
- Dla odtwarzaczy: podsumuj co jest odtwarzane
Priorytet: informacyjny""",
        focus_keywords=[],
        action_patterns={},
        priority_boost=0.3,
    ),

    AppCategory.GRAPHICS: AppProfile(
        category=AppCategory.GRAPHICS,
        name="Grafika / Design",
        emoji="🖌️",
        system_prompt_addon="""
Kontekst: Użytkownik pracuje w programie graficznym lub CAD.
Dodatkowe instrukcje:
- Wykrywaj narzędzia, warstwy, palety kolorów
- Sugeruj skróty klawiszowe dla aktywnego narzędzia
- Jeśli to CAD/3D, zwracaj uwagę na wymiary i ograniczenia
- Wykrywaj błędy renderowania lub eksportu
Priorytet: narzędzia > parametry > podgląd""",
        focus_keywords=["layer", "tool", "brush", "color", "export", "render"],
        action_patterns={},
        priority_boost=0.6,
    ),

    AppCategory.GAME: AppProfile(
        category=AppCategory.GAME,
        name="Gra",
        emoji="🎮",
        system_prompt_addon="""
Kontekst: Użytkownik gra w grę.
Dodatkowe instrukcje:
- Podsumuj stan gry (zdrowie, amunicja, cel misji)
- Jeśli widoczne menu/ekwipunek, opisz dostępne opcje
- Wykrywaj błędy gry, crashe lub lagi
- Nie przeszkadzaj w trakcie aktywnej rozgrywki (chyba że użytkownik pyta)
Priorytet: stan gry > cele > błędy""",
        focus_keywords=["health", "ammo", "score", "level", "mission", "inventory"],
        action_patterns={},
        priority_boost=0.4,
    ),

    AppCategory.FILE_MANAGER: AppProfile(
        category=AppCategory.FILE_MANAGER,
        name="Menedżer plików",
        emoji="📂",
        system_prompt_addon="""
Kontekst: Użytkownik zarządza plikami.
Dodatkowe instrukcje:
- Podsumuj bieżący katalog i zaznaczone pliki
- Wykrywaj operacje (kopiowanie, przenoszenie, usuwanie)
- Ostrzegaj przed usuwaniem dużej liczby plików
- Wykrywaj zajętość dysku jeśli widoczna
Priorytet: operacje > zaznaczenie > struktura""",
        focus_keywords=["folder", "file", "size", "date", "permissions", "free space"],
        action_patterns={},
        priority_boost=0.5,
    ),

    AppCategory.SYSTEM: AppProfile(
        category=AppCategory.SYSTEM,
        name="System / Ustawienia",
        emoji="⚙️",
        system_prompt_addon="""
Kontekst: Użytkownik konfiguruje system lub monitoruje zasoby.
Dodatkowe instrukcje:
- Wykrywaj wysokie zużycie CPU/RAM (jeśli widoczny monitor zasobów)
- Podsumuj zmieniane ustawienia
- Ostrzegaj przed zmianami zagrażającymi stabilności
- Jeśli widoczny menedżer pakietów, podsumuj instalację/aktualizację
Priorytet: zasoby > zmiany ustawień > informacje""",
        focus_keywords=["cpu", "memory", "ram", "disk", "network", "update", "setting", "config"],
        action_patterns={
            r"high cpu|100%": "Wysokie zużycie CPU — sprawdź proces",
            r"low disk space|brak miejsca": "Brak miejsca na dysku — sugerowane czyszczenie",
        },
        priority_boost=0.9,
    ),

    AppCategory.UTILITY: AppProfile(
        category=AppCategory.UTILITY,
        name="Narzędzia",
        emoji="🛠️",
        system_prompt_addon="""
Kontekst: Użytkownik korzysta z narzędzia pomocniczego.
Dodatkowe instrukcje:
- Zidentyfikuj cel narzędzia (kalkulator, hasła, screenshot)
- Sprawdź poprawność wprowadzanych danych
- Jeśli kalkulator: zweryfikuj wynik jeśli możliwe
- Jeśli menedżer haseł: NIE czytaj/loguj haseł (privacy)
Priorytet: poprawność danych > cel""",
        focus_keywords=["password", "result", "calculation", "copy", "save"],
        action_patterns={},
        priority_boost=0.5,
    ),
}

# Default profile for unclassified apps
DEFAULT_PROFILE = AppProfile(
    category=AppCategory.UNKNOWN,
    name="Ogólny",
    emoji="📝",
    system_prompt_addon="",
    focus_keywords=[],
    action_patterns={},
    priority_boost=1.0,
)


class ProfileManager:
    """
    Manages per-app analysis profiles and prompt generation.
    """

    def __init__(self):
        self.profiles = dict(PROFILES)
        self.default = DEFAULT_PROFILE
        self.active_category: Optional[AppCategory] = None
        self.switch_count = 0

        logger.info(
            "ProfileManager initialized",
            profiles=list(self.profiles.keys()),
        )

    def get_profile(self, category: AppCategory) -> AppProfile:
        """Get profile for a given app category."""
        return self.profiles.get(category, self.default)

    def get_prompt_addon(self, category: AppCategory) -> str:
        """Get the system prompt addon for an app category."""
        profile = self.get_profile(category)
        if self.active_category != category:
            self.active_category = category
            self.switch_count += 1
            logger.info(
                "App profile switched",
                category=category.value,
                profile=profile.name,
            )
        return profile.system_prompt_addon

    def get_focus_keywords(self, category: AppCategory) -> List[str]:
        """Get keywords to focus on for ROI detection."""
        profile = self.get_profile(category)
        return profile.focus_keywords

    def get_priority_boost(self, category: AppCategory) -> float:
        """Get change detection sensitivity multiplier."""
        profile = self.get_profile(category)
        return profile.priority_boost

    def match_action_patterns(self, category: AppCategory, text: str) -> List[Dict]:
        """
        Match detected text against action patterns for the current app.
        Returns list of suggested actions.
        """
        import re

        profile = self.get_profile(category)
        actions = []

        for pattern, action_desc in profile.action_patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                actions.append({
                    "pattern": pattern,
                    "action": action_desc,
                    "category": category.value,
                    "profile": profile.name,
                })

        return actions

    def get_all_profiles(self) -> List[Dict]:
        """Get all available profiles as dicts."""
        result = []
        for cat, profile in self.profiles.items():
            result.append({
                "category": cat.value,
                "name": profile.name,
                "emoji": profile.emoji,
                "keywords_count": len(profile.focus_keywords),
                "actions_count": len(profile.action_patterns),
                "priority_boost": profile.priority_boost,
            })
        return result

    def get_stats(self) -> Dict:
        """Get ProfileManager statistics."""
        return {
            "active_category": self.active_category.value if self.active_category else None,
            "switch_count": self.switch_count,
            "total_profiles": len(self.profiles),
        }


def create_profile_manager(settings=None) -> ProfileManager:
    """Create ProfileManager instance."""
    return ProfileManager()
