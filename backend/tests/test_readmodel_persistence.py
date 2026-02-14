"""Tests for ReadModel snapshot persistence (save/load)."""
import json
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query_handlers import ReadModel


class TestStateDict:
    def test_empty_model(self):
        rm = ReadModel()
        d = rm._state_dict()
        assert d["total_pipeline_runs"] == 0
        assert d["event_counts"] == {}
        assert d["last_pipeline_run_id"] == ""

    def test_populated_model(self):
        rm = ReadModel()
        rm.on_windows_scanned({"total": 5})
        rm.on_analysis_completed({"tokens": 200, "cost": 0.05, "provider": "openai"})
        rm.on_pipeline_completed("r1", ["a"], {"a": 10}, [])

        d = rm._state_dict()
        assert d["last_window_count"] == 5
        assert d["last_analysis_tokens"] == 200
        assert d["total_pipeline_runs"] == 1
        assert d["last_pipeline_run_id"] == "r1"


class TestRestore:
    def test_restore_from_dict(self):
        rm = ReadModel()
        rm._restore({
            "total_pipeline_runs": 42,
            "event_counts": {"a": 10, "b": 5},
            "last_analysis_provider": "gemini",
        })
        assert rm.total_pipeline_runs == 42
        assert rm.event_counts == {"a": 10, "b": 5}
        assert rm.last_analysis_provider == "gemini"

    def test_restore_ignores_unknown_keys(self):
        rm = ReadModel()
        rm._restore({"unknown_key": "value", "total_pipeline_runs": 1})
        assert rm.total_pipeline_runs == 1
        assert not hasattr(rm, "unknown_key")

    def test_restore_partial(self):
        rm = ReadModel()
        rm.total_pipeline_runs = 10
        rm._restore({"last_window_count": 3})
        assert rm.total_pipeline_runs == 10  # not overwritten
        assert rm.last_window_count == 3


class TestSaveSnapshot:
    def test_save_creates_file(self, tmp_path):
        rm = ReadModel()
        rm.on_pipeline_completed("r1", ["scan"], {"scan": 5}, [])
        path = str(tmp_path / "snapshot.json")

        rm.save_snapshot(path)

        assert os.path.exists(path)
        data = json.loads(open(path).read())
        assert data["total_pipeline_runs"] == 1
        assert data["last_pipeline_run_id"] == "r1"

    def test_save_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "sub" / "dir" / "snapshot.json")
        rm = ReadModel()
        rm.save_snapshot(path)
        assert os.path.exists(path)

    def test_save_graceful_on_bad_path(self):
        rm = ReadModel()
        rm.save_snapshot("/proc/nonexistent/snapshot.json")
        # Should not raise


class TestLoadSnapshot:
    def test_load_restores_state(self, tmp_path):
        path = str(tmp_path / "snapshot.json")

        # Save
        rm1 = ReadModel()
        rm1.on_windows_scanned({"total": 7})
        rm1.on_analysis_completed({"tokens": 300, "cost": 0.1, "provider": "anthropic"})
        rm1.on_pipeline_completed("r42", ["a", "b"], {"a": 1}, [])
        rm1.save_snapshot(path)

        # Load into fresh model
        rm2 = ReadModel()
        rm2.load_snapshot(path)

        assert rm2.last_window_count == 7
        assert rm2.last_analysis_tokens == 300
        assert rm2.total_pipeline_runs == 1
        assert rm2.last_pipeline_run_id == "r42"

    def test_load_missing_file_no_error(self, tmp_path):
        rm = ReadModel()
        rm.load_snapshot(str(tmp_path / "nonexistent.json"))
        assert rm.total_pipeline_runs == 0

    def test_load_corrupted_file(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("{invalid json")
        rm = ReadModel()
        rm.load_snapshot(path)
        assert rm.total_pipeline_runs == 0

    def test_roundtrip_preserves_event_counts(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        from event_bus import Event

        rm1 = ReadModel()
        rm1.on_event(Event(type="pipeline.windows_scanned", data={}))
        rm1.on_event(Event(type="pipeline.windows_scanned", data={}))
        rm1.on_event(Event(type="pipeline.analysis_completed", data={}))
        rm1.save_snapshot(path)

        rm2 = ReadModel()
        rm2.load_snapshot(path)
        counts = rm2.get_event_counts()
        assert counts["pipeline.windows_scanned"] == 2
        assert counts["pipeline.analysis_completed"] == 1

    def test_roundtrip_preserves_agent_suggestions(self, tmp_path):
        path = str(tmp_path / "snapshot.json")

        rm1 = ReadModel()
        rm1.on_agent_suggested({"count": 3})
        rm1.on_agent_suggested({"count": 7})
        rm1.save_snapshot(path)

        rm2 = ReadModel()
        rm2.load_snapshot(path)
        assert rm2.total_agent_suggestions == 10
        assert rm2.last_agent_action_count == 7
