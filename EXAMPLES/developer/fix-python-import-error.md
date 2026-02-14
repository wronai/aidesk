# Developer — Naprawa błędu `ModuleNotFoundError`

## Dla kogo

Programista Python pracujący w terminalu/IDE, który chce szybko naprawić brakujący pakiet.

## Cel

Od błędu na ekranie do gotowej komendy naprawczej i jej wykonania.

## Kroki (UI)

1. Uruchom aplikację.

   ```bash
   make run
   ```

2. W IDE/terminalu wygeneruj błąd, np.:

   ```text
   ModuleNotFoundError: No module named 'flask'
   ```

3. Zaznacz fragment błędu myszką i naciśnij `Ctrl+Shift+S` (analiza zaznaczenia)
   albo wypowiedz komendę głosową zawierającą `uruchom` / `przetłumacz` /
   `przeczytaj`.
4. W panelu wyboru opcji kliknij sugestię naprawy (np. `pip install flask`) albo
   skopiuj komendę.
5. Jeśli pojawi się akcja agenta, zatwierdź i uruchom.

## Kroki (API)

```bash
curl -X POST http://localhost:8001/analyze-selection \
  -H "Content-Type: application/json" \
  -d '{
    "text": "ModuleNotFoundError: No module named \'flask\'",
    "clipboard_text": "Traceback ... ModuleNotFoundError: No module named \'flask\'"
  }'
```

Następnie wykonaj wybraną opcję skilla:

```bash
curl -X POST http://localhost:8001/skill/execute \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "error_fixer",
    "option_id": "copy_fix",
    "text": "ModuleNotFoundError: No module named \'flask\'"
  }'
```

## Oczekiwany efekt

- Dostajesz gotową komendę (`pip install flask`) lub szybki workflow naprawczy.
- Skracasz czas od wykrycia błędu do jego rozwiązania.
