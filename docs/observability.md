# Proxeen Observability Architecture

## Overview

Proxeen uses [nfo](https://github.com/wronai/nfo) for structured function logging
and pipeline transparency. Every pipeline tick, bootstrap component init, and
runtime decision is captured as an `nfo.LogEntry` and routed through a layered
sink chain.

## Sink Chain

```
EnvTagger  →  RingBufferSink(50)  →  PipelineSink(72ch)  →  TerminalSink(toon)
                                                            ↓
                                                        SQLiteSink  →  logs/nfo_proxeen.db
                                                        MarkdownSink → logs/nfo_proxeen.md
```

| Sink | Purpose |
|---|---|
| **EnvTagger** | Adds `environment`, `hostname`, `trace_id` to every entry |
| **RingBufferSink** | Keeps last 50 entries; flushes all on ERROR/CRITICAL for context |
| **PipelineSink** | Groups entries by `pipeline_run_id`, renders box-drawing terminal blocks |
| **TerminalSink** | Formats non-pipeline entries in `toon` format for stderr |
| **SQLiteSink** | Persists all entries to `logs/nfo_proxeen.db` |
| **MarkdownSink** | Persists all entries to `logs/nfo_proxeen.md` |

## Pipeline Tick Blocks

Each pipeline tick renders as a terminal block:

```
╔══════════════════════════════════════════════════════════════════════╗
║ TICK #42 │ a1b2c3 │ 14:23:01                                       ║
╠══════════════════════════════════════════════════════════════════════╣
║ ✓ scan_windows            12ms │ 8 win, VSCode                      ║
║ ✓ detect_active_window     1ms │ VSCode                             ║
║ ✓ capture_screen          45ms │ 64KB, CHANGE                       ║
║ ⊘ crop_windows                 │ skipped (can_run=False)            ║
║ ✓ build_context            5ms │ 1200ch ctx                         ║
║ ✓ analyze                890ms │ $0.0023, gemini-2.0-flash          ║
║   └─ gemini-2.0-flash 1200→350tok $0.0023                          ║
║   └─ OCR: paddleocr 23ms, 847ch                                    ║
║ ✓ suggest_actions          3ms │ 2 actions                          ║
║ ✓ build_broadcast          1ms │ 7 events                           ║
║ DATA capture_screen:64KB → ctx:1200ch → llm:1200→350tok             ║
╠══════════════════════════════════════════════════════════════════════╣
║ 957ms │ $0.0023 │ 7/8 steps                                         ║
║ COST Session: $0.0847 │ avg/tick: $0.0021                            ║
╚══════════════════════════════════════════════════════════════════════╝
```

### Reading the block

- **✓** — step executed successfully
- **✗** — step failed (with error message)
- **⊘** — step skipped (`can_run()` returned `False`)
- **►** — decision annotation (e.g., budget downgrade, optimization strategy switch)
- **DATA** line — data flow sizes through the pipeline
- **COST** line — session total and rolling average per tick
- **Footer** — total duration, cost, step count

## Decision Logging

Key decisions are logged with structured `decision`/`reason` fields:

### Profile Selection (`decision.profile_select`)

| Decision | Reason | When |
|---|---|---|
| `full` | `periodic_scan` | Periodic full scan interval reached |
| `normal` | `default` | Default mode, active screen |
| `normal` | `idle_but_vlm_ocr_active` | Idle but VLM OCR is too slow for FAST |
| `fast` | `idle` | Screen idle, local OCR available |
| `full/normal/fast` | `forced` | Forced via `FORCE_PROFILE` env var |

### Budget Check (`decision.budget_check`)

| Decision | Reason | When |
|---|---|---|
| `budget_ok` | `within_limits` | Cost within hourly/daily budget |
| `downgraded` | `budget_exceeded` | Mode downgraded to save cost |
| `rejected` | `analyzer rejected switch` | Analyzer refused the downgrade |
| `no_budget_tracker` | (empty) | No cost budget configured |

### Optimization Strategy (`decision.optimization`)

This decision is emitted when `AnalyzeStep` applies an `OptimizationDecision`
from `OptimizationStrategy`.

| Field (`extra`) | Meaning |
|---|---|
| `mode` | Selected analysis mode (`skip`, `ocr_only`, `hybrid`, `ocr_plus_vision`) |
| `ocr` | Selected OCR engine (`none`, `tesseract`, `paddleocr`, `vlm_ocr`) |
| `model_tier` | Selected vision routing tier (`none`, `fallback`, `primary`) |
| `reason` | Human-readable reason from strategy (`auto→budget`, `speed_mode`, etc.) |
| `estimated_cost` | Predicted per-tick cost used by the strategy |
| `estimated_latency_ms` | Predicted per-tick latency used by the strategy |

## Tracer Bridge

The `observability.Tracer` system is bridged into nfo via `TracerNfoBridge`.
Every finished `Span` is automatically emitted as an nfo `LogEntry` with:

- `extra.trace_id`, `extra.span_id`, `extra.parent_span_id`
- `extra.span_name` and all span attributes
- `duration_ms` from the span timing
- ERROR level for failed spans

The bridge is attached during `init_pipeline()` via `attach_nfo_bridge()`.

## Bootstrap Logging

Each component init emits a `boot.<component>` entry with:

- Phase (`core`, `window`, `scanners`, `tier1`, `plugins`)
- Duration in ms
- Component type name or stats summary
- Success/failure status

A final `boot.summary` entry reports total boot time and lists OK/failed components.

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `NFO_LEVEL` | `INFO` | nfo log level (falls back to `LOG_LEVEL`) |
| `LOG_LEVEL` | `INFO` | General log level |

## Querying Logs

### SQLite

```sql
-- Recent pipeline ticks
SELECT * FROM logs WHERE function_name LIKE 'pipeline.%' ORDER BY timestamp DESC LIMIT 50;

-- Budget decisions
SELECT * FROM logs WHERE function_name = 'decision.budget_check' ORDER BY timestamp DESC;

-- Optimization decisions
SELECT
  json_extract(extra, '$.mode') AS mode,
  json_extract(extra, '$.ocr') AS ocr,
  json_extract(extra, '$.model_tier') AS model_tier,
  json_extract(extra, '$.reason') AS reason,
  timestamp
FROM logs
WHERE function_name = 'decision.optimization'
ORDER BY timestamp DESC
LIMIT 50;

-- Errors in last hour
SELECT * FROM logs WHERE level = 'ERROR' AND timestamp > datetime('now', '-1 hour');

-- Cost per tick
SELECT json_extract(extra, '$.total_cost') as cost, json_extract(extra, '$.total_ms') as ms
FROM logs WHERE json_extract(extra, '$.pipeline_complete') = 1 ORDER BY timestamp DESC LIMIT 20;
```

### Python

```python
import nfo

# Access the ring buffer for recent context
from server import _nfo_ring
recent = _nfo_ring.get_entries()  # last 50 entries

# Access pipeline sink stats
from server import _nfo_pipeline
print(f"Ticks: {_nfo_pipeline.tick_count}, Cost: ${_nfo_pipeline.session_cost:.4f}")
```

## High-Frequency Sampling

Some hot-path functions use `sample_rate` to reduce overhead:

| Function | Rate | Rationale |
|---|---|---|
| `scan_all_windows` | 10% | Called every tick, low-value when unchanged |
| `event_bus traces` | 5% | Very high volume |
| `skills analyze/execute` | 50% | Moderate volume, useful for debugging |
