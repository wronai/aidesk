# AI Desktop Assistant - Real-time Screen + Voice Assistant

Zaawansowany asystent AI z analizą ekranu w czasie rzeczywistym i rozpoznawaniem mowy po polsku.

## 🚀 Główne funkcje

- ✅ **Real-time screen capture** - analiza całego pulpitu (wszystkie okna)
- ✅ **Inteligentna detekcja zmian** - tylko istotne zmiany są analizowane (70-90% redukcja kosztów)
- ✅ **Polish STT** - rozpoznawanie mowy po polsku przez Deepgram Nova-3
- ✅ **Vision AI** - analiza zawartości ekranu (Gemini 2.0 Flash / GPT-4o)
- ✅ **Transparent overlay** - zawsze na wierzchu, przeźroczyste okno z podpowiedziami
- ✅ **Auto-refresh** - aktualizacja co sekundę podczas aktywności
- ✅ **Cross-platform** - Windows, macOS, Linux

## 📊 Szacunkowe koszty API

| Komponent | Koszt/miesiąc (8h/dzień) |
|-----------|--------------------------|
| **Deepgram Nova-3 STT** | ~$81 |
| **Gemini 2.0 Flash Vision** | ~$30-60 |
| **RAZEM** | **~$110-140** |

## 🛠️ Architektura

```
┌─────────────────────────────────────┐
│     ELECTRON OVERLAY (Svelte)       │
│  - Transparent window                │
│  - Always on top                     │
│  - SSE connection                    │
└──────────────┬──────────────────────┘
               │ SSE Stream
               ↓
┌─────────────────────────────────────┐
│     FASTAPI BACKEND (Python)        │
│  ┌────────────┐  ┌───────────────┐  │
│  │  Screen    │  │   STT Service │  │
│  │  Capture   │  │   (Deepgram)  │  │
│  │  (mss)     │  │               │  │
│  └─────┬──────┘  └───────┬───────┘  │
│        │                 │           │
│        ↓                 ↓           │
│  ┌─────────────────────────────┐    │
│  │    Change Detection         │    │
│  │    (imagehash)              │    │
│  └──────────┬──────────────────┘    │
│             ↓                        │
│  ┌─────────────────────────────┐    │
│  │  AI Analyzer                │    │
│  │  - Gemini 2.0 Flash         │    │
│  │  - GPT-4o (optional)        │    │
│  │  - Claude Sonnet (optional) │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

## 📦 Instalacja

### Wymagania

- Python 3.11+
- Node.js 18+
- Konto API: [Deepgram](https://deepgram.com) (darmowe $200 kredytu)
- Konto API: [Google AI Studio](https://makersuite.google.com/app/apikey) (darmowy Gemini)

### Krok 1: Backend (Python)

```bash
cd backend
pip install -r requirements.txt
```

### Krok 2: Konfiguracja API

Skopiuj `.env.example` do `.env` i wypełnij swoimi kluczami:

```bash
cp .env.example .env
nano .env  # lub notepad .env na Windows
```

```env
# Backend API Keys
DEEPGRAM_API_KEY=your_deepgram_key_here
GEMINI_API_KEY=your_gemini_key_here
OPENAI_API_KEY=your_openai_key_here  # opcjonalnie

# Vision Provider (gemini | openai | claude)
VISION_PROVIDER=gemini

# STT Language
STT_LANGUAGE=pl

# Change Detection Sensitivity (1-20, lower = more sensitive)
CHANGE_THRESHOLD=8

# Capture Interval (seconds)
MIN_CAPTURE_INTERVAL=1.0
```

### Krok 3: Frontend (Electron)

```bash
cd overlay
npm install
```

## 🚀 Uruchomienie

### Opcja 1: Oddzielne terminale (Development)

**Terminal 1 - Backend:**
```bash
cd backend
python server.py
# Backend działa na http://localhost:8000
```

**Terminal 2 - Overlay:**
```bash
cd overlay
npm start
# Overlay uruchomi się automatycznie
```

### Opcja 2: Jeden skrypt (Produkcja)

```bash
./start.sh   # Linux/macOS
start.bat    # Windows
```

## ⌨️ Skróty klawiszowe

- `Ctrl+Shift+A` - Pokaż/Ukryj overlay
- `Ctrl+Shift+Q` - Zamknij asystenta

## 🎯 Jak to działa

1. **Screen Capture** - Przechwytuje ekran co 1 sekundę (mss library)
2. **Change Detection** - Perceptual hash sprawdza czy ekran się zmienił
3. **AI Analysis** - Tylko zmienione ekrany są wysyłane do Gemini/GPT-4o
4. **STT Streaming** - Deepgram nasłuchuje mikrofonu i transkrybuje po polsku
5. **Context Management** - Ostatnie 20 interakcji w kontekście
6. **Overlay Display** - SSE stream aktualizuje overlay w czasie rzeczywistym

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

W `.env` ustaw `VISION_PROVIDER`:

- `gemini` - Gemini 2.0 Flash (najtańszy, $0.10/1M tokens)
- `openai` - GPT-4o-mini (balans cena/jakość)
- `claude` - Claude Sonnet 4.5 (najlepsza jakość, najdroższy)

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

Zamień w `analyzer.py`:
```python
# Użyj Ollama z lokalnym LLaVA
provider = "ollama"
model = "llava:13b"
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
