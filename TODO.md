# TODO — backlog techniczny (stan: 2026-02-14)

Backlog został przygotowany na podstawie aktualnego kodu oraz indeksu `project.functions.toon`.

## P0 — stabilność runtime

- [ ] Naprawić payload SSE dla clipboard w `AnalysisLoop`:
  - `backend/analysis_loop.py` odwołuje się do `ctx.clipboard_auto_copies`, ale pole nie istnieje w `PipelineContext`.
  - Opcje: dodać pole do `PipelineContext` albo użyć bezpiecznego fallback (`getattr(ctx, "clipboard_auto_copies", 0)`).

## P1 — domknięcie konfiguracji (typed Settings)

- [ ] Dodać `daily_budget` i `hourly_budget` do `Settings` (obecnie fallback + TODO w `backend/cost_budget.py`).
- [ ] Dodać `ocr_spell_dict`/`OCR_SPELL_DICT` do `Settings` (obecnie TODO w `backend/ocr_post_process.py`).
- [ ] Dodać `crop_change_threshold` do `Settings` (obecnie hardcoded TODO w `backend/window_cropper.py`).
- [ ] Dodać `diag_interval` do `Settings` (obecnie TODO w `backend/bootstrap.py`).

## P1 — pipeline i koszty

- [ ] Zintegrować `cost_budget` z decyzją o trybie analizy (degradacja do `ocr_only` po przekroczeniu budżetu).
  - `cost_budget` jest tworzony i przekazywany do `create_pipeline`, ale nie jest używany w krokach.

## P2 — testy i jakość

- [ ] Dodać test regresyjny dla scenariusza clipboard w `_broadcast_state` (żeby wykrywać brak pól w `PipelineContext`).
- [ ] Rozszerzyć testy tras skill/clipboard o scenariusze z realnym kontekstem schowka (`/analyze-selection`, `/skill/execute`).
- [ ] Uporządkować warningi markdownlint w `CHANGELOG.md` (historyczny dług formatowania).

## P3 — UX/operacyjne

- [ ] Ujednolicić konfigurację URL backendu dla overlay (obecnie domyślnie `http://localhost:8001` w `overlay/app.js` i `overlay/services/sse.js`).
- [ ] Dodać przykładowe pluginy referencyjne do `backend/plugins/` (demo rozszerzalności plugin loadera).
