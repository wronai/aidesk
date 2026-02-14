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

### 1. Screen Analysis Pipeline (v2.0.3 — SOLID / CQRS / Event Sourcing)

The analysis loop is implemented as a **composable pipeline** (`pipeline.py`).
Each step is an independent class satisfying the `PipelineStep` Protocol.
All steps emit typed events to the `EventBus`, persisted to an SQLite event store.

```
PipelineOrchestrator.run(PipelineContext)
  │
  ├─ ScanWindowsStep         → scan all visible windows (wmctrl/xdotool)
  │   └─ emits: pipeline.windows_scanned {total: N}
  │
  ├─ DetectActiveWindowStep  → detect active window, ROI, git context
  │   └─ emits: pipeline.windows_scanned {active: WindowInfo}
  │
  ├─ CaptureScreenStep       → capture fullscreen or ROI (mss/grim)
  │   └─ emits: pipeline.screen_captured {size_kb, has_change}
  │   └─ GATE: skips rest if no change detected
  │
  ├─ CropWindowsStep         → crop each visible app from fullscreen
  │   └─ emits: pipeline.screen_organized {total, summary, categories}
  │
  ├─ MultiMonitorStep *      → per-monitor activity scoring + LLM description
  │   └─ GATE: skips if single monitor
  │
  ├─ BuildContextStep        → window info + profiles + TTS transcript → rich prompt
  │   └─ emits: pipeline.context_built {context_length}
  │
  ├─ AnalyzeStep              → OCR + LLM (hybrid/vision modes)
  │   └─ emits: pipeline.analysis_completed {tokens, cost, provider, mode}
  │
  ├─ OCRPostProcessStep *    → enhance OCR text (char fixes, spell check, word merge)
  │   └─ GATE: skips if no OCR text in analysis result
  │
  ├─ SuggestActionsStep       → pattern-match text → safe commands
  │   └─ emits: pipeline.agent_suggested {count}
  │
  ├─ ActionTemplateStep *    → learned action patterns with confidence scoring
  │   └─ GATE: skips if no analysis result or active window
  │
  ├─ SemanticMemoryStep *    → store context + recall relevant past memories
  │   └─ GATE: skips if no analysis result
  │
  ├─ PredictiveStep *        → learn window transitions + trigger pre-fetch
  │   └─ GATE: skips if no active window
  │
  └─ BuildBroadcastStep      → assemble SSE payload from context
      └─ emits: pipeline.broadcast_sent {keys}

  (* = Tier 1 steps added in v2.1)
```

**SOLID compliance:**
- **S**: Each step has exactly one responsibility
- **O**: Add steps via `pipeline.add_step()` / `insert_after()` without modifying existing code
- **L**: Any class satisfying `PipelineStep` Protocol is valid (duck typing)
- **I**: `protocols.py` defines minimal contracts: `ScreenCapture`, `OCRExtractor`, `WindowDetector`, etc.
- **D**: Pipeline receives injected components, never imports concrete classes

**CQRS:**
- Commands (`cmd.*`) — state-changing: switch OCR engine, execute action, run benchmark
- Queries (`query.*`) — read-only: health, stats, window info
- Events (`pipeline.*`) — facts: captured, analyzed, suggested

**Event Sourcing:**
- All pipeline events persisted to `logs/events.db` (SQLite)
- Query by type, source, correlation_id, time range: `GET /events?type=...`
- Correlation IDs link events from the same pipeline run
- Full audit trail for debugging and replay

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

#### multi_monitor.py - Multi-Monitor Intelligence (Tier 1)
```python
MonitorAwareCapture
├── detect_active_monitor(monitors, active_window) → int
│   ├── 1. Active window monitor_index
│   ├── 2. Window center → monitor lookup
│   ├── 3. Mouse cursor monitor (xdotool)
│   └── 4. Primary monitor fallback
├── build_snapshot(monitors, windows, active_window) → MultiMonitorSnapshot
│   ├── Per-monitor activity scoring (window count, change score)
│   ├── Priority ranking (active > primary > most windows)
│   └── Human-readable description ("left: ide | right: terminal")
├── get_monitors_to_analyze(snapshot) → List[int]
│   └── active_only=true → only active monitor (60-80% cost savings)
├── get_capture_roi_for_monitor(monitor) → Dict
└── get_stats() → Dict
```

#### semantic_memory.py - Semantic Memory with Embeddings (Tier 1)
```python
SemanticMemory
├── __init__(model, db_path, max_memories, recall_top_k, threshold)
│   ├── Optional sentence-transformers (all-MiniLM-L6-v2, 384 dim)
│   ├── SQLite vector store with in-memory cache
│   └── Graceful fallback to keyword search if no model
├── add_memory(content, type, metadata) → memory_id
│   ├── Embed text → normalize → store to SQLite + cache
│   ├── Content-hash deduplication (5s window)
│   └── Auto-prune oldest when over max_memories
├── recall_relevant(query, k, type, since) → List[MemoryItem]
│   ├── Semantic: cosine similarity on normalized embeddings
│   └── Keyword fallback: Jaccard word overlap scoring
├── recall_recent(n, type) → List[MemoryItem]
├── compress_old_context(before_timestamp) → int
│   └── Group by hour → summarize → replace originals
├── get_context_string(query, n, max_length) → str
└── get_stats() → Dict
```

#### action_templates.py - App-Specific Action Templates (Tier 1)
```python
AppActionLibrary
├── __init__(db_path, auto_approve_default) → loads seed + DB templates
├── suggest_with_confidence(text, app_category) → List[ScoredAction]
│   ├── Regex pattern matching per app category
│   └── Confidence from approval/rejection history
├── learn_from_approval(template_id) → may promote to auto-execute
├── learn_from_rejection(template_id) → may revoke auto-execute
├── learn_from_execution(template_id)
├── add_template() / remove_template()
├── export_templates() → JSON (community sharing)
├── import_templates(json) → int
└── get_stats() → Dict

ActionTemplate (dataclass)
├── trigger_pattern: str (regex)
├── command_template: str (with {1}, {2} placeholders)
├── confidence: float (0.0-1.0 from approval history)
├── should_auto_execute: bool (promoted after N approvals, 0 rejections)
└── 10 seed templates: Python, Node, Git, Docker, Rust, disk, ports
```

#### ocr_post_process.py - OCR Post-Processing Pipeline (Tier 1)
```python
OCREnhancer
├── enhance(text, hint_type) → PostProcessResult
│   ├── 1. Detect text type (code | terminal | prose | mixed)
│   ├── 2. Apply type-specific fixes (CODE_OCR_FIXES, TERMINAL_OCR_FIXES)
│   ├── 3. Fix character confusions (O↔0, l↔1 context-aware)
│   ├── 4. Merge broken words across line breaks
│   └── 5. Optional spell check (symspellpy, preserves code keywords)
└── get_stats() → Dict

Text type detection heuristics:
├── CODE: def/class/import/function patterns (13 indicators)
├── TERMINAL: $prompt, ERROR/WARNING, Traceback (9 indicators)
└── PROSE: long sentences with punctuation

90+ programming keywords preserved from spell correction
```

#### predictive_engine.py - Predictive Pre-fetching (Tier 1)
```python
PredictiveAnalyzer
├── observe_window_change(category, window_id)
│   └── Update first-order Markov chain on app transitions
├── predict_next_action(current_category) → PredictionResult
│   └── Most likely next category if P > confidence_threshold
├── maybe_prefetch(prediction) → trigger background OCR/capture
├── get_prefetched(window_id) → PrefetchCache (if valid TTL)
├── get_transition_matrix() → Dict[str, Dict[str, float]]
├── get_top_patterns(n) → List[Dict]
└── get_stats() → Dict (accuracy, hit rate, latency saved)

PrefetchCache
├── window_id, app_category
├── ocr_text, image_b64 (pre-computed)
├── ttl: float (default 10s)
└── is_valid: bool
```

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

#### skills/ - Skill System (Selection → Intent → Action)
```python
BaseSkill (ABC)
├── name: str                    # Unique skill identifier
├── category: SkillCategory      # COMMAND, CLIPBOARD, TRANSLATION, etc.
├── icon: str                    # Emoji icon
├── priority: int                # Higher = checked first
├── detect(text, ctx) → float    # Confidence 0.0–1.0
├── get_options(text, ctx) → List[SkillOption]
└── execute(text, option_id, ctx) → SkillResult

SkillContext (dataclass)
├── text, window_category, window_title, window_class
├── cwd, locale, latest_transcript, timestamp
├── clipboard_top: str           # Most recent clipboard content
└── clipboard_items: List[Dict]  # Recent clipboard queue (up to 5)

SkillRouter
├── analyze(text, ctx) → List[SkillMatch]  # Ranked by confidence
├── execute(skill, text, option_id, ctx) → SkillResult
├── register_skill(cls) / get_skill_names()
└── Built-in skills (sorted by priority):
    ├── VoiceCommandSkill    (95) — voice commands via STT
    ├── ShellCommandSkill    (90) — detect & run shell commands
    ├── ErrorFixerSkill      (85) — error patterns → fix suggestions
    ├── ClipboardRelationSkill (80) — selection ↔ clipboard intent
    ├── URLHandlerSkill      (70) — URL detection & actions
    ├── TranslationSkill     (60) — language detection & translation
    └── TTSSkill             (40) — text-to-speech
```

**API Endpoints:**
- `POST /analyze-selection` — analyze text with all skills, return ranked matches
- `POST /skill/execute` — execute a specific skill option
- `GET /skills` — list registered skills

#### skills/clipboard_relation.py - Clipboard-Aware Intent Detection
```python
ClipboardRelationSkill (priority 80)
├── Analyzes the *pair* (selected_text, clipboard_content)
├── 16 intent detectors with confidence scoring
├── Custom option builders per intent type
└── Execution handlers: copy_both, show_diff, replace_clipboard,
    translate_pair, install_package, open_error_file, save_to_file, search_pair
```

**Intent Catalog (16 detectors):**

| # | Intent | Score | Selection | Clipboard | Action |
|---|--------|-------|-----------|-----------|--------|
| 1 | `already_copied` | 0.95 | Any text | Same text (>90% similar) | Replace clipboard |
| 2 | `error_file_match` | 0.92 | `app.py` | Traceback mentioning `app.py` | Open file at error line |
| 3 | `complement_cmd` | 0.88 | `flask` | `ModuleNotFoundError: flask` | `pip install flask` |
| 4 | `stack_trace_symbol` | 0.86 | `handle_request` | Stack trace with `handle_request` | Search symbol + error |
| 5 | `ip_conn_error` | 0.85 | `192.168.1.100` | `connection refused` | Diagnose connection |
| 6 | `env_var_missing` | 0.84 | `API_KEY` | `API_KEY is not set` | How to set env var |
| 7 | `docker_error` | 0.83 | Container ID | Docker error for that container | Docker troubleshoot |
| 8 | `git_diff_ref` | 0.82 | `abc1234` | `diff --git ...` | Copy ref + diff |
| 9 | `cross_language` | 0.78 | Polish text | English text | Translation pair |
| 10 | `env_var_match` | 0.76 | `DB_URL=...` | Config referencing `DB_URL` | Copy var + context |
| 11 | `config_key_match` | 0.73 | `server.port` | Config block with `server.port` | Copy key + config |
| 12 | `json_pair` | 0.72 | `{"a": 1}` | `{"b": 2}` | Compare/diff JSON |
| 13 | `url_pair` | 0.70 | URL (github.com) | URL (github.com) | Compare pages |
| 14 | `git_compare` | 0.70 | `main` | `develop` | Compare branches |
| 15 | `regex_test` | 0.68 | `^\d{3}-\d{4}$` | Test data | Test regex online |
| 16 | `save_to_path` | 0.65 | `/tmp/out.txt` | Long content | Save clipboard to file |

Additional low-priority intents (always available as fallback):
- `code_similarity` (0.6–0.8) — both are similar code fragments → show diff
- `diff_fragments` (0.4–0.7) — both are similar text → show diff
- `ip_pair` (0.55) — both are IP/host addresses
- `docker_context` (0.58) — both reference Docker

**Signal extraction:**
- Language detection: 5 languages (en/pl/de/fr/es) + Cyrillic/CJK script detection
- Text similarity: `SequenceMatcher` ratio (0.0–1.0)
- Domain extraction: URL parsing for same-domain detection
- Pattern matching: 12 compiled regexes (URL, path, package, error, code, JSON, git, IP, env, docker, config, stack frame)

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
├── Process & Screen Endpoints
│   ├── GET  /processes - All windows grouped by category
│   ├── GET  /windows/all - Raw window list with geometry
│   ├── GET  /screen/organized - Per-app crops + categories
│   └── GET  /screen/stats - Scanner & cropper stats
├── CQRS / Event Sourcing Endpoints
│   ├── GET  /events - Query event store (type, source, correlation_id, since)
│   ├── GET  /events/stats - Event bus statistics
│   ├── GET  /pipeline - Pipeline steps and execution stats
│   ├── GET  /read-model - CQRS materialized views
│   ├── GET  /read-model/pipeline - Pipeline execution view
│   └── GET  /read-model/stats - Enriched stats + event metrics
├── Configuration Endpoints
│   ├── GET  /config - Full config: .env values + schema + audio devices
│   ├── POST /config - Update .env configuration
│   ├── GET  /config/ui - Browser-based configuration UI
│   ├── GET  /audio/devices - PulseAudio/PipeWire device discovery
│   ├── GET  /crops - List per-app crop files
│   └── GET  /crops/{filename} - Serve crop file
├── Tier 1 Feature Endpoints
│   ├── GET  /multi-monitor - Multi-monitor snapshot + activity
│   ├── GET  /memory/search - Semantic memory search (q, k, type)
│   ├── GET  /memory/stats - Semantic memory statistics
│   ├── POST /memory/compress - Trigger memory compression
│   ├── GET  /templates - Action templates with learning stats
│   ├── POST /templates/import - Import templates from JSON
│   ├── GET  /templates/export - Export templates as JSON
│   ├── GET  /ocr/post-process/stats - OCR enhancer stats
│   └── GET  /predictive - Transition matrix + patterns
└── Background Tasks
    └── screen_analysis_loop() → PipelineOrchestrator
        └── 13 composable steps (see Pipeline section above)
```

#### event_bus.py - Event Bus (Event Sourcing + CQRS)
```python
EventBus
├── subscribe(type, handler) - Register async handler for event type
├── publish(event) → persist + dispatch to all matching handlers
├── emit(type, data, source) → create Event (convenience)
├── new_correlation_id() → str (links pipeline events)
└── add_middleware(fn) - Transform events before dispatch

EventStore (SQLite)
├── append(event) - Persist immutable event
├── query(type, source, correlation_id, since, limit) → List[Dict]
└── get_stats() → Dict

Event (frozen dataclass)
├── type: str (EventType enum or custom)
├── data: Dict (JSON-serializable payload)
├── event_id: str (auto UUID)
├── correlation_id: str (links related events)
├── category: str (command | query | event | system)
└── to_dict() / to_json()
```

#### pipeline.py - Pipeline Orchestrator (SOLID)
```python
PipelineOrchestrator
├── add_step(step) / remove_step(name) - Builder pattern
├── insert_before(name, step) / insert_after(name, step)
├── run(ctx) → PipelineContext - Execute all steps in order
└── get_stats() → Dict

PipelineContext (shared accumulator)
├── run_id, correlation_id - Identity
├── all_windows, active_window, roi - Phase 1 output
├── image_b64, capture_result - Phase 2 output
├── organized_screen, screen_summary - Phase 3+4 output
├── multi_monitor_snapshot, monitor_description - Tier 1: Multi-monitor
├── full_context, prompt_addon - Phase 5 output
├── analysis_result - Phase 6 output
├── ocr_enhanced, ocr_corrections - Tier 1: OCR post-processing
├── agent_actions - Phase 7 output
├── template_actions - Tier 1: Learned action templates
├── recalled_memories - Tier 1: Semantic memory recall
├── prediction, used_prefetch - Tier 1: Predictive pre-fetch
├── broadcast_data - Phase 8 output
└── steps_executed, step_timings, errors - Metrics

PipelineStep (Protocol)
├── name: str
├── can_run(ctx) → bool - Gate check
└── execute(ctx, bus) → PipelineContext - Do work + emit events
```

#### protocols.py - Component Interfaces (ISP + DIP)
```python
Protocols (runtime_checkable)
├── ScreenCapture    - capture(), adaptive_interval, get_monitors()
├── OCRExtractor     - extract(), set_engine(), benchmark()
├── ScreenAnalyzer   - analyze(), set_mode()
├── WindowDetector   - get_active_window(), get_monitors(), get_window_roi()
├── ProcessScanning  - scan_all_windows(), get_window_layout()
├── WindowCropping   - organize_screen()
├── ProfileProvider  - get_prompt_addon(), get_all_profiles()
├── CommandAgent     - suggest_actions(), execute_action(), execute_safe()
├── ContextStore     - add(), get_context_string(), get_recent()
├── SpeechToText     - start(), stop()
└── EventBroadcaster - broadcast(event_type, data)
```

#### command_handlers.py - CQRS Write Side
```python
CommandHandlers
├── register_all() - Subscribe all cmd.* handlers to EventBus
├── handle_switch_ocr(event)    - cmd.switch_ocr_engine
├── handle_switch_mode(event)   - cmd.switch_mode
├── handle_execute_action(event) - cmd.execute_action
├── handle_approve_action(event) - cmd.approve_action
├── handle_run_safe(event)      - cmd.run_safe
└── handle_run_benchmark(event) - cmd.run_benchmark
```

#### query_handlers.py - CQRS Read Side + ReadModel
```python
ReadModel (materialized view)
├── on_windows_scanned(data)     - Update window count
├── on_screen_captured(data)     - Update capture size
├── on_analysis_completed(data)  - Update tokens/cost/provider
├── on_agent_suggested(data)     - Update action counts
├── on_pipeline_completed(...)   - Update run metrics
├── get_pipeline_view() → Dict   - Materialized pipeline state
├── get_analysis_view() → Dict   - Materialized analysis metrics
└── get_event_counts() → Dict    - Event frequency counts

QueryHandlers
├── register_all() - Subscribe projectors to domain events
├── query_health() → Dict        - System health (read-only)
├── query_stats() → Dict         - Enriched stats + read model
├── query_events(filters) → Dict - Event store query
├── query_pipeline() → Dict      - Pipeline execution state
└── query_event_store_stats()    - Store statistics
```

#### config_service.py - Configuration Management
```python
EnvConfig (.env read/write)
├── read_env() → Dict[str, str]        - Parse .env file
├── update_env(updates) → Dict          - Update .env preserving comments
└── get_config_with_schema() → Dict     - Values + schema + audio devices

AudioDeviceScanner (PulseAudio/PipeWire)
├── discover_audio_devices() → Dict     - All sources/sinks/monitors
├── _discover_pactl() → (sources, sinks) - pactl list parsing
└── _discover_sounddevice() → List      - sounddevice fallback

CONFIG_SCHEMA = 7 groups:
├── 🔊 Audio / STT (mic, monitor, speaker, language, model, API key)
├── 🤖 Vision / AI Model (model, mode, tokens, temperature)
├── 🔤 OCR (engine, languages, GPU)
├── 🔑 Klucze API (Gemini, OpenAI, Anthropic, Groq, DeepSeek, Mistral)
├── ⚡ Wydajność (thresholds, intervals, dimensions)
├── 🖥️ Funkcje (window awareness, shell agent, capture mode)
└── 🌐 Serwer (port, host, log level, debug)
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
