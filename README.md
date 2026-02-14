# AI Desktop Assistant — Real-time Screen + Voice AI (v2.0.9)

Zaawansowany asystent AI z analizą ekranu w czasie rzeczywistym, rozpoznawaniem mowy i świadomością kontekstu okien.

## 🚀 Główne funkcje

- ✅ **8-etapowy pipeline analizy** — modularny, SOLID-compliant, event-driven
- ✅ **Window Awareness** — rozpoznawanie aktywnego okna, kategorii aplikacji, kontekstu git
- ✅ **Per-app crops** — oddzielne wycinki dla każdego okna na pulpicie
- ✅ **Vision AI** — 100+ providerów via LiteLLM (Gemini, GPT-4o, Claude, Ollama...)
- ✅ **3 silniki OCR** — PaddleOCR, EasyOCR, Tesseract z hot-swappingiem i benchmarkiem A/B
- ✅ **4 tryby analizy** — vision_only, ocr_only, hybrid (5-10x taniej), ocr+vision
- ✅ **Polish STT** — Deepgram Nova-3 streaming z wyborem urządzeń audio
- ✅ **Shell Agent** — sugeruje i wykonuje bezpieczne komendy (git, pip, npm...)
- ✅ **Event Sourcing + CQRS** — pełny audit trail w SQLite, materialized views
- ✅ **Konfiguracja przez przeglądarkę** — `/config/ui` z wykrywaniem urządzeń audio
- ✅ **Transparent overlay** — Electron, zawsze na wierzchu, click-through
- ✅ **Inteligentna detekcja zmian** — perceptual hashing (70-90% redukcja kosztów)
- ✅ **Wayland + X11** — auto-detekcja (PipeWire ScreenCast / mss / grim)

## 📊 Szacunkowe koszty API

| Komponent | Koszt/miesiąc (8h/dzień) |
|-----------|--------------------------|
| **Deepgram Nova-3 STT** | ~$81 |
| **Gemini 2.0 Flash (hybrid)** | ~$5-15 |
| **Gemini 2.0 Flash (vision)** | ~$30-60 |
| **RAZEM (hybrid)** | **~$86-96** |

> Tryb `hybrid` (OCR + LLM tekstowy) jest 5-10x tańszy niż `vision_only`

## 🛠️ Architektura

```
┌───────────────────────────────────────────────┐
│          ELECTRON OVERLAY (JS)                │
│  Transparent | Always-on-top | Click-through  │
│  SSE Client: analysis, window, agent, STT     │
└──────────────────┬────────────────────────────┘
                   │ SSE Stream
                   ↓
┌───────────────────────────────────────────────┐
│         FASTAPI BACKEND (Python)              │
│                                               │
│  EventBus ←── publish/subscribe ──→ EventStore│
│     ↑                                  ↑      │
│     │      PipelineOrchestrator        │      │
│     │  ┌─────────────────────────────┐ │      │
│     │  │ 1. ScanWindows (xdotool)    │ │      │
│     │  │ 2. DetectActiveWindow       │ │      │
│     │  │ 3. CaptureScreen (mss/grim) │ │      │
│     │  │ 4. CropWindows (per-app)    │ │      │
│     │  │ 5. BuildContext (profiles)  │ │      │
│     │  │ 6. Analyze (OCR+LLM)        │ │      │
│     │  │ 7. SuggestActions (agent)   │ │      │
│     │  │ 8. BuildBroadcast (SSE)     │ │      │
│     │  └─────────────────────────────┘ │      │
│     │                                  │      │
│  CommandHandlers (write)  QueryHandlers(read) │
│  ReadModel (materialized views)               │
│                                               │
│  STT (Deepgram) → transcript → EventBus      │
│  ConfigService → .env read/write + audio      │
└───────────────────────────────────────────────┘
```

> Szczegółowa dokumentacja: **[ARCHITECTURE.md](ARCHITECTURE.md)**

## 📦 Instalacja

### Wymagania

- **Python 3.11+**
- **Node.js 18+**
- **Linux:** `xdotool`, `xprop`, `xrandr` (Window Awareness)
- **Opcjonalnie:** [Ollama](https://ollama.ai) (lokalne modele AI — zero kosztów)
- **Opcjonalnie:** klucze API do zdalnych providerów (Gemini, OpenAI, Claude itd.)
- **Opcjonalnie:** `tesseract-ocr` / `paddleocr` / `easyocr` (OCR)

### Szybka instalacja (Makefile)

```bash
make setup    # tworzy venv, instaluje deps, kopiuje .env
make run      # uruchamia backend + overlay + otwiera config UI w przeglądarce
```

Po starcie automatycznie otwiera się:
- **Config UI** — `http://localhost:PORT/config/ui` — konfiguracja przez przeglądarkę
- **Screenshot Browser** — `http://localhost:PORT/browser` — podgląd zrzutów

### Krok 1: Backend (Python)

```bash
cd backend
pip install -r requirements.txt

# Linux: zainstaluj narzędzia systemowe
sudo apt install xdotool xprop xrandr tesseract-ocr tesseract-ocr-pol
```

### Krok 2: Konfiguracja

**Opcja A — przez przeglądarkę (zalecane):**
```bash
make run
# Otwórz http://localhost:8001/config/ui
```

**Opcja B — ręcznie:**
```bash
cp .env.example .env
nano .env
```

```env
# Lokalny model (zero kosztów, wymaga Ollama):
VISION_MODEL=ollama/llava

# Lub zdalny (wymaga klucza API):
# VISION_MODEL=gemini/gemini-2.0-flash
# GEMINI_API_KEY=twój_klucz
```

> 📖 Pełna lista providerów i modeli: **[PROVIDERS.md](PROVIDERS.md)**

### Krok 3: Frontend (Electron)

```bash
cd overlay
npm install
```

## 🚀 Uruchomienie

### Opcja 1: Makefile (zalecane)

```bash
make run      # Backend + overlay + config UI + screenshot browser
make stop     # Zatrzymaj wszystko (backend, overlay, wayland screencast)
make status   # Sprawdź czy backend działa
```

### Opcja 2: Oddzielne terminale (Development)

**Terminal 1 - Backend:**
```bash
make run-backend
```

**Terminal 2 - Overlay:**
```bash
make run-overlay
```

### Opcja 3: Skrypty startowe

```bash
./start.sh   # Linux/macOS
start.bat    # Windows
```

## ⌨️ Skróty klawiszowe

- `Ctrl+Shift+A` - Pokaż/Ukryj overlay
- `Ctrl+Shift+Q` - Zamknij asystenta

## 🎯 Jak to działa — 8-etapowy Pipeline

1. **ScanWindows** — skanuje wszystkie widoczne okna (`xdotool`), grupuje wg kategorii
2. **DetectActiveWindow** — wykrywa aktywne okno, ROI, kontekst git
3. **CaptureScreen** — przechwytuje ekran (mss/grim/PipeWire), detekcja zmian (phash)
4. **CropWindows** — wycina każdą aplikację osobno z pełnoekranowego zrzutu
5. **BuildContext** — buduje prompt: profil aplikacji + kontekst okna + transkrypcja STT
6. **Analyze** — OCR + Vision AI (hybrid/vision_only/ocr_only/ocr+vision)
7. **SuggestActions** — Shell Agent sugeruje bezpieczne komendy na podstawie tekstu
8. **BuildBroadcast** — wysyła wyniki SSE do overlay

Każdy krok emituje typowany event do EventBus, persystencja w SQLite, ReadModel.

## 📁 Struktura projektu

```
aidesk/
├── backend/
│   ├── server.py              # FastAPI — główny serwer + endpointy
│   ├── capture.py             # Screen capture (mss/grim/PipeWire)
│   ├── analyzer.py            # Vision AI (LiteLLM → 100+ providerów)
│   ├── ocr_engines.py         # OCR Manager (PaddleOCR/EasyOCR/Tesseract)
│   ├── stt.py                 # Deepgram STT streaming
│   ├── context.py             # Context Manager (sliding window)
│   ├── window_aware.py        # Window Awareness (xdotool/xprop)
│   ├── app_profiles.py        # Per-app analysis profiles (7 kategorii)
│   ├── shell_agent.py         # Shell Agent (suggest + execute)
│   ├── process_scanner.py     # Skanowanie widocznych okien + procesów
│   ├── window_cropper.py      # Per-app wycinki z fullscreen screenshot
│   ├── event_bus.py           # EventBus + EventStore (Event Sourcing)
│   ├── pipeline.py            # 8-step PipelineOrchestrator (SOLID)
│   ├── command_handlers.py    # CQRS write side (6 command handlers)
│   ├── query_handlers.py      # CQRS read side + ReadModel
│   ├── protocols.py           # 11 Protocol interfaces (ISP + DIP)
│   ├── config_service.py      # .env read/write + audio device discovery
│   ├── diagnostics.py         # Auto-diagnostics (15 health checks)
│   ├── wayland_screencast.py  # PipeWire ScreenCast daemon (Wayland)
│   ├── config.html            # Konfiguracja UI (Tailwind CSS)
│   ├── screenshots.html       # Screenshot browser UI
│   ├── requirements.txt       # Python dependencies
│   ├── test_setup.py          # Weryfikacja instalacji (12 checks)
│   ├── tests/
│   │   ├── test_units.py      # 68 unit tests
│   │   └── test_e2e.py        # 7 e2e API tests
│   └── .env                   # Konfiguracja (NIE commitować!)
├── overlay/
│   ├── main.js                # Electron main process
│   ├── preload.js             # Electron preload
│   ├── index.html             # Overlay UI
│   ├── styles.css             # Overlay styling
│   ├── app.js                 # SSE client logic
│   └── package.json           # Node dependencies
├── Makefile                   # setup, run, stop, test, status, clean, logs
├── ARCHITECTURE.md            # Szczegółowa dokumentacja architektury
├── PROVIDERS.md               # Konfiguracja 100+ providerów AI
├── CHANGELOG.md               # Historia zmian
├── INSTALL.md                 # Krok po kroku instalacja
├── QUICKSTART.md              # Szybki start (5 minut)
├── start.sh / start.bat       # Skrypty startowe
└── LICENSE                    # Apache License
```

## 🌐 API Endpoints

| Grupa | Endpoint | Opis |
|-------|----------|------|
| **Core** | `GET /stream` | SSE real-time updates |
| | `GET /status` | Bieżący status |
| | `GET /stats` | Szczegółowe statystyki |
| | `GET /health` | Health check (12 komponentów) |
| **Window** | `GET /window` | Aktywne okno (live) |
| | `GET /processes` | Wszystkie okna wg kategorii |
| | `GET /screen/organized` | Per-app wycinki + kategorie |
| **OCR** | `GET /ocr/engines` | Dostępne silniki OCR |
| | `POST /ocr/engine/{name}` | Zmień silnik w locie |
| | `POST /ocr/benchmark` | Benchmark A/B wszystkich silników |
| **Agent** | `GET /agent/actions` | Oczekujące akcje |
| | `POST /agent/execute/{id}` | Wykonaj akcję |
| | `POST /agent/run` | Uruchom bezpieczną komendę |
| **Events** | `GET /events` | Zapytanie do event store |
| | `GET /events/stats` | Statystyki EventBus |
| | `GET /pipeline` | Kroki i metryki pipeline |
| **CQRS** | `GET /read-model` | Materialized views |
| | `GET /read-model/pipeline` | Pipeline execution state |
| | `GET /read-model/stats` | Enriched stats + event metrics |
| **Config** | `GET /config` | Pełna konfiguracja + schemat |
| | `POST /config` | Aktualizuj .env |
| | `GET /config/ui` | Web UI konfiguracji |
| | `GET /audio/devices` | Wykrywanie urządzeń audio |
| **Files** | `GET /browser` | Screenshot browser |
| | `GET /crops` | Lista per-app wycinków |

## 🔧 Konfiguracja

### Przez przeglądarkę (zalecane)

Otwórz `http://localhost:PORT/config/ui` — 7 grup ustawień z wykrywaniem urządzeń audio.

### Zmiana modelu Vision AI

W `.env` ustaw `VISION_MODEL` (format LiteLLM: `provider/model`):

- `ollama/llava` - Lokalne, zero kosztów (domyślne)
- `gemini/gemini-2.0-flash` - Gemini (najtańszy cloud, $0.10/1M tokens)
- `gpt-4o-mini` - OpenAI (balans cena/jakość)
- `anthropic/claude-sonnet-4-20250514` - Claude (najlepsza jakość)
- `groq/llava-v1.5-7b-4096-preview` - Groq (najszybszy, darmowy tier)

> Pełna dokumentacja: **[PROVIDERS.md](PROVIDERS.md)**

### Tryby analizy

- `hybrid` — **rekomendowany** — OCR + LLM tekstowy (5-10x tańszy)
- `vision_only` — obraz + VLM
- `ocr_only` — tylko OCR, zero kosztów LLM
- `ocr_plus_vision` — OCR kontekst + obraz + VLM

### Czułość detekcji zmian

`CHANGE_THRESHOLD` w `.env`:
- `5` = bardzo czuły (więcej API calls, wyższe koszty)
- `8` = rekomendowane (dobry balans)
- `15` = mało czuły (mniej API calls, może pominąć drobne zmiany)

### Urządzenia audio

Konfiguruj mikrofon, monitor głośnika i wyjście audio w `/config/ui` lub w `.env`:
```env
STT_INPUT_DEVICE=alsa_input.usb-Generic_USB_Audio-00.iec958-stereo
STT_MONITOR_DEVICE=alsa_output.pci-0000_01_00.1.hdmi-stereo.monitor
AUDIO_OUTPUT_DEVICE=alsa_output.pci-0000_01_00.1.hdmi-stereo
```

## 🧪 Testy

```bash
make test          # Uruchom wszystkie testy (75 tests: 68 unit + 7 e2e)
make test-units    # Tylko unit testy (szybkie, bez serwera)
make test-e2e      # Tylko e2e API testy (z lifespan)
make test-setup    # Weryfikacja instalacji (12 checks)
```

## 🐛 Troubleshooting

### Backend nie startuje
```bash
make status                           # Sprawdź czy działa
make stop                             # Zabij stare procesy
curl http://localhost:8001/health     # Health check
```

### Overlay nie łączy się
```bash
curl http://localhost:8001/status     # Sprawdź backend
# Otwórz DevTools w overlay: Ctrl+Shift+I
```

### STT nie działa
```bash
# Sprawdź urządzenia audio
curl http://localhost:8001/audio/devices

# Lub w config UI
xdg-open http://localhost:8001/config/ui
```

### Wayland — czarny ekran
Na GNOME Wayland (Shell 49+) standardowe narzędzia nie działają.
AIDesk automatycznie używa PipeWire ScreenCast Portal — przy pierwszym uruchomieniu
pojawi się dialog GNOME z prośbą o zgodę na udostępnienie ekranu.

### Wysokie zużycie CPU
- Zwiększ `MIN_CAPTURE_INTERVAL` do 2.0
- Zwiększ `CHANGE_THRESHOLD` do 12-15
- Użyj trybu `hybrid` zamiast `vision_only`

## 📊 Monitoring

- **Stats**: `http://localhost:PORT/stats` — szczegółowe statystyki
- **Health**: `http://localhost:PORT/health` — 12 komponentów
- **Diagnostics**: auto-check co 30s, wyniki w overlay
- **Event Store**: `http://localhost:PORT/events` — pełny audit trail
- **Pipeline**: `http://localhost:PORT/pipeline` — metryki kroków
- **Logi**: `make logs` — tail backend log

## 🔐 Bezpieczeństwo i prywatność

- **Wszystkie dane zostają lokalnie** — zrzuty ekranu nie są zapisywane
- API keys w `.env` są w `.gitignore`
- Overlay nie wysyła danych nigdzie poza backend
- Shell Agent: 15+ zablokowanych wzorców (rm -rf /, fork bomb...), whitelist bezpiecznych komend
- Electron: `contextIsolation: true`, `nodeIntegration: false`
- Event Store: lokalna baza SQLite, auto-prune przy 50k eventów

### Wersja lokalna (zero cloud API calls)

```env
VISION_MODEL=ollama/llava:13b
ENABLE_STT=false
```

## 📖 Dokumentacja

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — szczegółowa architektura (pipeline, CQRS, event sourcing)
- **[PROVIDERS.md](PROVIDERS.md)** — konfiguracja 100+ providerów AI (Ollama, Gemini, OpenAI, Claude...)
- **[INSTALL.md](INSTALL.md)** — krok po kroku instalacja
- **[QUICKSTART.md](QUICKSTART.md)** — szybki start w 5 minut
- **[CHANGELOG.md](CHANGELOG.md)** — historia zmian

## 🤝 Contributing

Pull requesty mile widziane! Sprawdź CONTRIBUTING.md dla guidelines.

## 📄 Licencja

Apache License — użyj jak chcesz, komercyjnie lub prywatnie.

## 🙏 Podziękowania

Projekt inspirowany przez:
- [Screenpipe](https://github.com/mediar-ai/screenpipe) — 24/7 screen + audio capture
- [MIRIX](https://github.com/acui51/mirix) — Multi-agent memory system
- [Natively](https://github.com/ShivanshDubey1/natively) — Voice AI assistant

## 🔗 Przydatne linki

- [LiteLLM Providers](https://docs.litellm.ai/docs/providers) — 100+ AI providerów
- [Deepgram Docs](https://developers.deepgram.com/)
- [Gemini API](https://ai.google.dev/docs)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Electron](https://www.electronjs.org/docs/latest/)

---

**Wersja:** 2.0.9 | **Aktualizacja:** 2026-02-14 | **Kontakt:** [info@softreck.com]
