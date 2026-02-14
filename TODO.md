# TODO — backlog techniczny (stan: 2026-02-14)

Backlog został przygotowany na podstawie aktualnego kodu oraz indeksu `project.functions.toon`.

## P0 — stabilność runtime

- [x] Naprawić payload SSE dla clipboard w `AnalysisLoop`:
  - `backend/analysis_loop.py` odwołuje się do `ctx.clipboard_auto_copies`, ale pole nie istnieje w `PipelineContext`.
  - Opcje: dodać pole do `PipelineContext` albo użyć bezpiecznego fallback (`getattr(ctx, "clipboard_auto_copies", 0)`).

## P1 — domknięcie konfiguracji (typed Settings)

- [x] Dodać `daily_budget` i `hourly_budget` do `Settings` (obecnie fallback + TODO w `backend/cost_budget.py`).
- [x] Dodać `ocr_spell_dict`/`OCR_SPELL_DICT` do `Settings` (obecnie TODO w `backend/ocr_post_process.py`).
- [x] Dodać `crop_change_threshold` do `Settings` (obecnie hardcoded TODO w `backend/window_cropper.py`).
- [x] Dodać `diag_interval` do `Settings` (obecnie TODO w `backend/bootstrap.py`).

## P1 — pipeline i koszty

- [x] Zintegrować `cost_budget` z decyzją o trybie analizy (degradacja do `ocr_only` po przekroczeniu budżetu).
  - `cost_budget` jest tworzony i przekazywany do `create_pipeline`, ale nie jest używany w krokach.

## P2 — testy i jakość

- [x] Dodać test regresyjny dla scenariusza clipboard w `_broadcast_state` (żeby wykrywać brak pól w `PipelineContext`).
- [x] Rozszerzyć testy tras skill/clipboard o scenariusze z realnym kontekstem schowka (`/analyze-selection`, `/skill/execute`).
- [x] Uporządkować warningi markdownlint w `CHANGELOG.md` (historyczny dług formatowania).

## P3 — UX/operacyjne

- [x] Ujednolicić konfigurację URL backendu dla overlay (obecnie domyślnie `http://localhost:8001` w `overlay/app.js` i `overlay/services/sse.js`).
  - Nowy plik `overlay/config.js` — single source of truth: `BACKEND_URL`, `SSE_URL`.
  - `app.js` i `services/sse.js` importują z `config.js`.
- [x] Dodać przykładowe pluginy referencyjne do `backend/plugins/` (demo rozszerzalności plugin loadera).
  - `example_logger.py` — subskrybuje pipeline events i loguje do structured log.
  - `example_window_notifier.py` — wykrywa zmianę kategorii okna i emituje custom event.
