"""Tests for Wayland screencast daemon logic (mocking system dependencies)."""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Mock gi/Gst dependencies before importing wayland_screencast
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.Gst"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()
sys.modules["gi.repository.Gio"] = MagicMock()

# Now we can import the module (it will use the mocks)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wayland_screencast
from wayland_screencast import ScreenCastCapture


# ===== Token Persistence Tests =====

class TestRestoreToken:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        # Override TOKEN_PATH to use temp dir
        token_path = str(tmp_path / "token")
        monkeypatch.setattr("wayland_screencast.TOKEN_PATH", token_path)

        # Save
        ScreenCastCapture._save_restore_token("abc-123")
        assert os.path.exists(token_path)
        assert open(token_path).read() == "abc-123"

        # Load
        loaded = ScreenCastCapture._load_restore_token()
        assert loaded == "abc-123"

    def test_save_none_deletes_file(self, tmp_path, monkeypatch):
        token_path = str(tmp_path / "token")
        monkeypatch.setattr("wayland_screencast.TOKEN_PATH", token_path)

        with open(token_path, "w") as f:
            f.write("old")

        ScreenCastCapture._save_restore_token(None)
        assert not os.path.exists(token_path)

    def test_load_missing_file_returns_none(self, tmp_path, monkeypatch):
        token_path = str(tmp_path / "missing")
        monkeypatch.setattr("wayland_screencast.TOKEN_PATH", token_path)

        assert ScreenCastCapture._load_restore_token() is None


# ===== Status File Tests =====

class TestStatusFile:
    def test_write_status(self, tmp_path, monkeypatch):
        status_path = str(tmp_path / "status.json")
        monkeypatch.setattr("wayland_screencast.STATUS_PATH", status_path)

        capture = ScreenCastCapture()
        capture._write_status({"state": "running", "fps": 60})

        assert os.path.exists(status_path)
        with open(status_path) as f:
            data = json.load(f)
            assert data["state"] == "running"
            assert data["fps"] == 60
            assert "pid" in data
            assert "updated" in data


# ===== GStreamer Logic Tests =====

class TestNewSample:
    """Test frame processing logic using mocked GStreamer components."""

    def test_rate_limiting(self):
        capture = ScreenCastCapture()
        # Mock time to be same as last_save_time
        capture.last_save_time = 1000.0
        
        with patch("time.time", return_value=1000.01):  # +10ms
            # Should skip (MAX_FPS=1.0 default means min 1s interval)
            result = capture._on_new_sample(MagicMock())
            # Return value is Gst.FlowReturn.OK (which is a mock)
            # We can't check the return value easily since Gst is mocked, 
            # but we can check if it returned early by verifying no sample pull.
            assert not capture.last_save_time == 1000.01

    @patch("time.time")
    def test_pull_sample_failure(self, mock_time, tmp_path, monkeypatch):
        mock_time.return_value = 2000.0
        output_path = str(tmp_path / "frame.jpg")
        monkeypatch.setattr("wayland_screencast.OUTPUT_PATH", output_path)

        capture = ScreenCastCapture()
        capture.last_save_time = 1000.0

        sink = MagicMock()
        sink.emit.return_value = None  # No sample available

        capture._on_new_sample(sink)
        
        # Should not update save time
        assert capture.last_save_time == 1000.0

    @patch("time.time")
    def test_successful_frame_save(self, mock_time, tmp_path, monkeypatch):
        mock_time.return_value = 2000.0
        output_path = str(tmp_path / "frame.jpg")
        monkeypatch.setattr("wayland_screencast.OUTPUT_PATH", output_path)

        capture = ScreenCastCapture()
        capture.last_save_time = 1000.0

        # Mock GStreamer sample/buffer/caps hierarchy
        sink = MagicMock()
        sample = MagicMock()
        sink.emit.return_value = sample
        
        buf = MagicMock()
        sample.get_buffer.return_value = buf
        
        caps = MagicMock()
        sample.get_caps.return_value = caps
        
        structure = MagicMock()
        caps.get_structure.return_value = structure
        structure.get_value.side_effect = lambda k: 100 if k in ("width", "height") else 0

        # Mock buffer map
        buf.map.return_value = (True, MagicMock(data=b"\x00" * (100 * 100 * 3)))

        # Mock PIL
        with patch("PIL.Image.frombytes") as mock_frombytes:
            mock_img = MagicMock()
            mock_frombytes.return_value = mock_img
            
            capture._on_new_sample(sink)

            # Verification
            assert capture.frame_count == 1
            assert capture.last_width == 100
            assert capture.last_height == 100
            assert capture.last_save_time == 2000.0
            
            # Check file atomic write
            assert os.path.exists(output_path)  # The code moves tmp to output
            mock_img.save.assert_called_once()


# ===== Lifecycle Tests =====

class TestLifecycle:
    def test_stop_stops_pipeline(self):
        capture = ScreenCastCapture()
        capture.pipeline = MagicMock()
        capture.loop = MagicMock()
        capture.loop.is_running.return_value = True

        capture.stop()

        # Should set state to NULL
        capture.pipeline.set_state.assert_called()
        # Should quit loop
        capture.loop.quit.assert_called()
