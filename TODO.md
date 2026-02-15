# TODO — backlog techniczny (stan: 2026-02-15)

Backlog podzielony na: rzeczy domkniete i najblizsze priorytety.

## Zrobione ostatnio

- [x] Dodano `make diag` jako jednolita diagnostyke systemu (`test_setup.py` + `preflight.py`).
- [x] Dodano uruchamianie CLI `backend/preflight.py` z poprawnym kodem wyjscia.
- [x] Usprawniono detekcje okien: preferowany backend `python-xlib + ewmh`, fallback na CLI.
- [x] Naprawiono `sqlite3 database is locked` w EventStore (retry + backoff + wiekszy `busy_timeout`).
- [x] Wdrozono `OptimizationStrategy` (priorytety `auto|budget|speed|quality`) i integracje z `ProfileSelector`, `AnalyzeStep`, `PipelineOrchestrator`.
- [x] Uporzadkowano dokumentacje: `docs/README.md`, `docs/system-overview.md`, `docs/diagnostics.md` + linki w README.
- [x] Dodano rozszerzone komendy testowe w Makefile: `test-vlm-ocr`, `test-strategy`, `test-all`.
- [x] Rozszerzono regresje w `backend/tests/test_units.py` (ShellAgent blokady pipe, ProfileSelector VLM OCR, BuildContext semantic recall, AutoDiagnostics VLM OCR).
- [x] Zrefaktoryzowano `ProcessScanner.scan_all_windows` na mniejsze etapy (`_resolve_active_wid`, `_scan_via_backend`, `_filter_service_windows`, `_enrich_with_process_info`).
- [x] Dodano event `pipeline.ocr_cost` i sampling `@nfo.log_call(..., sample_rate=...)` dla sledzenia kosztow i redukcji szumu logow.

## P0 — diagnostyka i stabilnosc

- [ ] Dodac test integracyjny dla `make diag` (scenariusze: brak `.env`, brak API key, brak narzedzi systemowych).
- [ ] Rozdzielic wynik diagnostyki na poziomy `ERROR` vs `WARN` (z fail tylko dla bledow krytycznych).
- [ ] Ograniczyc rozrost logow `nfo_proxeen.*` (rotacja/retencja i limity rozmiaru).

## P1 — window awareness i capture

- [ ] Dodac bardziej precyzyjny fallback dla Wayland (gdy X11 backend jest niedostepny).
- [ ] Dodac testy regresyjne dla wyboru aktywnego okna (kursor vs focus vs service window).
- [ ] Doprecyzowac diagnostyke `DISPLAY`/sesji graficznej z jasna instrukcja naprawy.

## P2 — CI i jakosc

- [ ] Dodac szybki smoke job CI: `make diag` + `pytest backend/tests/test_units.py`.
- [ ] Dodac artefakt diagnostyczny (np. JSON) dla pipeline CI/CD.
- [ ] Uporzadkowac historyczny format `CHANGELOG.md` (naglowek na gorze, sekcja `Unreleased` na poczatku, redukcja warningow markdownlint).

## P3 — dokumentacja operacyjna

- [ ] Dopisac runbook "incydent produkcyjny" (checklista: health, events, diagnostics, rollback).
- [ ] Dopisac sekcje "najczestsze bledy konfiguracji" z mapowaniem blad -> naprawa.
