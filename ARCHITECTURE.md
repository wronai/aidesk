# AI Desktop Assistant - Dokumentacja Architektury

## Przegląd systemu

AI Desktop Assistant to real-time desktop monitoring system składający się z dwóch głównych komponentów:
- **Backend**: Python/FastAPI - przechwytywanie ekranu, AI, STT, Window Awareness, Shell Agent
- **Frontend**: Electron - transparent overlay UI z kontekstem okna i sugerowanymi akcjami

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
│                                                          │
│  ┌────────────────┐  ┌────────────────┐                  │
│  │ Window Manager │→ │ Screen Capture │                  │
│  │ (xdotool/xprop)│  │ (mss/grim)     │                  │
│  │ title, class,  │  │ ROI or full    │                  │
│  │ PID, geometry  │  │ screen         │                  │
│  │ git context    │  └────────┬───────┘                  │
│  └───────┬────────┘           │                          │
│          │ AppCategory        │ Perceptual Hash          │
│          ↓                    ↓                          │
│  ┌────────────────┐  Change Detected?                    │
│  │ Profile Manager│  (70-90% filtered)                   │
│  │ per-app prompt │           │                          │
│  │ + focus hints  │           ↓                          │
│  └───────┬────────┘                                      │
│          │ prompt addon                                  │
│          ↓                                               │
│  ┌────────────────────────────────┐                      │
│  │   OCR Pre-Processor            │                      │
│  │  ┌──────────┐  ┌──────────┐    │                      │
│  │  │PaddleOCR │  │ EasyOCR  │    │  Hot-swappable       │
│  │  └──────────┘  └──────────┘    │  at runtime          │
│  │  ┌──────────┐                  │                      │
│  │  │Tesseract │  OCR Manager     │                      │
│  │  └──────────┘  (benchmark)     │                      │
│  └───────────────┬────────────────┘                      │
│                  ↓ extracted text                         │
│  ┌────────────────────────────────┐                      │
│  │   Vision AI Analyzer           │                      │
│  │  ┌──────────┐  ┌──────────┐    │  Context enriched    │
│  │  │ Gemini   │  │ GPT-4o   │    │  with window info    │
│  │  │ 2.0 Flash│  │  -mini   │    │  + per-app prompt    │
│  │  └──────────┘  └──────────┘    │                      │
│  │  ┌──────────┐                  │                      │
│  │  │ Claude   │  Rate Limiter    │                      │
│  │  │ Sonnet   │  (Token Bucket)  │                      │
│  │  └──────────┘                  │                      │
│  │  Modes: vision_only | ocr_only │                      │
│  │         hybrid | ocr+vision    │                      │
│  └───────────────┬────────────────┘                      │
│                  ↓                                       │
│  ┌────────────────────────────────┐                      │
│  │   Shell Agent                  │                      │
│  │  - Pattern matching on text    │                      │
│  │  - Suggest safe commands       │                      │
│  │  - Whitelist + block list      │                      │
│  │  - Execute with approval       │                      │
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
│  │  - analysis (+ window + agent) │                      │
│  │  - window (active window info) │                      │
│  │  - agent_actions (suggestions) │                      │
│  │  - agent_result (exec output)  │                      │
│  │  - transcript                  │                      │
│  │  - ocr_benchmark               │                      │
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
│  │  │ OCR Engine │  │ Mode        │             │       │
│  │  │ Selector   │  │ Selector    │             │       │
│  │  └────────────┘  └─────────────┘             │       │
│  │  ┌────────────┐  ┌─────────────┐             │       │
│  │  │ Benchmark  │  │ Stats       │             │       │
│  │  │ Display    │  │ Display     │             │       │
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

### 1. Screen Analysis Flow (v2.0)

```
Step 1: Window Detection (xdotool/xprop)
    → title, WM_CLASS, PID, geometry, category
    → Git context (branch, status) for IDE/Terminal
    ↓
Step 2: Screen Capture (mss/grim)
    → Full screen or ROI (active window region)
    → Resize (1280x720) → Hash (phash)
    ↓
Hash diff < threshold? → NO → Sleep (adaptive)
    ↓ YES
Encode JPEG (quality 60) → Base64
    ↓
Step 3: Build Context
    → Window context string (app, CWD, git branch)
    → Recent context (sliding window)
    ↓
Step 4: Per-App Profile
    → Get prompt addon for app category (IDE, Terminal, Browser...)
    → Merge into analysis context
    ↓
Step 5: OCR + Analysis
    → OCR Pre-Processing (PaddleOCR/EasyOCR/Tesseract)
    → Mode Selection:
      ┌── vision_only:     image → VLM                   ┐
      │   ocr_only:        OCR text only (no LLM call)   │
      │   hybrid:          OCR text → LLM text prompt    │
      └── ocr_plus_vision: OCR text + image → VLM        ┘
    ↓
Step 6: Shell Agent
    → Pattern match on analysis + OCR text
    → Suggest safe commands (git status, pip install, etc.)
    → Broadcast agent_actions to overlay
    ↓
Step 7: Broadcast via SSE
    → analysis (text + window + agent_actions + OCR meta)
    → window (active window info)
    → agent_actions (suggested commands)
    → Overlay displays all components
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
├── __init__() - Initialize mss/grim, set thresholds
├── capture(monitor_index, roi) → Optional[Dict]
│   ├── Rate limiting check
│   ├── Grab screen (mss or grim, with optional ROI)
│   ├── Resize image (PIL)
│   ├── Compute phash (imagehash)
│   ├── Compare with last hash
│   ├── Encode JPEG + base64
│   └── Return if changed
├── capture_roi_image(roi) → Optional[str]
│   └── Capture specific region, return base64 JPEG
├── get_monitors() → List[Dict]
│   └── List available monitors (mss or wlr-randr)
└── adaptive_interval → float
    └── Return 1s (active) or 10s (idle)
```

**Optymalizacje:**
- Backend auto-detection: X11 (mss) or Wayland (grim)
- ROI capture: only active window region (CAPTURE_MODE=window)
- Perceptual hashing (8x8 phash) ~5ms
- Adaptive polling (zmniejsza CPU o 60-80% w idle)
- JPEG compression (jakość 60 = 40% mniej danych)
- Resize do 1280x720 (30-50% mniej tokenów)

#### ocr_engines.py - OCR Engine Abstraction
```python
OCRManager
├── __init__(default_engine, languages, use_gpu, enabled)
│   └── _register_available_engines() - Auto-detect installed OCR libs
├── extract(image_b64) → OCRResult
│   └── Delegates to active engine
├── set_engine(name) → bool - Hot-swap engine at runtime
├── benchmark(image_b64) → Dict
│   └── Run ALL engines on same image, compare results
└── get_available_engines() → List[Dict]

BaseOCREngine (ABC)
├── PaddleOCREngine  - Najszybszy (~12.7 FPS GPU, ~500MB VRAM)
├── EasyOCREngine    - Najdokładniejszy (CER 0.09, ~56 FPS)
└── TesseractEngine  - Najlżejszy (~10MB, 0.3-1s/obraz)

OCRResult
├── text: str          - Wyekstrahowany tekst
├── boxes: List[Dict]  - Bounding boxes z pozycjami
├── confidence: float  - Średnia pewność (0-1)
├── engine: str        - Użyty silnik
├── latency_ms: float  - Czas przetwarzania
└── to_llm_context()   - Formatuj jako kontekst dla LLM
```

**Porównanie silników OCR:**
| Aspekt | PaddleOCR | EasyOCR | Tesseract |
|--------|-----------|---------|----------|
| Szybkość | ~12.7 FPS (GPU) | ~56 FPS | 0.3-1s/obraz |
| Dokładność | 96-99% | CER 0.09 | 95%+ (czysty tekst) |
| VRAM | ~500MB | ~1GB | ~10MB |
| Języki | 80+ | 80+ | 100+ |
| Najlepszy do | UI/screenshoty | Mieszany tekst | Prosty/czysty tekst |

#### analyzer.py - Vision AI (LiteLLM) + Hybrid OCR
```python
ScreenAnalyzer
├── __init__(model, api_base, api_key, ocr_manager, analysis_mode, ...)
├── analyze(image_b64, context) → Dict
│   ├── Acquire rate limit token
│   ├── [hybrid/ocr modes] Run OCR pre-processing
│   ├── Build messages based on mode:
│   │   ├── vision_only: image → VLM
│   │   ├── ocr_only: OCR text only (no LLM)
│   │   ├── hybrid: OCR text → LLM text prompt (5-10x faster)
│   │   └── ocr_plus_vision: OCR text + image → VLM
│   ├── litellm.acompletion() → unified call
│   ├── litellm.completion_cost() → auto cost tracking
│   └── Return response + OCR metadata
├── set_mode(mode) → bool - Switch mode at runtime
├── _run_ocr(image_b64) → OCRResult
├── _detect_provider(model) → str
└── TokenBucketLimiter
    ├── Max tokens: 5
    ├── Refill rate: 1 token/second
    └── acquire() - Wait if no tokens
```

**Tryby analizy:**
- `vision_only` — Oryginalne zachowanie: obraz → VLM
- `ocr_only` — Najszybszy: tylko OCR, zero kosztów API LLM
- `hybrid` — **Rekomendowany**: OCR tekst → LLM tekstowy prompt (5-10x szybszy)
- `ocr_plus_vision` — Najdokładniejszy: OCR tekst + obraz → VLM

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

#### window_aware.py - Window Awareness
```python
WindowManager
├── __init__(enable_git, git_timeout, cache_ttl)
│   └── Auto-detect tools: xdotool, xprop, xrandr, wmctrl
├── get_active_window() → WindowInfo
│   ├── xdotool getactivewindow → window ID
│   ├── xdotool getwindowname → title
│   ├── xdotool getwindowgeometry → x, y, width, height
│   ├── xdotool getwindowpid → PID
│   ├── xprop WM_CLASS → wm_class, wm_class_name
│   ├── _classify_app() → AppCategory (IDE, Terminal, Browser...)
│   ├── /proc/{pid}/cwd → working directory
│   └── git rev-parse, git status → branch, repo, status
├── get_monitors() → List[MonitorInfo] (via xrandr)
├── get_window_roi(info) → Dict (left, top, width, height)
└── get_stats() → Dict

AppCategory (Enum)
├── IDE, TERMINAL, BROWSER, EMAIL, CHAT
└── OFFICE, MEDIA, FILE_MANAGER, SYSTEM, UNKNOWN

WindowInfo (dataclass)
├── window_id, title, wm_class, wm_class_name, pid
├── x, y, width, height, monitor_index
├── category: AppCategory
├── git_repo, git_branch, git_status, cwd
├── to_dict() → Dict
└── to_context_string() → str (for LLM prompt injection)
```

**Obsługiwane aplikacje (50+ reguł):**
- IDE: VS Code, JetBrains, Sublime, Zed, Cursor, Windsurf...
- Terminal: gnome-terminal, alacritty, kitty, wezterm...
- Browser: Firefox, Chrome, Brave, Vivaldi...
- Email, Chat, Office, Media, File Manager, System

#### app_profiles.py - Per-App Analysis Profiles
```python
ProfileManager
├── __init__() - Load 7 built-in profiles
├── get_profile(category) → AppProfile
├── get_prompt_addon(category) → str
│   └── Category-specific system prompt addon for LLM
├── get_focus_keywords(category) → List[str]
├── get_priority_boost(category) → float
├── match_action_patterns(category, text) → List[Dict]
└── get_all_profiles() → List[Dict]

AppProfile (dataclass)
├── category: AppCategory
├── system_prompt_addon: str  (e.g. "Wykrywaj błędy składniowe...")
├── focus_keywords: List[str] (change detection hints)
├── action_patterns: Dict     (regex → action description)
└── priority_boost: float     (sensitivity multiplier: IDE=1.5, Media=0.3)
```

**Profile examples:**
- **IDE** (boost 1.5): Detect syntax errors, TODO/FIXME, imports, anti-patterns
- **Terminal** (boost 1.3): Explain commands, suggest fixes for errors
- **Browser** (boost 0.8): Summarize pages, detect StackOverflow Q&A, GitHub PRs

#### shell_agent.py - Shell Agent
```python
ShellAgent
├── __init__(auto_execute_safe, max_output, timeout)
├── suggest_actions(text, category, cwd) → List[AgentAction]
│   └── Pattern-match text against ACTION_RULES
├── execute_action(action_id, cwd) → AgentAction
│   ├── Safety check (_is_blocked)
│   ├── Risk check (SAFE auto, MEDIUM needs approval)
│   └── subprocess.run() with timeout + output capture
├── execute_safe(command, cwd) → AgentAction
│   └── Whitelist-validated direct execution
├── approve_action(action_id) → bool
├── get_pending_actions() → List[Dict]
├── get_history(n) → List[Dict]
└── get_stats() → Dict

ActionRisk (Enum)
├── SAFE       - Read-only (auto-execute: git status, ls, df -h)
├── LOW        - Minor side effects (clipboard)
├── MEDIUM     - File/git changes (needs user approval)
├── HIGH       - System changes (manual only)
└── DANGEROUS  - Never execute (rm -rf /, fork bomb, etc.)
```

**Action Rules (12+ patterns):**
- `git push rejected` → `git pull --rebase`
- `ModuleNotFoundError: {module}` → `pip install {module}`
- `Cannot find module '{pkg}'` → `npm install {pkg}`
- `No space left on device` → `df -h && du -sh /tmp/*`
- `Connection refused` → `ss -tlnp | grep {port}`

**Safety:**
- 15+ blocked patterns (rm -rf /, fork bomb, dd, curl|bash...)
- 30+ safe commands whitelist (git status, ls, df, ps...)
- 10+ approval-required commands (git push, pip install, make...)

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

#### server.py - FastAPI Application (v2.0)
```python
FastAPI App
├── Lifespan (startup/shutdown)
│   ├── Initialize capture, OCR manager, analyzer
│   ├── Initialize window manager, profile manager, shell agent
│   ├── Start screen_analysis_loop()
│   └── On shutdown: stop all tasks
├── Core Endpoints
│   ├── GET  / - API info (v2.0)
│   ├── GET  /stream - SSE endpoint
│   ├── GET  /status - Current state
│   ├── GET  /stats - Detailed statistics (all components)
│   ├── GET  /health - Health check (6 components)
├── OCR Endpoints
│   ├── GET  /ocr/engines - List available OCR engines
│   ├── POST /ocr/engine/{name} - Switch OCR engine
│   ├── POST /ocr/benchmark - Run A/B benchmark
│   ├── GET  /ocr/stats - OCR statistics
│   ├── GET  /mode - Get analysis mode
│   └── POST /mode/{name} - Switch analysis mode
├── Window Endpoints
│   ├── GET  /window - Active window info (live)
│   ├── GET  /window/latest - Cached window info
│   ├── GET  /window/stats - Window manager stats
│   └── GET  /monitors - Connected monitors (xrandr)
├── Profile Endpoints
│   └── GET  /profiles - All per-app profiles
├── Agent Endpoints
│   ├── GET  /agent/actions - Pending actions
│   ├── POST /agent/approve/{id} - Approve action
│   ├── POST /agent/execute/{id} - Execute action
│   ├── POST /agent/run - Run safe command directly
│   └── GET  /agent/history - Execution history
└── Background Tasks
    └── screen_analysis_loop()
        ├── Step 1: Detect active window
        ├── Step 2: Capture screen (full or ROI)
        ├── Step 3: Build context + window awareness
        ├── Step 4: Get per-app prompt addon
        ├── Step 5: Analyze (OCR + LLM)
        ├── Step 6: Shell agent suggestions
        └── Step 7: Broadcast to overlay
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
│   ├── 'analysis' → handleAnalysis() (+ window + agent_actions)
│   ├── 'transcript' → handleTranscript()
│   ├── 'window' → handleWindowUpdate()
│   ├── 'agent_actions' → handleAgentActions()
│   ├── 'agent_result' → handleAgentResult()
│   ├── 'error' → handleError()
│   ├── 'ocr_engine_changed' → Update selector
│   ├── 'mode_changed' → Update selector
│   ├── 'ocr_benchmark' → Display results
│   └── 'heartbeat' → Keep alive
└── On error → Auto-reconnect (3s delay)

handleAnalysis(data)
├── Fade out current content
├── Parse response (JSON or text)
├── Format with emoji + styling
├── Show OCR info bar (engine, latency, confidence)
├── Update window context bar (if window data present)
├── Update agent actions (if suggestions present)
├── Fade in new content
└── Update stats footer (provider • mode • category • tokens)

handleWindowUpdate(data)
├── Show window context bar
├── Display app emoji + name + category badge
└── Show git branch + CWD detail line

handleAgentActions(data)
├── Show agent actions section
├── Render action cards (description, command, risk level)
├── Approve + Execute buttons
├── Copy command button
└── Auto-hide after 30s

handleAgentResult(data)
├── Show execution result (exit code, output)
└── Auto-hide after 15s

approveAndExecute(actionId)
├── POST /agent/approve/{id}
└── POST /agent/execute/{id} → handleAgentResult()

OCR Controls
├── switchMode(mode) → POST /mode/{mode}
├── switchOCREngine(engine) → POST /ocr/engine/{engine}
├── runBenchmark() → POST /ocr/benchmark
│   └── Display comparative results (15s auto-hide)
└── loadOCRSettings() → Sync UI with backend state

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
| OCR latency (PaddleOCR) | <100ms | ~50ms ✅ |
| OCR latency (EasyOCR) | <200ms | ~100ms ✅ |
| OCR latency (Tesseract) | <1s | ~500ms ✅ |
| Vision API latency | <1s | 530ms (Gemini) ✅ |
| Hybrid mode latency | <600ms | ~200ms (OCR→LLM) ✅ |
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
├── mss (screen capture - X11)
├── grim (screen capture - Wayland, optional)
├── imagehash (perceptual hashing)
│   └── PIL (image processing)
├── OCR Engines (hot-swappable)
│   ├── paddleocr + paddlepaddle (fastest, best for UI)
│   ├── easyocr (highest accuracy, mixed text)
│   └── pytesseract (lightest, clean text fallback)
├── litellm (unified AI gateway → 100+ providers)
│   ├── Local: Ollama, LM Studio, vLLM, llama.cpp
│   └── Cloud: Gemini, OpenAI, Claude, Groq, DeepSeek, Mistral
├── deepgram-sdk (STT)
│   └── websockets
├── sounddevice (audio input)
└── System tools (window awareness)
    ├── xdotool (active window detection)
    ├── xprop (WM_CLASS detection)
    ├── xrandr (monitor detection)
    └── git (repo context)

JavaScript Frontend
├── electron (desktop framework)
└── (no additional dependencies - vanilla JS)
```

## Licencja

MIT - See LICENSE file

---

Dokument stworzony: 2025-02-14
Wersja: 2.0.0 (Window Awareness + Shell Agent)
