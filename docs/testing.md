# Testy i jakosc

Dokument opisuje jak uruchamiac testy i szybkie suite'y przez Makefile.

## 1. Najczesciej uzywane komendy

```bash
make test         # unit + e2e
make test-units   # szybkie testy jednostkowe
make test-e2e     # testy end-to-end API
```

## 2. Rozszerzone suite'y

```bash
make test-vlm-ocr   # testy silnika VLM OCR + preflight
make test-strategy  # testy OptimizationStrategy (unit + integration)
make test-all       # pelna paczka testow backend/tests (z wykluczeniem test_plugins.py)
```

`test-all` sluzy jako szeroki smoke/regression run lokalnie przed wiekszym merge.

## 3. Rekomendowany workflow lokalny

1. Po zmianach w logice: `make test-units`
2. Po zmianach runtime/startup/config: `make diag`
3. Przed wypchnieciem brancha: `make test`
4. Przed release/refactor: `make test-all`

## 4. Co obejmuja najnowsze regresje

W aktualnym pakiecie testow dodano m.in. pokrycie dla:

- blokowania niebezpiecznych komend shell (`pipe` do interpreterow/source/eval),
- kosztow OCR i diagnostyki VLM OCR,
- selektora profili dla VLM OCR (`min_interval`, zachowanie przy idle),
- dekompozycji BuildContext (semantic recall i formatowanie memory).

## 5. Diagnostyka a testy

`make diag` nie zastepuje testow — sprawdza konfiguracje hosta i preflight providerow.
Traktuj to jako bramke operacyjna, a testy jako bramke regresji kodu.
