"""
Event Bus - Async typed event system with pub/sub and event store.

Implements Event Sourcing pattern:
- Typed events with immutable payloads
- Async pub/sub with handler registration
- SQLite event store for replay and audit
- CQRS: events separate commands (write) from queries (read)

Usage:
    bus = EventBus()
    bus.subscribe("screen.captured", handler_fn)
    await bus.publish(Event(type="screen.captured", data={...}))
"""
import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

import structlog

logger = structlog.get_logger()


# ===== Event Types (domain taxonomy) =====

class EventCategory(str, Enum):
    """Top-level event categories for CQRS separation."""
    COMMAND = "command"    # Write intent (state-changing)
    QUERY = "query"       # Read intent (no side effects)
    EVENT = "event"       # Domain event (fact that happened)
    SYSTEM = "system"     # Infrastructure/lifecycle


class EventType(str, Enum):
    """All known event types in the system."""
    # Lifecycle
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    COMPONENT_INITIALIZED = "component.initialized"
    COMPONENT_FAILED = "component.failed"

    # Pipeline phases (domain events)
    WINDOWS_SCANNED = "pipeline.windows_scanned"
    SCREEN_CAPTURED = "pipeline.screen_captured"
    SCREEN_CROPPED = "pipeline.screen_cropped"
    SCREEN_ORGANIZED = "pipeline.screen_organized"
    CONTEXT_BUILT = "pipeline.context_built"
    ANALYSIS_COMPLETED = "pipeline.analysis_completed"
    AGENT_SUGGESTED = "pipeline.agent_suggested"
    BROADCAST_SENT = "pipeline.broadcast_sent"
    PIPELINE_COMPLETED = "pipeline.completed"
    CLIPBOARD_UPDATED = "pipeline.clipboard_updated"
    PASTE_SUGGESTED = "pipeline.paste_suggested"

    # Commands (CQRS write side)
    CMD_SWITCH_OCR_ENGINE = "cmd.switch_ocr_engine"
    CMD_SWITCH_MODE = "cmd.switch_mode"
    CMD_RUN_BENCHMARK = "cmd.run_benchmark"
    CMD_EXECUTE_ACTION = "cmd.execute_action"
    CMD_APPROVE_ACTION = "cmd.approve_action"
    CMD_RUN_SAFE = "cmd.run_safe"

    # Queries (CQRS read side)
    QUERY_HEALTH = "query.health"
    QUERY_STATS = "query.stats"
    QUERY_WINDOW = "query.window"

    # External triggers
    TRANSCRIPT_RECEIVED = "external.transcript"
    SPEECH_FINAL = "external.speech_final"


# ===== Event (immutable fact) =====

@dataclass(frozen=True)
class Event:
    """
    Immutable domain event.

    An Event is a fact that something happened. Once created, it cannot be modified.
    This is the core building block of Event Sourcing.
    """
    type: str                          # EventType value or custom string
    data: Dict[str, Any]               # Payload (should be JSON-serializable)
    event_id: str = ""                 # Unique ID (auto-generated if empty)
    timestamp: float = 0.0             # Unix timestamp (auto-set if 0)
    source: str = ""                   # Component that emitted this event
    category: str = "event"            # EventCategory value
    correlation_id: str = ""           # Links related events (e.g. same pipeline run)
    version: int = 1                   # Schema version for forward compat

    def __post_init__(self):
        # Frozen dataclass workaround for defaults
        if not self.event_id:
            object.__setattr__(self, "event_id", str(uuid.uuid4())[:12])
        if self.timestamp == 0.0:
            object.__setattr__(self, "timestamp", time.time())

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "category": self.category,
            "source": self.source,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "version": self.version,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ===== Event Handler protocol =====

# Handler is an async callable: (Event) -> None
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


# ===== Event Store (SQLite persistence) =====

class EventStore:
    """
    Persistent event store using SQLite.

    Enables:
    - Full audit trail of all events
    - Event replay for debugging / state reconstruction
    - Query by type, source, time range, correlation_id
    """

    def __init__(
        self,
        db_path: str = "logs/events.db",
        max_events: int = 50000,
        flush_every: int = 20,
        prune_every: int = 100,
    ):
        self.db_path = db_path
        self.max_events = max_events
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._flush_every = max(1, int(flush_every))
        self._prune_every = max(1, int(prune_every))
        self._pending_writes = 0
        self._writes_since_prune = 0
        self._lock = threading.Lock()
        self._conn = self._open_connection()
        self._ensure_db()

    def _open_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Tuning for high-frequency append workload.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    def _ensure_db(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        with self._lock:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT,
                timestamp REAL NOT NULL,
                correlation_id TEXT,
                version INTEGER DEFAULT 1,
                data TEXT NOT NULL
            )
        """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_corr ON events(correlation_id)")
            self._conn.commit()

    def _flush_if_needed(self):
        if self._pending_writes > 0:
            self._conn.commit()
            self._pending_writes = 0

    def _prune_old_events_if_needed(self):
        if self.max_events <= 0:
            return

        count = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        over_limit = count - self.max_events
        if over_limit <= 0:
            return

        self._conn.execute(
            "DELETE FROM events WHERE event_id IN (SELECT event_id FROM events ORDER BY timestamp ASC LIMIT ?)",
            (over_limit,),
        )

    def flush(self):
        """Flush pending writes to disk."""
        try:
            with self._lock:
                self._flush_if_needed()
        except Exception as e:
            logger.warning("Event store flush failed", error=str(e))

    def close(self):
        """Flush and close connection (call on app shutdown)."""
        try:
            with self._lock:
                self._flush_if_needed()
                self._conn.close()
        except Exception as e:
            logger.warning("Event store close failed", error=str(e))

    def append(self, event: Event):
        """Append event to store (fire-and-forget, never blocks pipeline)."""
        try:
            payload = json.dumps(event.data, default=str)
            with self._lock:
                self._conn.execute(
                    "INSERT OR IGNORE INTO events (event_id, type, category, source, timestamp, correlation_id, version, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.type,
                        event.category,
                        event.source,
                        event.timestamp,
                        event.correlation_id,
                        event.version,
                        payload,
                    ),
                )

                self._pending_writes += 1
                self._writes_since_prune += 1

                if self._writes_since_prune >= self._prune_every:
                    self._prune_old_events_if_needed()
                    self._writes_since_prune = 0

                if self._pending_writes >= self._flush_every:
                    self._flush_if_needed()
        except Exception as e:
            logger.warning("Event store append failed", error=str(e))

    def query(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        correlation_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Query events from store."""
        where_clauses = []
        params = []

        if event_type:
            where_clauses.append("type = ?")
            params.append(event_type)
        if source:
            where_clauses.append("source = ?")
            params.append(source)
        if correlation_id:
            where_clauses.append("correlation_id = ?")
            params.append(correlation_id)
        if since:
            where_clauses.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            self._flush_if_needed()
            rows = self._conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            results.append({
                "event_id": row["event_id"],
                "type": row["type"],
                "category": row["category"],
                "source": row["source"],
                "timestamp": row["timestamp"],
                "correlation_id": row["correlation_id"],
                "version": row["version"],
                "data": json.loads(row["data"]),
            })
        return results

    def get_stats(self) -> Dict:
        """Get event store statistics."""
        try:
            with self._lock:
                self._flush_if_needed()
                total = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                types = self._conn.execute(
                    "SELECT type, COUNT(*) as cnt FROM events GROUP BY type ORDER BY cnt DESC LIMIT 10"
                ).fetchall()
                oldest = self._conn.execute("SELECT MIN(timestamp) FROM events").fetchone()[0]
                newest = self._conn.execute("SELECT MAX(timestamp) FROM events").fetchone()[0]

            return {
                "total_events": total,
                "top_types": {t[0]: t[1] for t in types},
                "oldest_timestamp": oldest,
                "newest_timestamp": newest,
                "db_path": self.db_path,
            }
        except Exception:
            return {"total_events": 0, "db_path": self.db_path}


# ===== Event Bus =====

class EventBus:
    """
    Async event bus with typed pub/sub and persistent event store.

    Features:
    - Subscribe handlers to specific event types or wildcards
    - Publish events asynchronously (non-blocking)
    - All events persisted to SQLite event store
    - Correlation IDs link related events across pipeline stages
    - Middleware support for cross-cutting concerns (logging, metrics)
    """

    def __init__(
        self,
        store: Optional[EventStore] = None,
        enable_store: bool = True,
    ):
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._wildcard_handlers: List[EventHandler] = []
        self._middleware: List[Callable[[Event], Event]] = []
        self.store = store
        self._enable_store = enable_store
        self._total_published = 0
        self._total_handled = 0
        self._errors = 0

        if enable_store and not store:
            self.store = EventStore()

        logger.info("EventBus initialized", store_enabled=enable_store)

    def subscribe(self, event_type: str, handler: EventHandler):
        """
        Subscribe a handler to a specific event type.

        Args:
            event_type: EventType value or custom string. Use "*" for all events.
            handler: Async callable (Event) -> None
        """
        if event_type == "*":
            self._wildcard_handlers.append(handler)
            logger.debug("Wildcard handler registered", handler=handler.__name__)
        else:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            logger.debug("Handler registered", event_type=event_type, handler=handler.__name__)

    def unsubscribe(self, event_type: str, handler: EventHandler):
        """Remove a handler from an event type."""
        if event_type == "*":
            if handler in self._wildcard_handlers:
                self._wildcard_handlers.remove(handler)
        elif event_type in self._handlers:
            if handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)

    def add_middleware(self, middleware: Callable[[Event], Event]):
        """Add middleware that transforms events before dispatch."""
        self._middleware.append(middleware)

    async def publish(self, event: Event):
        """
        Publish an event to all subscribed handlers.

        Steps:
        1. Apply middleware transforms
        2. Persist to event store
        3. Dispatch to type-specific handlers
        4. Dispatch to wildcard handlers
        5. Errors in handlers don't stop other handlers
        """
        # Apply middleware
        processed = event
        for mw in self._middleware:
            processed = mw(processed)

        self._total_published += 1

        # Persist to store (non-blocking intent, but sync SQLite is fast)
        if self._enable_store and self.store:
            self.store.append(processed)

        # Collect handlers
        handlers = list(self._handlers.get(processed.type, []))
        handlers.extend(self._wildcard_handlers)

        # Dispatch to all handlers
        for handler in handlers:
            try:
                await handler(processed)
                self._total_handled += 1
            except Exception as e:
                self._errors += 1
                logger.error(
                    "Event handler error",
                    event_type=processed.type,
                    handler=handler.__name__,
                    error=str(e),
                )

    async def publish_many(self, events: List[Event]):
        """Publish multiple events in order."""
        for event in events:
            await self.publish(event)

    def emit(self, event_type: str, data: Dict, source: str = "", correlation_id: str = "") -> Event:
        """
        Convenience: create Event and return it (caller must await publish).

        Usage:
            event = bus.emit("pipeline.captured", {"image": "..."}, source="capture")
            await bus.publish(event)
        """
        return Event(
            type=event_type,
            data=data,
            source=source,
            category=self._infer_category(event_type),
            correlation_id=correlation_id,
        )

    @staticmethod
    def _infer_category(event_type: str) -> str:
        """Infer CQRS category from event type prefix."""
        if event_type.startswith("cmd."):
            return EventCategory.COMMAND.value
        if event_type.startswith("query."):
            return EventCategory.QUERY.value
        if event_type.startswith("system."):
            return EventCategory.SYSTEM.value
        return EventCategory.EVENT.value

    def new_correlation_id(self) -> str:
        """Generate a new correlation ID for linking pipeline events."""
        return str(uuid.uuid4())[:8]

    def get_stats(self) -> Dict:
        """Get event bus statistics."""
        stats = {
            "total_published": self._total_published,
            "total_handled": self._total_handled,
            "handler_errors": self._errors,
            "registered_types": len(self._handlers),
            "total_handlers": sum(len(h) for h in self._handlers.values()) + len(self._wildcard_handlers),
            "wildcard_handlers": len(self._wildcard_handlers),
        }
        if self.store:
            stats["store"] = self.store.get_stats()
        return stats


def create_event_bus(enable_store: bool = True, db_path: str = "logs/events.db") -> EventBus:
    """Create EventBus from configuration."""
    store = EventStore(db_path=db_path) if enable_store else None
    return EventBus(store=store, enable_store=enable_store)
