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
ai-desktop-assistant/
├── backend/
│   ├── server.py           # FastAPI główny serwer
│   ├── capture.py          # Screen capture + change detection
│   ├── analyzer.py         # Vision AI (Gemini/GPT-4o/Claude)
│   ├── stt.py             # Deepgram STT streaming
│   ├── context.py         # Context manager
│   ├── requirements.txt   # Python dependencies
│   └── .env              # API keys (NIE commitować!)
├── overlay/
│   ├── main.js           # Electron main process
│   ├── preload.js        # Electron preload
│   ├── index.html        # Overlay UI
│   ├── styles.css        # Overlay styling
│   ├── app.js            # SSE client logic
│   └── package.json      # Node dependencies
├── start.sh              # Startup script (Linux/macOS)
├── start.bat             # Startup script (Windows)
├── README.md             # Ten plik
└── LICENSE               # MIT License
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
# Sprawdź czy port 8000 jest wolny
lsof -i :8000  # Linux/macOS
netstat -ano | findstr :8000  # Windows

# Zainstaluj brakujące biblioteki
pip install -r backend/requirements.txt --upgrade
```

### Overlay nie łączy się z backendem
```bash
# Sprawdź czy backend działa
curl http://localhost:8000/status

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

## 📊 Monitoring kosztów

Backend loguje statystyki do `logs/usage.log`:
```
[2025-02-14 10:30:45] Vision API call: gemini-2.0-flash | tokens: 1548 | cost: $0.00015
[2025-02-14 10:30:46] STT streaming: 15s | cost: $0.00192
```

Podgląd dziennych kosztów:
```bash
python backend/analyze_costs.py
```

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

Pull requesty mile widziane! Sprawdź CONTRIBUTING.md dla guidelines.

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
