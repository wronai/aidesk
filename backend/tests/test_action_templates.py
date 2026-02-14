"""Tests for AppActionLibrary — suggest, learn, persistence, export/import."""
import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from action_templates import ActionTemplate, AppActionLibrary, ScoredAction


class TestActionTemplate:
    """Unit tests for the ActionTemplate dataclass."""

    def test_confidence_neutral_prior(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd")
        assert t.confidence == 0.5

    def test_confidence_all_approved(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           times_approved=5, times_rejected=0)
        assert t.confidence == 1.0

    def test_confidence_mixed(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           times_approved=3, times_rejected=1)
        assert t.confidence == 0.75

    def test_should_auto_execute_not_yet(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           times_approved=2, auto_approve_after=3)
        assert not t.should_auto_execute

    def test_should_auto_execute_threshold_reached(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           times_approved=3, times_rejected=0, auto_approve_after=3)
        assert t.should_auto_execute

    def test_should_auto_execute_blocked_by_rejection(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           times_approved=5, times_rejected=1, auto_approve_after=3)
        assert not t.should_auto_execute

    def test_should_auto_execute_blocked_by_high_risk(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           times_approved=5, times_rejected=0, auto_approve_after=3, risk_level="high")
        assert not t.should_auto_execute

    def test_should_auto_execute_manual_promotion(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd",
                           is_auto_approved=True)
        assert t.should_auto_execute

    def test_to_dict_keys(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd")
        d = t.to_dict()
        assert "template_id" in d
        assert "confidence" in d
        assert "should_auto_execute" in d


class TestAppActionLibrary:
    """Integration tests for AppActionLibrary."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        db_path = str(tmp_path / "test_templates.db")
        self.lib = AppActionLibrary(db_path=db_path, auto_approve_default=3, enabled=True)
        yield
        self.lib.close()

    def test_seed_templates_loaded(self):
        assert len(self.lib._templates) >= 10  # built-in seeds

    def test_suggest_module_not_found(self):
        results = self.lib.suggest_with_confidence(
            "ModuleNotFoundError: No module named 'flask'",
            app_category="terminal",
        )
        assert len(results) >= 1
        assert "pip install flask" in results[0].command

    def test_suggest_no_match(self):
        results = self.lib.suggest_with_confidence("all is well", app_category="terminal")
        assert results == []

    def test_suggest_filters_by_category(self):
        results = self.lib.suggest_with_confidence(
            "ModuleNotFoundError: No module named 'flask'",
            app_category="browser",  # no seed templates for browser
        )
        assert results == []

    def test_suggest_increments_suggested_count(self):
        self.lib.suggest_with_confidence(
            "ModuleNotFoundError: No module named 'flask'",
            app_category="terminal",
        )
        t = self.lib.get_template("py_module_not_found_terminal")
        assert t.times_suggested >= 1

    def test_learn_from_approval(self):
        t = self.lib.get_template("py_module_not_found_terminal")
        initial = t.times_approved
        self.lib.learn_from_approval("py_module_not_found_terminal")
        assert t.times_approved == initial + 1

    def test_learn_from_rejection(self):
        t = self.lib.get_template("py_module_not_found_terminal")
        self.lib.learn_from_rejection("py_module_not_found_terminal")
        assert t.times_rejected >= 1

    def test_learn_from_execution(self):
        t = self.lib.get_template("py_module_not_found_terminal")
        self.lib.learn_from_execution("py_module_not_found_terminal")
        assert t.times_executed >= 1

    def test_auto_promote_after_threshold(self):
        t = self.lib.add_template("promote_test", "terminal", r"promote_trigger", "echo ok",
                                  risk_level="low")
        for _ in range(t.auto_approve_after):
            self.lib.learn_from_approval("promote_test")
        assert t.should_auto_execute
        assert t.is_auto_approved

    def test_rejection_revokes_auto_approve(self):
        tid = "py_module_not_found_terminal"
        t = self.lib.get_template(tid)
        t.is_auto_approved = True
        # Add enough rejections to drop confidence below 0.7
        t.times_approved = 2
        t.times_rejected = 0
        self.lib.learn_from_rejection(tid)  # 2/3 ≈ 0.667 < 0.7
        assert not t.is_auto_approved

    def test_learn_unknown_template_is_noop(self):
        self.lib.learn_from_approval("nonexistent_template")  # should not raise

    def test_add_custom_template(self):
        t = self.lib.add_template(
            template_id="custom_1",
            app_category="terminal",
            trigger_pattern=r"segfault",
            command_template="dmesg | tail",
            description="Check kernel log",
            risk_level="safe",
        )
        assert t.template_id == "custom_1"
        assert self.lib.get_template("custom_1") is not None

    def test_remove_template(self):
        self.lib.add_template("to_remove", "terminal", ".*", "echo hi")
        assert self.lib.remove_template("to_remove")
        assert self.lib.get_template("to_remove") is None

    def test_remove_nonexistent_returns_false(self):
        assert not self.lib.remove_template("ghost")

    def test_get_templates_for_app(self):
        terminal_templates = self.lib.get_templates_for_app("terminal")
        assert len(terminal_templates) >= 5  # most seeds are for terminal

    def test_export_import_roundtrip(self):
        exported = self.lib.export_templates()
        data = json.loads(exported)
        assert data["version"] == 1
        assert len(data["templates"]) >= 10

        # Import into fresh library
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            new_db = f.name
        try:
            lib2 = AppActionLibrary(db_path=new_db, enabled=True)
            original_count = len(lib2._templates)
            imported = lib2.import_templates(exported, overwrite=False)
            # Seeds already exist, so should import 0 (all duplicates)
            assert imported == 0
            lib2.close()
        finally:
            os.unlink(new_db)

    def test_export_with_stats(self):
        self.lib.learn_from_approval("py_module_not_found_terminal")
        exported = self.lib.export_templates(include_stats=True)
        data = json.loads(exported)
        template = next(t for t in data["templates"] if t["template_id"] == "py_module_not_found_terminal")
        assert "times_approved" in template
        assert "confidence" in template

    def test_import_invalid_json(self):
        result = self.lib.import_templates("not valid json")
        assert result == 0

    def test_persistence_across_instances(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        lib1 = AppActionLibrary(db_path=db_path, enabled=True)
        lib1.add_template("persist_test", "terminal", "test", "echo test")
        lib1.learn_from_approval("persist_test")
        lib1.close()

        lib2 = AppActionLibrary(db_path=db_path, enabled=True)
        t = lib2.get_template("persist_test")
        assert t is not None
        assert t.times_approved == 1
        lib2.close()

    def test_get_stats(self):
        stats = self.lib.get_stats()
        assert stats["enabled"] is True
        assert stats["total_templates"] >= 10
        assert "top_templates" in stats

    def test_disabled_library(self, tmp_path):
        lib = AppActionLibrary(db_path=str(tmp_path / "disabled.db"), enabled=False)
        results = lib.suggest_with_confidence("ModuleNotFoundError: No module named 'x'", "terminal")
        assert results == []
        lib.close()


class TestScoredAction:
    def test_to_dict(self):
        t = ActionTemplate(template_id="t1", app_category="ide", trigger_pattern=".*", command_template="cmd")
        sa = ScoredAction(template=t, command="resolved_cmd", confidence=0.85, auto_execute=True)
        d = sa.to_dict()
        assert d["command"] == "resolved_cmd"
        assert d["confidence"] == 0.85
        assert d["auto_execute"] is True
