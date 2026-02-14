"""Focused tests for EventStore performance/path correctness."""
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_bus import Event, EventStore


class TestEventStore:
    def test_append_query_and_stats(self, tmp_path):
        db = str(tmp_path / "events.db")
        store = EventStore(db_path=db, flush_every=100, prune_every=10)

        store.append(Event(type="evt.one", data={"n": 1}, source="test"))
        store.append(Event(type="evt.one", data={"n": 2}, source="test"))
        store.append(Event(type="evt.two", data={"n": 3}, source="test"))

        rows = store.query(limit=10)
        assert len(rows) == 3

        stats = store.get_stats()
        assert stats["total_events"] == 3
        assert "evt.one" in stats["top_types"]

        store.close()

    def test_duplicate_event_id_does_not_increment_count(self, tmp_path):
        db = str(tmp_path / "events.db")
        store = EventStore(db_path=db, flush_every=100, prune_every=10)

        event = Event(type="evt.dup", data={"ok": True}, event_id="fixed-id", source="test")
        store.append(event)
        store.append(event)  # INSERT OR IGNORE

        stats = store.get_stats()
        assert stats["total_events"] == 1

        store.close()

    def test_prune_respects_max_events(self, tmp_path):
        db = str(tmp_path / "events.db")
        store = EventStore(db_path=db, max_events=3, flush_every=100, prune_every=1)

        for i in range(5):
            store.append(Event(type="evt.prune", data={"i": i}, source="test"))

        rows = store.query(limit=10)
        assert len(rows) == 3
        values = {r["data"]["i"] for r in rows}
        assert values == {2, 3, 4}

        store.close()

    def test_close_is_idempotent(self, tmp_path):
        db = str(tmp_path / "events.db")
        store = EventStore(db_path=db)

        store.append(Event(type="evt.close", data={}, source="test"))
        store.close()
        store.close()  # should be no-op

        assert store.query(limit=10) == []
        stats = store.get_stats()
        assert stats["total_events"] == 1

    def test_sampled_nfo_trace_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVENTSTORE_NFO_SAMPLE_EVERY", "1")
        db = str(tmp_path / "events.db")
        store = EventStore(db_path=db, flush_every=100, prune_every=10)

        append_trace = MagicMock(return_value={})
        query_trace = MagicMock(return_value={})
        store._trace_append_sample = append_trace
        store._trace_query_sample = query_trace

        store.append(Event(type="evt.sample", data={"x": 1}, source="sampler"))
        store.query(limit=5)

        append_trace.assert_called_once()
        query_trace.assert_called_once()

        append_kwargs = append_trace.call_args.kwargs
        assert append_kwargs["event_type"] == "evt.sample"
        assert append_kwargs["payload_bytes"] >= 1

        query_kwargs = query_trace.call_args.kwargs
        assert query_kwargs["limit"] == 5
        assert query_kwargs["returned"] >= 1

        store.close()
