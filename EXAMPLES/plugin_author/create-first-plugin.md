# Plugin Author — Pierwszy plugin

## Dla kogo

Developer rozszerzający Proxeen o własne zachowanie.

## Cel

Dodać prosty plugin reagujący na zakończenie pipeline.

## Krok 1: utwórz plik

Utwórz `backend/plugins/my_pipeline_logger.py`:

```python
from event_bus import EventType
import structlog

logger = structlog.get_logger()


class Plugin:
    name = "my_pipeline_logger"
    version = "0.1.0"
    enabled = True

    def register(self, bus, app_state):
        bus.subscribe(EventType.PIPELINE_COMPLETED.value, self._on_completed)
        logger.info("Plugin registered", name=self.name)

    async def _on_completed(self, event):
        logger.info(
            "Pipeline completed",
            run_id=event.data.get("run_id"),
            steps=len(event.data.get("steps_executed", [])),
            errors=len(event.data.get("errors", [])),
        )

    async def shutdown(self):
        logger.info("Plugin shutdown", name=self.name)
```

## Krok 2: uruchom i sprawdź

```bash
make run
```

W logach powinieneś zobaczyć `Plugin registered` oraz wpisy po każdym `pipeline.completed`.

## Krok 3: iteracja

- Dodaj własne subskrypcje eventów.
- Emituj custom eventy przez `bus.publish(...)`.
- Trzymaj plugin stateless lub czytelnie zarządzaj stanem w `app_state`.
