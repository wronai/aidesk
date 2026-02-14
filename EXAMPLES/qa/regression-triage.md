# QA — Triage regresji po zmianie

## Dla kogo

QA/Tester, który chce szybko potwierdzić czy ostatnie zmiany nie zepsuły
kluczowych ścieżek.

## Cel

Przejść checklistę funkcjonalną i zebrać artefakty diagnostyczne.

## Checklist

1. Uruchom:

   ```bash
   make run
   ```

2. Sprawdź zdrowie systemu:

   ```bash
   curl http://localhost:8001/health
   curl http://localhost:8001/stats
   ```

3. Zweryfikuj ścieżkę zaznaczenia:
   - zaznacz tekst,
   - uruchom `Ctrl+Shift+S`,
   - potwierdź, że panel pokazuje opcje skilli.
4. Zweryfikuj ścieżkę voice command:
   - wypowiedz komendę typu „przetłumacz” lub „przeczytaj”,
   - sprawdź czy panel wyboru pojawia się automatycznie.
5. Zweryfikuj agenta:
   - wywołaj akcję,
   - zatwierdź i wykonaj,
   - sprawdź rezultat w overlay.

## Artefakty do raportu

- `GET /pipeline` — aktywne kroki i statystyki.
- `GET /events/stats` — zagregowane eventy.
- `GET /traces` — metryki i ostatnie spany.

## Gotowy szablon zgłoszenia

```text
[REGRESJA] Krótki tytuł

Build/commit:
Środowisko:
Kroki reprodukcji:
Wynik oczekiwany:
Wynik faktyczny:
Załączniki: /pipeline, /events/stats, screenshot
```
