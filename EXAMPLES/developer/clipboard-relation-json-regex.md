# Developer — ClipboardRelation: JSON + Regex workflow

## Dla kogo

Programista analizujący dwa fragmenty danych (zaznaczenie vs schowek).

## Cel

Wykorzystać `clipboard_relation` do szybkiego porównania JSON lub testowania regexów.

## Przykład A: JSON diff

1. Skopiuj do schowka:

   ```json
   {"name":"Alice","role":"dev"}
   ```

2. Zaznacz w IDE drugi JSON:

   ```json
   {"name":"Alice","role":"lead"}
   ```

3. Uruchom analizę zaznaczenia (`Ctrl+Shift+S`).
4. Wybierz opcję `json_diff`.

### API

```bash
curl -X POST http://localhost:8001/skill/execute \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "clipboard_relation",
    "option_id": "json_diff",
    "text": "{\"name\":\"Alice\",\"role\":\"dev\"}",
    "clipboard_text": "{\"name\":\"Alice\",\"role\":\"lead\"}"
  }'
```

## Przykład B: test regex

1. Skopiuj dane testowe do schowka:

   ```text
   user1@example.com
   bad_mail
   test@domain.pl
   ```

2. Zaznacz regex:

   ```text
   [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}
   ```

3. Uruchom analizę i wybierz `regex_match`.

## Oczekiwany efekt

- Szybki diff danych i dopasowania regex bez przełączania narzędzi.
- Wynik można od razu skopiować do schowka.
