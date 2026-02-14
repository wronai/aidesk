# 📦 Instalacja AI Desktop Assistant (v2.0.9)

## Pobierz projekt

```bash
git clone https://github.com/wronai/aidesk.git
cd aidesk
```

## Struktura projektu

```
aidesk/
├── backend/                     # Python backend (FastAPI)
│   ├── server.py               # Główny serwer + API endpointy
│   ├── capture.py              # Screen capture (mss/grim/PipeWire)
│   ├── analyzer.py             # Vision AI (LiteLLM → 100+ providerów)
│   ├── ocr_engines.py          # OCR Manager (PaddleOCR/EasyOCR/Tesseract)
│   ├── stt.py                  # Deepgram STT streaming
│   ├── context.py              # Context Manager
│   ├── window_aware.py         # Window Awareness (xdotool/xprop)
│   ├── app_profiles.py         # Per-app analysis profiles
│   ├── shell_agent.py          # Shell Agent (suggest + execute)
│   ├── process_scanner.py      # Skanowanie okien + procesów
│   ├── window_cropper.py       # Per-app wycinki ze screenshota
│   ├── event_bus.py            # EventBus + EventStore (Event Sourcing)
│   ├── pipeline.py             # 8-step Pipeline (SOLID)
│   ├── command_handlers.py     # CQRS write side
│   ├── query_handlers.py       # CQRS read side + ReadModel
│   ├── protocols.py            # 11 Protocol interfaces
│   ├── config_service.py       # .env read/write + audio devices
│   ├── diagnostics.py          # Auto-diagnostics (15 checks)
│   ├── wayland_screencast.py   # PipeWire ScreenCast (Wayland)
│   ├── config.html             # Web UI konfiguracji
│   ├── screenshots.html        # Screenshot browser
│   ├── requirements.txt        # Python dependencies
│   ├── .env.example            # Szablon konfiguracji
│   ├── test_setup.py           # Weryfikacja instalacji (12 checks)
│   └── tests/                  # 75 testów (68 unit + 7 e2e)
├── overlay/                     # Electron overlay (UI)
│   ├── main.js / app.js / index.html / styles.css
│   └── package.json
├── Makefile                     # setup, run, stop, test, status, clean
├── logs/                        # Logi (tworzone automatycznie)
├── README.md                    # Główna dokumentacja
├── ARCHITECTURE.md              # Architektura szczegółowo
├── PROVIDERS.md                 # Konfiguracja providerów AI
├── QUICKSTART.md                # Szybki start
├── CHANGELOG.md                 # Historia zmian
└── LICENSE                      # Apache License
```

## Krok po kroku

### 1. Wymagania systemowe

#### System operacyjny
- ✅ Windows 10/11
- ✅ macOS 12+ (Monterey lub nowszy)
- ✅ Linux (Ubuntu 20.04+, Fedora 35+, Arch)

#### Oprogramowanie
- **Python 3.11+** - https://www.python.org/downloads/
- **Node.js 18+** - https://nodejs.org/
- **Git** (opcjonalnie) - https://git-scm.com/
- **Linux:** `xdotool`, `xprop`, `xrandr` (Window Awareness)
- **Opcjonalnie:** `tesseract-ocr` (OCR)

#### Sprzęt
- CPU: Dowolny nowoczesny procesor (2+ rdzenie)
- RAM: 4GB minimum, 8GB zalecane
- Dysk: 500MB wolnego miejsca
- Mikrofon: Do rozpoznawania mowy (opcjonalnie)

### 2. Instalacja Python (jeśli nie masz)

**Windows:**
1. Pobierz instalator z https://www.python.org/downloads/
2. **WAŻNE**: Zaznacz "Add Python to PATH" podczas instalacji
3. Potwierdź instalację: `python --version`

**macOS:**
```bash
# Z Homebrew (zalecane)
brew install python@3.11

# Lub pobierz z python.org
```

**Linux:**
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.11 python3-pip python3-venv
sudo apt install xdotool xprop xrandr tesseract-ocr tesseract-ocr-pol

# Fedora
sudo dnf install python3.11 xdotool xprop xrandr tesseract

# Arch
sudo pacman -S python xdotool xorg-xprop xorg-xrandr tesseract
```

### 3. Instalacja Node.js (jeśli nie masz)

**Windows/macOS:**
- Pobierz instalator LTS z https://nodejs.org/

**Linux:**
```bash
# Ubuntu/Debian (NodeSource)
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs

# Fedora
sudo dnf install nodejs

# Arch
sudo pacman -S nodejs npm
```

### 4. Zdobądź klucze API

#### Deepgram (STT) - WYMAGANE ✅
1. Zarejestruj się: https://deepgram.com/signup
2. Przejdź do Console → API Keys
3. Kliknij "Create a New API Key"
4. Skopiuj klucz (wygląda jak: `abc123...`)
5. **Dostaniesz $200 darmowego kredytu!**

#### Google Gemini (Vision) - WYMAGANE ✅
1. Otwórz: https://makersuite.google.com/app/apikey
2. Zaloguj się kontem Google
3. Kliknij "Create API key"
4. Skopiuj klucz
5. **Darmowy tier: 15 zapytań/minutę**

#### OpenAI (opcjonalnie)
1. https://platform.openai.com/signup
2. API Keys → Create new secret key
3. Wymagana karta kredytowa (pay-as-you-go)

#### Anthropic Claude (opcjonalnie)
1. https://console.anthropic.com/
2. Settings → API Keys → Create Key
3. $5 minimum credit

### 5. Konfiguracja

```bash
cd ai-desktop-assistant/backend
cp .env.example .env
```

**Edytuj .env:**

**Linux/macOS:**
```bash
nano .env
```

**Windows:**
```bash
notepad .env
```

**Minimalna konfiguracja:**
```env
# WYMAGANE
DEEPGRAM_API_KEY=twoj_klucz_deepgram_tutaj
GEMINI_API_KEY=twoj_klucz_gemini_tutaj

# Ustawienia
VISION_PROVIDER=gemini
STT_LANGUAGE=pl
CHANGE_THRESHOLD=8
MIN_CAPTURE_INTERVAL=1.0
```

**Zaawansowana konfiguracja:**
```env
# Vision provider (gemini = najtańszy, claude = najlepszy)
VISION_PROVIDER=gemini

# Modele
GEMINI_MODEL=gemini-2.0-flash-exp
OPENAI_MODEL=gpt-4o-mini
CLAUDE_MODEL=claude-sonnet-4-20250514

# STT
STT_LANGUAGE=pl
DEEPGRAM_MODEL=nova-3

# Performance tuning
CHANGE_THRESHOLD=8          # 5=czuły, 15=mało czuły
MIN_CAPTURE_INTERVAL=1.0    # sekundy między zrzutami
SCREEN_WIDTH=1280           # rozdzielczość
SCREEN_HEIGHT=720
JPEG_QUALITY=60             # jakość JPEG (1-100)

# Feature flags
ENABLE_STT=true
ENABLE_VISION=true
DEBUG=false
```

### 6. Instalacja zależności

#### Backend (Python)

**Automatycznie (zalecane):**
```bash
cd backend
python3 -m venv venv          # Stwórz wirtualne środowisko
source venv/bin/activate      # Linux/macOS
# LUB
venv\Scripts\activate.bat     # Windows

pip install -r requirements.txt
```

**Ręcznie (jeśli automatyczna zawiedzie):**
```bash
pip install fastapi uvicorn mss imagehash Pillow opencv-python-headless \
            google-generativeai openai anthropic deepgram-sdk \
            sounddevice structlog python-dotenv
```

#### Frontend (Electron)

```bash
cd overlay
npm install
```

### 7. Weryfikacja instalacji

```bash
cd backend
python test_setup.py
```

Powinno pokazać:
```
✓ Python version (3.11+)
✓ All packages installed
✓ .env file exists
✓ API keys configured
✓ Screen capture working
✓ Audio devices detected
✓ APIs connected
```

### 8. Pierwsze uruchomienie

**Automatyczny start:**

Linux/macOS:
```bash
./start.sh
```

Windows:
```bash
start.bat
```

**Ręczny start (2 terminale):**

Terminal 1 - Backend:
```bash
cd backend
source venv/bin/activate  # Linux/macOS
# LUB venv\Scripts\activate  # Windows
python server.py
```

Terminal 2 - Overlay:
```bash
cd overlay
npm start
```

### 9. Weryfikacja działania

Po uruchomieniu powinno się pojawić:

1. **Terminal (backend):**
```
INFO: Screen capture initialized
INFO: Gemini initialized
INFO: Deepgram STT initialized
INFO: Application startup complete
INFO: Uvicorn running on http://127.0.0.1:8000
```

2. **Overlay:** Przeźroczyste okno w prawym dolnym rogu
3. **Status:** Zielona kropka = połączono ✅

### 10. Test funkcji

**Screen Analysis:**
- Zmień okno (np. otwórz przeglądarkę)
- Poczekaj 1-2 sekundy
- Overlay powinien pokazać analizę

**Speech-to-Text:**
- Powiedz coś do mikrofonu po polsku
- Transkrypcja pojawi się w overlay

**Shortcuts:**
- `Ctrl+Shift+A` - Pokaż/ukryj overlay
- `Ctrl+Shift+Q` - Zamknij asystenta

## Troubleshooting

### Python nie znaleziony

**Windows:**
```bash
# Dodaj Python do PATH
setx PATH "%PATH%;C:\Python311;C:\Python311\Scripts"
```

**Sprawdź instalację:**
```bash
python --version
python3 --version
```

### pip nie działa

```bash
python -m pip --version
python3 -m pip --version

# Upgrade pip
python -m pip install --upgrade pip
```

### sounddevice error na Linux

```bash
sudo apt install libportaudio2 portaudio19-dev
pip install sounddevice --upgrade
```

### Electron nie startuje

```bash
cd overlay
rm -rf node_modules package-lock.json
npm install
```

### Backend nie startuje - port zajęty

**Linux/macOS:**
```bash
lsof -i :8000
kill -9 <PID>
```

**Windows:**
```bash
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### API errors

**Deepgram:**
```bash
# Test klucza
curl -H "Authorization: Token YOUR_KEY" \
     https://api.deepgram.com/v1/projects
```

**Gemini:**
```python
import google.generativeai as genai
genai.configure(api_key="YOUR_KEY")
model = genai.GenerativeModel("gemini-2.0-flash-exp")
print(model.generate_content("test"))
```

### Wysokie zużycie CPU

1. Zwiększ `MIN_CAPTURE_INTERVAL` do 2.0
2. Zwiększ `CHANGE_THRESHOLD` do 12-15
3. Zmniejsz rozdzielczość: `SCREEN_WIDTH=960`, `SCREEN_HEIGHT=540`

### Overlay niewidoczny

**macOS:**
- System Preferences → Security & Privacy → Screen Recording
- Dodaj Electron do listy dozwolonych

**Linux (Wayland):**
- Może nie działać poprawnie, użyj X11

## Następne kroki

1. ✅ Monitoruj koszty: http://localhost:8000/stats
2. ✅ Dostosuj czułość w `.env`
3. ✅ Przeczytaj README.md dla zaawansowanych opcji
4. ✅ Dołącz do społeczności (GitHub Issues)

## Pomoc

- 📖 Dokumentacja: README.md, QUICKSTART.md
- 🐛 Problemy: GitHub Issues
- 💬 Community: Discord (link w README)
- 📧 Kontakt: support@example.com

