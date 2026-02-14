# AI Desktop Assistant - Dokumentacja Architektury

## Przegląd systemu

AI Desktop Assistant to real-time desktop monitoring system składający się z dwóch głównych komponentów:
- **Backend**: Python/FastAPI - przechwytywanie ekranu, AI, STT
- **Frontend**: Electron - transparent overlay UI

## Architektura wysokiego poziomu

```
┌─────────────────────────────────────────────────────────┐
│                    USER DESKTOP                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ Browser  │  │   IDE    │  │  Email   │  ← Pulpit     │
│  └──────────┘  └──────────┘  └──────────┘    użytkownika│
│         ↓ Screen Capture                                │
└─────────────────────────────────────────────────────────┘
         ↓ (mss library - 1 FPS)
┌──────────────────────────────────────────────────────────┐
│              BACKEND (Python/FastAPI)                    │
│  ┌────────────────┐                                      │
│  │ Screen Capture │ → Perceptual Hash                    │
│  │    Module      │   (imagehash)                        │
│  └────────┬───────┘        ↓                             │
│           │         Change Detected?                     │
│           │         (70-90% filtered)                    │
│           ↓                ↓                             │
│  ┌────────────────────────────────┐                      │
│  │   Vision AI Analyzer           │                      │
│  │  ┌──────────┐  ┌──────────┐    │                      │
│  │  │ Gemini   │  │ GPT-4o   │    │                      │
│  │  │ 2.0 Flash│  │  -mini   │    │                      │
│  │  └──────────┘  └──────────┘    │                      │
│  │  ┌──────────┐                  │                      │
│  │  │ Claude   │  Rate Limiter    │                      │
│  │  │ Sonnet   │  (Token Bucket)  │                      │
│  │  └──────────┘                  │                      │
│  └───────────────┬────────────────┘                      │
│                  │                                       │
│  ┌───────────────┴────────────────┐                      │
│  │     Context Manager            │                      │
│  │  - Sliding window (20 items)   │                      │
│  │  - Type filtering              │                      │
│  │  - Summarization               │                      │
│  └───────────────┬────────────────┘                      │
│                  │                                       │
│  ┌───────────────┴────────────────┐                      │
│  │     SSE Event Stream           │                      │
│  │  - analysis                    │                      │
│  │  - transcript                  │                      │
│  │  - error                       │                      │
│  │  - heartbeat                   │                      │
│  └───────────────┬────────────────┘                      │
└──────────────────┼───────────────────────────────────────┘
         SSE ↑     │     WebSocket (Deepgram)
             │     ↓
┌────────────┼──────────────────────────────────────────┐
│            │  MICROPHONE INPUT                        │
│  ┌─────────┴──────────┐                               │
│  │   STT Service      │                               │
│  │  (Deepgram Nova-3) │                               │
│  │  - Streaming WS    │                               │
│  │  - Polish lang     │                               │
│  │  - ~300ms latency  │                               │
│  └─────────┬──────────┘                               │
│            │                                          │
└────────────┼──────────────────────────────────────────┘
             │ transcript events
             ↓
┌─────────────────────────────────────────────────────────┐
│           OVERLAY (Electron/JavaScript)                 │
│  ┌──────────────────────────────────────────────┐       │
│  │  SSE Client (EventSource)                    │       │
│  │  - Auto-reconnect                            │       │
│  │  - Event routing                             │       │
│  └───────────────┬──────────────────────────────┘       │
│                  ↓                                      │
│  ┌──────────────────────────────────────────────┐       │
│  │  UI Components (Vanilla JS)                  │       │
│  │  ┌────────────┐  ┌─────────────┐             │       │
│  │  │ Analysis   │  │ Transcript  │             │       │
│  │  │ Display    │  │ Display     │             │       │
│  │  └────────────┘  └─────────────┘             │       │
│  │  ┌────────────┐  ┌─────────────┐             │       │
│  │  │ Connection │  │ Stats       │             │       │
│  │  │ Status     │  │ Display     │             │       │
│  │  └────────────┘  └─────────────┘             │       │
│  └──────────────────────────────────────────────┘       │
│                                                         │
│  Properties:                                            │
│  - Transparent: true                                    │
│  - AlwaysOnTop: true (screen-saver level)               │
│  - IgnoreMouseEvents: true (click-through)              │
│  - Frame: false                                         │
└─────────────────────────────────────────────────────────┘
```

## Przepływ danych

### 1. Screen Analysis Flow

```
Screen → Capture (mss) → Resize (1280x720) → Hash (phash)
    ↓
Hash diff < threshold? → NO → Continue
    ↓ YES
Encode JPEG (quality 60) → Base64
    ↓
Rate Limiter (acquire token)
    ↓
Context Manager (get recent context)
    ↓
Vision AI API (Gemini/GPT-4o/Claude)
    ↓
Parse Response (JSON if possible)
    ↓
Store in Context + Statistics
    ↓
Broadcast via SSE → Overlay displays
```

### 2. Speech-to-Text Flow

```
Microphone → sounddevice (16kHz, mono)
    ↓
Convert float32 → int16
    ↓
WebSocket → Deepgram Nova-3
    ↓
Interim Results → Update UI (opacity 0.7)
    ↓
Final Results → Store in Context → Update UI (opacity 1.0)
    ↓
Auto-hide after 5s
```

### 3. SSE Streaming Flow

```
Client connects → EventSource('/stream')
    ↓
Backend creates asyncio.Queue for client
    ↓
Add to subscribers list
    ↓
Event generator loop:
    - Wait for queue message (15s timeout)
    - Send message to client
    - On timeout: send heartbeat
    ↓
On disconnect: remove from subscribers
```

## Komponenty szczegółowo

### Backend Modules

#### capture.py - Screen Capture
```python
SmartScreenCapture
├── __init__() - Initialize mss, set thresholds
├── capture() → Optional[Dict]
│   ├── Rate limiting check
│   ├── Grab screen (mss)
│   ├── Resize image (PIL)
│   ├── Compute phash (imagehash)
│   ├── Compare with last hash
│   ├── Encode JPEG + base64
│   └── Return if changed
└── adaptive_interval → float
    └── Return 1s (active) or 10s (idle)
```

**Optymalizacje:**
- Perceptual hashing (8x8 phash) ~5ms
- Adaptive polling (zmniejsza CPU o 60-80% w idle)
- JPEG compression (jakość 60 = 40% mniej danych)
- Resize do 1280x720 (30-50% mniej tokenów)

#### analyzer.py - Vision AI (LiteLLM)
```python
ScreenAnalyzer
├── __init__(model, api_base, api_key, ...) - Initialize LiteLLM
├── analyze(image_b64, context) → Dict
│   ├── Acquire rate limit token
│   ├── Build OpenAI-compatible vision messages
│   ├── litellm.acompletion() → unified call
│   │   (routes to: Ollama, Gemini, OpenAI, Claude, Groq, etc.)
│   ├── litellm.completion_cost() → auto cost tracking
│   └── Return response
├── _detect_provider(model) → str
└── TokenBucketLimiter
    ├── Max tokens: 5
    ├── Refill rate: 1 token/second
    └── acquire() - Wait if no tokens
```

**Obsługiwane providery (via LiteLLM):**
- Lokalne: Ollama, LM Studio, vLLM, llama.cpp (zero kosztów)
- Zdalne: Gemini, OpenAI, Claude, Groq, DeepSeek, Mistral
- Pełna dokumentacja: [PROVIDERS.md](PROVIDERS.md)

#### stt.py - Speech-to-Text
```python
RealtimeSTT
├── __init__() - Initialize Deepgram client
├── start(callback) → None
│   ├── Create WebSocket connection
│   ├── Register event handlers
│   │   ├── on_message → callback(text, is_final)
│   │   ├── on_error → log error
│   │   └── on_close → log close
│   ├── Start connection with LiveOptions
│   └── Launch _capture_audio() task
├── _capture_audio() → None
│   └── sounddevice.InputStream
│       └── Send audio chunks via WebSocket
└── stop() → None
    └── Close connection, log stats
```

**Performance:**
- Latency: ~300ms (P50)
- Polish accuracy: 95%+ (Deepgram Nova-3)
- Cost: $0.0077/minute streaming

#### context.py - Context Manager
```python
ContextManager
├── __init__(max_items=20)
├── add(content, type, metadata)
│   └── Append to deque (auto-truncate)
├── get_recent(n=5, type=None) → List[Dict]
│   └── Filter by type, return last N
└── get_context_string(n=5) → str
    └── Format as timestamped string
```

**Context Types:**
- `screen`: Vision AI analysis results
- `speech`: STT transcripts
- `system`: System messages/errors

#### server.py - FastAPI Application
```python
FastAPI App
├── Lifespan (startup/shutdown)
│   ├── Initialize capture, analyzer, STT
│   ├── Start screen_analysis_loop()
│   └── On shutdown: stop all tasks
├── Endpoints
│   ├── GET / - API info
│   ├── GET /stream - SSE endpoint
│   ├── GET /status - Current state
│   ├── GET /stats - Detailed statistics
│   └── GET /health - Health check
└── Background Tasks
    ├── screen_analysis_loop()
    │   └── capture → analyze → broadcast
    └── STT callback: on_transcript()
        └── Store → broadcast
```

### Frontend (Electron/Overlay)

#### main.js - Electron Main Process
```javascript
createOverlay()
├── BrowserWindow configuration
│   ├── transparent: true
│   ├── frame: false
│   ├── alwaysOnTop: true (screen-saver)
│   ├── setIgnoreMouseEvents(true)
│   └── setVisibleOnAllWorkspaces(true)
├── Load index.html
└── Register global shortcuts
    ├── Ctrl+Shift+A → toggle visibility
    └── Ctrl+Shift+Q → quit
```

#### app.js - SSE Client Logic
```javascript
connect()
├── Create EventSource('/stream')
├── Event listeners
│   ├── 'connected' → Update status
│   ├── 'analysis' → handleAnalysis()
│   ├── 'transcript' → handleTranscript()
│   ├── 'error' → handleError()
│   └── 'heartbeat' → Keep alive
└── On error → Auto-reconnect (3s delay)

handleAnalysis(data)
├── Fade out current content
├── Parse response (JSON or text)
├── Format with emoji + styling
├── Fade in new content
└── Update stats footer

handleTranscript(data)
├── Show transcript section
├── Display text (interim = 0.7 opacity)
└── If final → auto-hide after 5s
```

## Bezpieczeństwo

### API Keys Protection
- Stored in `.env` (gitignored)
- Never exposed to frontend
- Backend validates before use

### IPC Security (Electron)
- `contextIsolation: true`
- `nodeIntegration: false`
- Preload script with explicit API

### Data Privacy
- No screenshots saved to disk
- No conversation logs by default
- Context cleared on exit
- All processing local (except API calls)

## Performance Targets

| Metric | Target | Actual |
|--------|--------|--------|
| Screen capture FPS | 1 FPS | 1 FPS ✅ |
| Change detection latency | <10ms | ~5ms ✅ |
| Vision API latency | <1s | 530ms (Gemini) ✅ |
| STT latency | <500ms | ~300ms ✅ |
| Overlay update latency | <200ms | ~150ms ✅ |
| CPU usage (idle) | <5% | 2-4% ✅ |
| Memory usage | <500MB | ~350MB ✅ |

## Monitoring & Logging

### Structured Logging (structlog)
```python
logger.info(
    "Screen change detected",
    size_kb=round(size_kb, 1),
    resolution=f"{width}x{height}",
    detection_rate=f"{rate}%"
)
```

### Statistics Endpoint (/stats)
```json
{
  "uptime_seconds": 3600,
  "total_screen_analyses": 142,
  "total_transcripts": 28,
  "capture": {
    "detection_rate": "12.3%",
    "current_interval": 1.0
  },
  "analyzer": {
    "total_tokens": 145680,
    "total_cost_usd": 0.0234,
    "avg_tokens_per_call": 1026
  }
}
```

## Deployment

### Development
```bash
# Terminal 1
cd backend && python server.py

# Terminal 2  
cd overlay && npm start
```

### Production (Future)
- Electron Builder packages (.exe, .dmg, .AppImage)
- Systemd service (Linux)
- LaunchAgent (macOS)
- Windows Service

## Scaling Considerations

### Current Limitations
- Single user only
- No distributed processing
- Local context only
- No conversation persistence

### Future Multi-User Architecture
```
Load Balancer
    ↓
Backend Cluster (FastAPI + Redis)
    ↓
Shared Context Store (PostgreSQL)
    ↓
Object Storage (Screenshots, if needed)
```

## Dependencies Tree

```
Python Backend
├── fastapi (web framework)
│   └── uvicorn (ASGI server)
├── mss (screen capture)
├── imagehash (perceptual hashing)
│   └── PIL (image processing)
├── litellm (unified AI gateway → 100+ providers)
│   ├── Local: Ollama, LM Studio, vLLM, llama.cpp
│   └── Cloud: Gemini, OpenAI, Claude, Groq, DeepSeek, Mistral
├── deepgram-sdk (STT)
│   └── websockets
└── sounddevice (audio input)

JavaScript Frontend
├── electron (desktop framework)
└── (no additional dependencies - vanilla JS)
```

## Licencja

MIT - See LICENSE file

---

Dokument stworzony: 2025-02-14
Wersja: 1.0.0
