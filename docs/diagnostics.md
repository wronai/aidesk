# Diagnostyka systemu

Ten dokument opisuje jak diagnozowac konfiguracje i runtime projektu Proxeen.

## 1. Jeden punkt wejscia: `make diag`

Uruchom:

```bash
make diag
```

Komenda wykonuje dwa etapy:

1. `backend/test_setup.py` (zaleznosci, .env, audio, narzedzia systemowe, importy)
2. `backend/preflight.py` (ping modeli, klucze API, sprawdzenie backendow okien)

`make diagnostics` jest aliasem wstecznie kompatybilnym do `make diag`.

## 2. Co sprawdza setup (`test_setup.py`)

- wersje Pythona i pakiety pip
- obecna konfiguracje `.env`
- klucze API (wymagane i opcjonalne)
- screen capture i image processing
- urzadzenia audio
- narzedzia systemowe okien (`xdotool`, `xprop`, `xrandr`, `wmctrl`)
- import kluczowych modulow backendu

Setup odpowiada na pytanie: "czy host i srodowisko lokalne sa gotowe?"

## 3. Co sprawdza preflight (`preflight.py`)

- dostepnosc LiteLLM
- obecnosc wymaganych kluczy API dla aktywnych modeli
- realny ping modelu Vision LLM
- realny ping modelu VLM OCR (jesli wlaczony)
- backend detekcji okien:
  - preferowany: `python-xlib + ewmh`
  - fallback: `xdotool`, `xprop`, `wmctrl`

Preflight odpowiada na pytanie: "czy runtime i providerzy dzialaja teraz?"

## 4. Interpretacja wyniku

- Oba etapy `0` -> system gotowy
- Ktorykolwiek etap != `0` -> popraw konfiguracje wedlug komunikatow

Szybka checklista po bledzie:

1. Uzupelnij `backend/.env` i API keys
2. Doinstaluj zaleznosci pip (`pip install -r backend/requirements.txt`)
3. Doinstaluj narzedzia systemowe (`xdotool`, `xprop`, `xrandr`, `wmctrl`)
4. Sprawdz `DISPLAY` (dla X11) lub backend capture dla Wayland

## 5. Diagnostyka runtime przez API

Po starcie backendu przydatne endpointy:

- `GET /health`
- `GET /stats`
- `GET /diagnostics`
- `GET /events` i `GET /events/stats`
- `GET /pipeline`

## 6. Sprawdzanie decyzji OptimizationStrategy

Strategia optymalizacji nie ma osobnego endpointu API, ale jej decyzje sa
widoczne w logach `nfo` jako `decision.optimization`.

Szybka checklista:

1. Sprawdz konfiguracje w `backend/.env`:
   - `OPTIMIZATION_PRIORITY`
   - `HARDWARE_PROFILE`
   - `BUDGET_WARNING_PCT`, `BUDGET_CRITICAL_PCT`, `MAX_TICK_LATENCY_MS`
2. Uruchom backend i wymus kilka tickow pipeline (normalna praca UI wystarczy).
3. Zweryfikuj decyzje w logach/SQLite (`decision.optimization`, `decision.profile_select`).

Przyklady zapytan SQL i interpretacja sa opisane w [observability.md](observability.md).

## 7. Dobre praktyki operacyjne

- Uruchamiaj `make diag` po kazdej zmianie modelu lub OCR engine.
- Uruchamiaj `make diag` po instalacji na nowej maszynie.
- Traktuj preflight failure jako blocker przed uruchomieniem petli runtime.
