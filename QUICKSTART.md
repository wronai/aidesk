# 🚀 Quick Start Guide

Kompletny przewodnik uruchomienia AI Desktop Assistant w 5 minut.

## ⚡ Szybka instalacja

### 1. Pobierz projekt

```bash
# Rozpakuj pobraną paczkę
unzip ai-desktop-assistant.zip
cd ai-desktop-assistant
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
sudo apt install python3.11 python3-pip nodejs npm
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

### 4. Edytuj konfigurację

```bash
cd backend
cp .env.example .env
nano .env  # lub notepad .env na Windows
```

**Minimum configuration:**
```env
DEEPGRAM_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
VISION_PROVIDER=gemini
STT_LANGUAGE=pl
```

### 5. Uruchom!

**Linux/macOS:**
```bash
./start.sh
```

**Windows:**
```bash
start.bat
```

**Lub ręcznie (2 terminale):**

Terminal 1 - Backend:
```bash
cd backend
pip install -r requirements.txt
python server.py
```

Terminal 2 - Overlay:
```bash
cd overlay
npm install
npm start
```

## ✅ Weryfikacja

Po uruchomieniu powinieneś zobaczyć:

1. **Backend**: `INFO: Application startup complete` w terminalu
2. **Overlay**: Przeźroczyste okno w prawym dolnym rogu ekranu
3. **Status**: Zielona kropka = połączono

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
# Sprawdź logi
cd backend
python server.py
# Sprawdź czy port 8000 jest wolny
lsof -i :8000  # macOS/Linux
netstat -ano | findstr :8000  # Windows
```

### Overlay nie łączy się
```bash
# Sprawdź czy backend działa
curl http://localhost:8000/health

# Otwórz DevTools w overlay (odkomentuj w main.js):
# overlay.webContents.openDevTools({ mode: 'detach' });
```

### STT nie działa
```bash
# Test mikrofonu
python -c "import sounddevice as sd; print(sd.query_devices())"

# Test Deepgram key
curl -H "Authorization: Token YOUR_KEY" https://api.deepgram.com/v1/projects
```

### Wysokie koszty API
Sprawdź logi kosztów:
```bash
tail -f backend/logs/usage.log
```

Zwiększ `CHANGE_THRESHOLD` w `.env` (np. do 15) aby zmniejszyć ilość API calls.

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
