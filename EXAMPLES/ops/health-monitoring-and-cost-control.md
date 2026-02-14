# Ops/SRE — Monitoring zdrowia i kontrola kosztów

## Dla kogo

Osoba utrzymująca środowisko lokalne/zespołowe Proxeen.

## Cel

Zminimalizować ryzyko awarii i ograniczyć koszty API.

## Szybki runbook

1. Start/stop:

   ```bash
   make run
   make stop
   make status
   ```

2. Health check:

   ```bash
   curl http://localhost:8001/health
   ```

3. Runtime metrics:

   ```bash
   curl http://localhost:8001/stats
   curl http://localhost:8001/pipeline
   curl http://localhost:8001/events/stats
   ```

## Ustawienia kosztowe (`backend/.env`)

Rekomendowany profil dla niskich kosztów:

```env
ANALYSIS_MODE=hybrid
CHANGE_THRESHOLD=12
ENABLE_STT=false
```

Dla jakości (wyższe koszty):

```env
ANALYSIS_MODE=vision_only
CHANGE_THRESHOLD=8
ENABLE_STT=true
```

## Objawy i reakcje

- **Wysoki CPU** → zwiększ `MIN_CAPTURE_INTERVAL`, podnieś `CHANGE_THRESHOLD`.
- **Wzrost kosztu tokenów** → przełącz na `hybrid`, tańszy model, mniej czułą detekcję zmian.
- **Niestabilny backend** → sprawdź `/health`, potem pełny restart `make stop && make run`.
