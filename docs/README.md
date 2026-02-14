# Dokumentacja Proxeen

Ten katalog porzadkuje dokumentacje techniczna i operacyjna projektu.
Najpierw przeczytaj ten plik, potem przejdz do dokumentu dla swojej roli.

## Szybki start do dokumentacji

1. Architektura i przeplyw: [system-overview.md](system-overview.md)
2. Diagnostyka i konfiguracja: [diagnostics.md](diagnostics.md)
3. Szybkie uruchomienie: [../QUICKSTART.md](../QUICKSTART.md)

## Mapa dokumentacji

- [system-overview.md](system-overview.md)
  - Jak dziala system end-to-end (overlay -> backend -> pipeline -> event bus)
  - Przeplyw danych i odpowiedzialnosci komponentow
  - Okna aplikacji, klasyfikacja i wycinki ROI
- [diagnostics.md](diagnostics.md)
  - Jak uruchomic `make diag`
  - Co sprawdza `test_setup.py` i `preflight.py`
  - Jak interpretowac bledy i co poprawic w konfiguracji

## Dokumenty w glownym katalogu (zachowane dla kompatybilnosci)

- [../README.md](../README.md) - overview produktu i instrukcje uruchomienia
- [../ARCHITECTURE.md](../ARCHITECTURE.md) - szczegolowa architektura komponentow
- [../INSTALL.md](../INSTALL.md) - instalacja zaleznosci
- [../PROVIDERS.md](../PROVIDERS.md) - modele i providerzy AI
- [../CHANGELOG.md](../CHANGELOG.md) - historia zmian
- [../TODO.md](../TODO.md) - otwarty backlog techniczny

## Kiedy uzywac ktorego pliku

- Potrzebujesz zrozumiec jak system dziala? -> `system-overview.md`
- Potrzebujesz debugowac startup lub API keys? -> `diagnostics.md`
- Potrzebujesz uruchomic projekt od zera? -> `QUICKSTART.md` / `INSTALL.md`
