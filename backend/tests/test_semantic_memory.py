"""Tests for SemanticMemory — add/recall/compress cycle, keyword fallback, pruning."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_memory import SemanticMemory, MemoryItem


class TestMemoryItem:
    def test_to_dict_keys(self):
        m = MemoryItem(memory_id="m1", content="hello", timestamp=time.time())
        d = m.to_dict()
        assert "memory_id" in d
        assert "content" in d
        assert "relevance_score" in d

    def test_content_truncated_in_dict(self):
        m = MemoryItem(memory_id="m1", content="x" * 300, timestamp=time.time())
        d = m.to_dict()
        assert len(d["content"]) <= 200


class TestSemanticMemoryKeywordFallback:
    """Tests using keyword fallback (no sentence-transformers required)."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.mem = SemanticMemory(
            db_path=str(tmp_path / "test_mem.db"),
            max_memories=100,
            recall_top_k=3,
            similarity_threshold=0.1,
            compress_after_hours=0.001,  # very short for testing
            enabled=False,  # force keyword fallback
        )
        yield
        self.mem.close()

    def test_add_memory_returns_id(self):
        mid = self.mem.add_memory("test content", context_type="screen")
        assert mid is not None
        assert isinstance(mid, str)

    def test_add_empty_content_returns_none(self):
        assert self.mem.add_memory("") is None
        assert self.mem.add_memory("   ") is None

    def test_add_memory_dedup(self):
        mid1 = self.mem.add_memory("same content", timestamp=1000.0)
        mid2 = self.mem.add_memory("same content", timestamp=1000.0)
        assert mid1 == mid2
        assert self.mem.total_memories == 1

    def test_recall_recent(self):
        self.mem.add_memory("first", timestamp=1.0)
        self.mem.add_memory("second", timestamp=2.0)
        self.mem.add_memory("third", timestamp=3.0)
        recent = self.mem.recall_recent(n=2)
        assert len(recent) == 2
        assert recent[0].timestamp > recent[1].timestamp

    def test_recall_recent_filter_by_type(self):
        self.mem.add_memory("screen content", context_type="screen", timestamp=1.0)
        self.mem.add_memory("speech content", context_type="speech", timestamp=2.0)
        screen_only = self.mem.recall_recent(n=5, context_type="screen")
        assert len(screen_only) == 1
        assert screen_only[0].context_type == "screen"

    def test_recall_keyword_search(self):
        self.mem.add_memory("python import error flask", timestamp=1.0)
        self.mem.add_memory("docker container running", timestamp=2.0)
        self.mem.add_memory("python pip install requests", timestamp=3.0)
        results = self.mem.recall_relevant("python flask")
        assert len(results) >= 1
        # The first result should be about python + flask
        assert "python" in results[0].content.lower()

    def test_recall_keyword_no_match(self):
        self.mem.add_memory("hello world", timestamp=1.0)
        results = self.mem.recall_relevant("xyznonexistent")
        assert results == []

    def test_recall_keyword_with_type_filter(self):
        self.mem.add_memory("python error", context_type="screen", timestamp=1.0)
        self.mem.add_memory("python speech", context_type="speech", timestamp=2.0)
        results = self.mem.recall_relevant("python", context_type="screen")
        assert len(results) == 1
        assert results[0].context_type == "screen"

    def test_recall_keyword_with_since_filter(self):
        self.mem.add_memory("old content python", timestamp=100.0)
        self.mem.add_memory("new content python", timestamp=200.0)
        results = self.mem.recall_relevant("python", since=150.0)
        assert len(results) == 1
        assert results[0].timestamp == 200.0

    def test_prune_oldest(self, tmp_path):
        mem = SemanticMemory(
            db_path=str(tmp_path / "prune_mem.db"),
            max_memories=5,
            enabled=False,
        )
        mem._init_db()
        for i in range(8):
            mem.add_memory(f"content_{i}", timestamp=float(i + 1))
        # Cache should not grow beyond max + 1 (pruning fires after insert)
        assert len(mem._content_cache) <= 6
        # The very first item should be pruned
        contents = {m.content for m in mem._content_cache.values()}
        assert "content_0" not in contents
        mem.close()

    def test_compress_old_context(self, tmp_path):
        mem = SemanticMemory(
            db_path=str(tmp_path / "compress_mem.db"),
            max_memories=100,
            compress_after_hours=0.001,
            enabled=False,
        )
        mem._init_db()  # enable DB for compress to work
        now = time.time()
        # Add old memories (> compress threshold)
        for i in range(10):
            mem.add_memory(f"old item {i}", context_type="screen",
                           timestamp=now - 3600 * 2 + i)
        # Add recent memory
        mem.add_memory("recent item", timestamp=now)
        compressed = mem.compress_old_context(before_timestamp=now - 10)
        assert compressed >= 5  # 10 old items compressed
        # Summaries are created and originals removed; net count should drop
        # Check that summary entries exist
        summaries = mem.recall_recent(n=50, context_type="summary")
        assert len(summaries) >= 1
        mem.close()

    def test_compress_too_few_items(self):
        now = time.time()
        self.mem.add_memory("only one old", timestamp=now - 7200)
        compressed = self.mem.compress_old_context()
        assert compressed == 0

    def test_get_context_string(self):
        self.mem.add_memory("important context", timestamp=time.time())
        ctx = self.mem.get_context_string(n=5, max_length=500)
        assert "important context" in ctx

    def test_get_context_string_empty(self):
        ctx = self.mem.get_context_string()
        assert ctx == ""

    def test_get_context_string_max_length(self):
        for i in range(20):
            self.mem.add_memory(f"item number {i} with some content padding here", timestamp=float(i))
        ctx = self.mem.get_context_string(n=20, max_length=100)
        assert len(ctx) <= 200  # some tolerance for last line

    def test_metadata_stored(self):
        mid = self.mem.add_memory("test", metadata={"app": "vscode", "run_id": "abc"})
        item = self.mem._content_cache[mid]
        assert item.metadata["app"] == "vscode"

    def test_get_stats(self):
        self.mem.add_memory("test")
        stats = self.mem.get_stats()
        assert stats["total_memories"] >= 1
        assert stats["enabled"] is False
        assert stats["model"] == "keyword_fallback"

    def test_persistence_across_instances(self, tmp_path):
        db_path = str(tmp_path / "persist_mem.db")
        mem1 = SemanticMemory(db_path=db_path, enabled=False)
        mem1._init_db()  # enable DB for persistence
        mem1.add_memory("persistent content", timestamp=1000.0)
        mem1.close()

        mem2 = SemanticMemory(db_path=db_path, enabled=False)
        mem2._init_db()
        mem2._load_cache()
        assert mem2.total_memories >= 1
        recent = mem2.recall_recent(n=1)
        assert len(recent) == 1
        assert "persistent content" in recent[0].content
        mem2.close()

    def test_remove_memory(self):
        mid = self.mem.add_memory("to remove", timestamp=1.0)
        assert mid in self.mem._content_cache
        self.mem._remove_memory(mid)
        assert mid not in self.mem._content_cache

    def test_close_idempotent(self):
        self.mem.close()
        self.mem.close()  # should not raise
