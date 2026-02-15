# Jak dziala system Proxeen

Dokument opisuje przeplyw od sygnalu z desktopu do odpowiedzi w overlay.

## 1. Warstwy systemu

## 1.1 Overlay (Electron)

- Odpowiada za UI always-on-top i click-through.
- Odbiera eventy przez SSE i pokazuje wynik analizy.
- Kluczowe pliki: `overlay/app.js`, `overlay/services/sse.js`.

## 1.2 Backend (FastAPI)

- Udostepnia API, SSE i endpointy diagnostyczne.
- Uruchamia glowna petle analizy i orchestruje pipeline.
- Kluczowe pliki: `backend/server.py`, `backend/analysis_loop.py`, `backend/pipeline/orchestrator.py`.

## 1.3 Runtime intelligence

- Window awareness: wykrywa aktywne okno, klase aplikacji i geometrie.
- Process scanner: buduje liste okien i klasyfikacje aplikacji.
- Cropper: wycina ROI per aplikacja/okno przed analiza AI.

## 2. Przeplyw runtime (tick pipeline)

Kazdy tick petli wykonuje logiczny ciag krokow:

1. Skan okien i aktywnego kontekstu
2. Capture ekranu (X11/Wayland)
3. Crop okien (per-app ROI)
4. Budowa kontekstu (okno + historia + clipboard)
5. Decyzja OptimizationStrategy (mode/OCR/model tier)
6. Analiza (vision/ocr/hybrid)
7. Post-processing OCR i sugestie akcji
8. Broadcast do SSE + zapis eventow

W kodzie pipeline jest rozszerzony i modulowy (14 krokow), ale powyzsza lista
oddaje glowna logike biznesowa i kolejnosc danych.

## 2.1 Warstwa decyzyjna: OptimizationStrategy

Przed krokiem analizy pipeline moze podjac centralna decyzje optymalizacyjna,
ktora ustawia:

- `analysis_mode` (`hybrid`, `ocr_only`, `ocr_plus_vision`, `skip`),
- preferowany OCR engine,
- tier modelu vision (`primary`/`fallback`/`emergency`),
- uzasadnienie decyzji + estymacje kosztu i latencji.

Tryby strategii:

- `budget` — minimalizacja kosztow,
- `speed` — minimalizacja opoznien,
- `quality` — maksymalizacja jakosci,
- `auto` — adaptacja wg budzetu i rolling metryk runtime.

Implementacja: `backend/optimization_strategy.py`.

## 3. Detekcja okien i kontekst aplikacji

System preferuje szybki backend X11 przez `python-xlib` + `ewmh`, a gdy to
niemozliwe, przechodzi na narzedzia CLI (`xdotool`, `xprop`, `wmctrl`).

Co daje ta architektura:

- mniejsze opoznienia detekcji aktywnego okna,
- stabilniejsze pobieranie geometrii i WM_CLASS,
- fallback dzialajacy bez zmian po stronie pipeline.

Glowna odpowiedzialnosc komponentow:

- `backend/window_aware.py` - aktywne okno, atrybuty, klasy aplikacji
- `backend/process_scanner.py` - skan wszystkich okien i filtrowanie service windows
- `backend/window_cropper.py` - wycinanie ROI dla konkretnej aplikacji

## 4. Event sourcing i observability

Runtime publikuje zdarzenia do EventBus, a EventStore (SQLite) utrwala je do
replay, statystyk i audytu.

Najwazniejsze elementy:

- EventBus (publish/subscribe, telemetry)
- EventStore SQLite (append, read, stats)
- ReadModel (materialized views do szybkich zapytan)

Dzieki temu mozna:

- odtworzyc przebieg sesji,
- mierzyc latencje i wydajnosc pipeline,
- budowac API diagnostyczne i dashboardy.

## 5. Konfiguracja i providerzy AI

Konfiguracja runtime jest oparta o `backend/.env` i typed settings.

Najwazniejsze obszary konfiguracji:

- model vision (`VISION_MODEL`)
- OCR engine (`OCR_ENGINE`)
- STT (`ENABLE_STT`, klucze Deepgram)
- progi i profile pipeline (`FAST`, `NORMAL`, `FULL`)
- strategia optymalizacji (`OPTIMIZATION_PRIORITY`, `HARDWARE_PROFILE`, limity budzetu i latency)

Lista providerow i modele: [../PROVIDERS.md](../PROVIDERS.md).

## 6. Diagnostyka i operacje

Do szybkiej oceny stanu systemu uzywaj:

- `make diag` - setup + preflight w jednym przebiegu
- `GET /health`, `GET /stats`, `GET /diagnostics` - endpointy runtime

Szczegoly: [diagnostics.md](diagnostics.md).
