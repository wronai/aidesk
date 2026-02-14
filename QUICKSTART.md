# 🚀 Quick Start Guide (v2.0.9)

Kompletny przewodnik uruchomienia AI Desktop Assistant w 5 minut.

## ⚡ Szybka instalacja

### 1. Pobierz projekt

```bash
git clone https://github.com/wronai/aidesk.git
cd aidesk
```

### 2. Zainstaluj wymagania

**Python 3.11+** i **Node.js 18+** muszą być zainstalowane.

**macOS/Linux:**
```bash
# Sprawdź wersje
python3 --version
node --version

# Jeśli nie masz, zainstaluj:
# macOS (Homebrew):
brew install python@3.11 node

# Ubuntu/Debian:
sudo apt update
sudo apt install python3.11 python3-pip python3-venv nodejs npm
sudo apt install xdotool xprop xrandr tesseract-ocr tesseract-ocr-pol
```

**Windows:**
- Python: https://www.python.org/downloads/
- Node.js: https://nodejs.org/

### 3. Konfiguracja API

#### Deepgram (STT) - WYMAGANE
1. Zarejestruj się: https://deepgram.com
2. Dostaniesz **$200 darmowego kredytu**
3. Skopiuj API key z dashboardu

#### Google Gemini (Vision) - WYMAGANE
1. Otwórz: https://makersuite.google.com/app/apikey
2. Kliknij "Create API Key"
3. Skopiuj klucz (darmowy tier wystarczy)

#### Opcjonalnie
- OpenAI: https://platform.openai.com/api-keys
- Anthropic Claude: https://console.anthropic.com/

### 4. Setup + Uruchom

```bash
make setup    # Tworzy venv, instaluje deps, kopiuje .env
make run      # Uruchamia backend + overlay + otwiera config UI
```

Po starcie automatycznie otwiera się **Config UI** w przeglądarce — skonfiguruj klucze API i urządzenia audio.

**Lub ręcznie:**

```bash
cd backend
cp .env.example .env
nano .env  # ustaw klucze API
```

```env
DEEPGRAM_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
VISION_MODEL=gemini/gemini-2.0-flash
STT_LANGUAGE=pl
ANALYSIS_MODE=hybrid
```

```bash
# Terminal 1 - Backend:
make run-backend

# Terminal 2 - Overlay:
make run-overlay
```

## ✅ Weryfikacja

Po uruchomieniu powinieneś zobaczyć:

1. **Backend**: `INFO: Backend fully initialized and running` w terminalu
2. **Przeglądarka**: Config UI + Screenshot Browser
3. **Overlay**: Przeźroczyste okno w prawym dolnym rogu ekranu
4. **Status**: Zielona kropka = połączono

## ⌨️ Używanie

- **Ctrl+Shift+A** - Pokaż/ukryj overlay
- **Ctrl+Shift+Q** - Zamknij aplikację

Overlay automatycznie:
- Analizuje ekran gdy coś się zmienia
- Pokazuje sugestie AI
- Transkrybuje twoją mowę (po polsku)

## 🐛 Problemy?

### Backend nie startuje
```bash
make status                        # Sprawdź status
make stop                          # Zabij stare procesy
curl http://localhost:8001/health  # Health check
```

### Overlay nie łączy się
```bash
curl http://localhost:8001/status
# Otwórz DevTools w overlay: Ctrl+Shift+I
```

### STT nie działa
```bash
# Sprawdź urządzenia audio
curl http://localhost:8001/audio/devices

# Lub skonfiguruj w przeglądarce
xdg-open http://localhost:8001/config/ui
```

### Wayland — czarny ekran
AIDesk automatycznie używa PipeWire ScreenCast Portal na GNOME Wayland.
Przy pierwszym uruchomieniu pojawi się dialog z prośbą o zgodę.

### Wysokie koszty API
- Użyj trybu `hybrid` (5-10x tańszy niż `vision_only`)
- Zwiększ `CHANGE_THRESHOLD` do 12-15
- Skonfiguruj w `/config/ui`

## 📊 Monitorowanie

Statystyki dostępne pod:
- http://localhost:8000/stats - Szczegółowe statystyki
- http://localhost:8000/status - Bieżący status

## 🎯 Następne kroki

1. Dostosuj czułość detekcji w `.env`
2. Wypróbuj różne modele AI (gemini/openai/claude)
3. Sprawdź koszty po 1 godzinie użytkowania
4. Przeczytaj pełną dokumentację w README.md

## 💡 Wskazówki

- **Oszczędzaj koszty**: Ustaw `CHANGE_THRESHOLD=12` i `MIN_CAPTURE_INTERVAL=2.0`
- **Maksymalna jakość**: Użyj `VISION_PROVIDER=claude`
- **Najszybsze działanie**: Pozostań przy `gemini` (0.53s latency)
- **Offline STT**: Zainstaluj Faster-Whisper lokalnie

---

Problemy? Pytania? Otwórz issue na GitHubie!
