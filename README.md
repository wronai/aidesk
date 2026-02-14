# AI Desktop Assistant — Real-time Screen + Voice Copilot (v2.1.9)

Zaawansowany asystent AI z analizą ekranu w czasie rzeczywistym, rozpoznawaniem mowy i świadomością kontekstu okien.

> Ten README został zsynchronizowany z aktualnym kodem oraz indeksem `project.functions.toon` (generated: 2026-02-14T13:13:02.896116).

## 🚀 Główne funkcje

- ✅ **14-etapowy pipeline analizy** — modularny, SOLID-compliant, event-driven, profile FAST/NORMAL/FULL
- ✅ **Window Awareness + Process Scanner** — aktywne okno, klasy aplikacji, kontekst git, filtrowanie okien serwisowych
- ✅ **Per-app crops** — oddzielne wycinki okien + organizacja ekranu według kategorii
- ✅ **Vision AI** — 100+ providerów via LiteLLM (Gemini, GPT-4o, Claude, Ollama...)
- ✅ **3 silniki OCR** — PaddleOCR, EasyOCR, Tesseract + benchmark runtime
- ✅ **4 tryby analizy** — `vision_only`, `ocr_only`, `hybrid`, `ocr_plus_vision`
- ✅ **Tier 1 Intelligence** — multi-monitor, semantic memory, action templates, OCR post-process, predictive prefetch
- ✅ **Clipboard Intelligence** — kolejka schowka, sugestie wklejania, snippets, auto-copy z kontekstu
- ✅ **Skill Router** — analiza zaznaczonego tekstu i wykonywanie akcji per-skill (`/analyze-selection`, `/skill/execute`)
- ✅ **Shell Agent** — sugeruje i wykonuje bezpieczne komendy (z approval flow)
- ✅ **Event Sourcing + CQRS** — EventBus + SQLite EventStore + ReadModel
- ✅ **Plugin Loader** — dynamiczne ładowanie pluginów z `backend/plugins`
- ✅ **Konfiguracja przez przeglądarkę** — `/config/ui` + discovery urządzeń audio
- ✅ **Transparent overlay** — Electron, always-on-top, click-through
- ✅ **Wayland + X11** — auto-detekcja backendu przechwytywania (PipeWire/mss/grim)

## 📌 Snapshot projektu (na podstawie `project.functions.toon`)

- **89 modułów** (backend + overlay + testy)
- **10 modułów routingu FastAPI** (`backend/routes/*.py`)
- **19 plików testowych** w `backend/tests`
- **Rozszerzalność**: `plugins/` (interfejs pluginów) + `skills/` (system umiejętności)

## 📊 Szacunkowe koszty API

| Komponent | Koszt/miesiąc (8h/dzień) |
| ----------- | -------------------------- |
| **Deepgram Nova-3 STT** | ~$81 |
| **Gemini 2.0 Flash (hybrid)** | ~$5-15 |
| **Gemini 2.0 Flash (vision)** | ~$30-60 |
| **RAZEM (hybrid)** | **~$86-96** |

> Tryb `hybrid` (OCR + LLM tekstowy) jest 5-10x tańszy niż `vision_only`

## 🛠️ Architektura

```text
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
│     │  │ 14-step adaptive pipeline:  │ │      │
│     │  │ scan→detect→capture→crop    │ │      │
│     │  │ →multi-monitor→context      │ │      │
│     │  │ →analyze→ocr-post→actions   │ │      │
│     │  │ →templates→memory→predict   │ │      │
│     │  │ →clipboard→broadcast         │ │      │
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

> ⚠️ Uwaga dot. portu: overlay (`overlay/app.js` i `overlay/services/sse.js`) domyślnie łączy się z `http://localhost:8001`. Ustaw `PORT=8001` w `backend/.env` albo zmień `BACKEND_URL` po stronie overlay.

### Krok 1: Backend (Python)

```bash
cd backend
pip install -r requirements.txt

# Linux: zainstaluj narzędzia systemowe
sudo apt install xdotool xprop xrandr tesseract-ocr tesseract-ocr-pol
```

### Krok 2: Konfiguracja

#### Opcja A — przez przeglądarkę (zalecane)

```bash
make run
# Otwórz http://localhost:8001/config/ui
```

#### Opcja B — ręcznie

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

#### Terminal 1 - Backend

```bash
make run-backend
```

#### Terminal 2 - Overlay

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

## 🎯 Jak to działa — aktualny pipeline (14 kroków)

Kolejność kroków (z `backend/pipeline.py:create_pipeline`):

1. **ScanWindows**
2. **DetectActiveWindow**
3. **CaptureScreen**
4. **CropWindows**
5. **MultiMonitor**
6. **BuildContext**
7. **Analyze**
8. **OCRPostProcess**
9. **SuggestActions**
10. **ActionTemplates**
11. **SemanticMemory**
12. **Predictive**
13. **Clipboard**
14. **BuildBroadcast**

Dodatkowo pipeline używa profili `FAST`, `NORMAL`, `FULL` (adaptacyjny dobór per tick).
Każdy krok emituje event do EventBus, a dane mogą być projekowane do ReadModel (CQRS).

## 📁 Struktura projektu

```text
aidesk/
├── backend/
│   ├── server.py              # FastAPI + lifespan + logowanie
│   ├── bootstrap.py           # Fazy startu/shutdown komponentów
│   ├── analysis_loop.py       # Główna pętla pipeline
│   ├── pipeline.py            # Kroki pipeline + profile FAST/NORMAL/FULL
│   ├── routes/                # 10 modułów API (core/ocr/windows/agent/...)
│   ├── skills/                # Skill Router i built-in skills
│   ├── plugins/               # Plugin loader + interfejs pluginów
│   ├── tests/                 # Testy jednostkowe i e2e
│   └── .env.example           # Szablon konfiguracji
├── overlay/
│   ├── main.js               # Electron main process
│   ├── app.js                # App (web components)
│   ├── components/           # Komponenty UI overlay
│   ├── services/sse.js       # SSE service
│   └── package.json
├── project.functions.toon     # Indeks modułów/funkcji projektu
├── README.md
├── ARCHITECTURE.md
├── CHANGELOG.md
└── TODO.md
```

## 🔧 Konfiguracja zaawansowana

### Zmiana modelu Vision AI

W `.env` ustaw `VISION_MODEL` (format LiteLLM: `provider/model`):

- `ollama/llava` - Lokalne, zero kosztów (domyślne)
- `gemini/gemini-2.0-flash` - Gemini (najtańszy cloud, $0.10/1M tokens)
- `gpt-4o-mini` - OpenAI (balans cena/jakość)
- `anthropic/claude-sonnet-4-20250514` - Claude (najlepsza jakość)
- `groq/llava-v1.5-7b-4096-preview` - Groq (najszybszy, darmowy tier)

> Pełna dokumentacja: **[PROVIDERS.md](PROVIDERS.md)**

### Dostosowanie czułości detekcji zmian

`CHANGE_THRESHOLD` w `.env`:

- `5` = bardzo czuły (więcej API calls, wyższe koszty)
- `8` = rekomendowane (dobry balans)
- `15` = mało czuły (mniej API calls, może pominąć drobne zmiany)

### Wyłączenie STT

Jeśli nie potrzebujesz rozpoznawania mowy, ustaw w `.env`:

```env
ENABLE_STT=false
```

## 🐛 Troubleshooting

### Backend nie startuje

```bash
# Sprawdź czy port z backend/.env (PORT) jest wolny
lsof -i :PORT  # Linux/macOS
netstat -ano | findstr :PORT  # Windows

# Zainstaluj brakujące biblioteki
pip install -r backend/requirements.txt --upgrade
```

### Overlay nie łączy się z backendem

```bash
# Sprawdź czy backend działa
curl http://localhost:PORT/status

# Sprawdź logi w konsoli Electron (Ctrl+Shift+I)
```

### STT nie działa

```bash
# Sprawdź mikrofon
python -c "import sounddevice as sd; print(sd.query_devices())"

# Sprawdź klucz Deepgram
curl -H "Authorization: Token YOUR_KEY" https://api.deepgram.com/v1/projects
```

### Wysokie zużycie CPU

- Zwiększ `MIN_CAPTURE_INTERVAL` w `.env` (np. 2.0 zamiast 1.0)
- Zmniejsz rozdzielczość w `capture.py` (np. 960x540 zamiast 1280x720)

## 📊 Monitoring i observability

Najważniejsze endpointy diagnostyczne:

- `GET /stats` — statystyki komponentów i uptime
- `GET /health` — health check komponentów krytycznych
- `GET /events` i `GET /events/stats` — EventStore (SQLite)
- `GET /pipeline` — kroki i statystyki pipeline
- `GET /read-model` / `/read-model/pipeline` / `/read-model/stats` — widoki CQRS
- `GET /traces` — metryki i ostatnie span-y tracer-a
- `GET /diagnostics` — automatyczne checki stanu systemu

Pliki logów:

- `logs/assistant.log`
- `logs/logs.sqlite`
- `logs/events.db`
- `logs/nfo_aidesk.md`

## 🔐 Bezpieczeństwo i prywatność

- **Wszystkie dane zostają lokalnie** - zrzuty ekranu i audio nie są zapisywane
- API keys w `.env` są w `.gitignore`
- Overlay nie wysyła danych nigdzie poza backend
- Możesz używać lokalnych modeli (Ollama/LM Studio) zamiast cloud API

### Wersja lokalna (zero cloud API calls)

W `backend/.env` ustaw model Ollama:
```env
VISION_MODEL=ollama/llava:13b
ENABLE_STT=false
```

## 🤝 Contributing

Pull requesty mile widziane — opisz kontekst zmiany, kroki reprodukcji i sposób testowania.

## 📄 Licencja

Apache License - użyj jak chcesz, komercyjnie lub prywatnie.

## 🙏 Podziękowania

Projekt inspirowany przez:

- [Screenpipe](https://github.com/mediar-ai/screenpipe) - 24/7 screen + audio capture
- [MIRIX](https://github.com/acui51/mirix) - Multi-agent memory system
- [Natively](https://github.com/ShivanshDubey1/natively) - Voice AI assistant

## 🔗 Przydatne linki

- [Dokumentacja Deepgram](https://developers.deepgram.com/)
- [Gemini API Docs](https://ai.google.dev/docs)
- [OpenAI Vision Guide](https://platform.openai.com/docs/guides/vision)
- [Electron Docs](https://www.electronjs.org/docs/latest/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)

---

**Pytania? Problemy?** Otwórz issue na GitHubie lub kontakt: [info@softreck.com]
