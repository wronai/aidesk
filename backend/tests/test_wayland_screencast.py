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

    def test_stop_no_pipeline(self):
        capture = ScreenCastCapture()
        capture.pipeline = None
        capture.loop = MagicMock()
        capture.loop.is_running.return_value = True

        # Should not raise
        capture.stop()
        capture.loop.quit.assert_called()


# ===== Session Management Tests =====

class TestSessionManagement:
    def test_next_token_increments(self):
        capture = ScreenCastCapture()
        capture.sender_name = "test_sender"

        t1 = capture._next_token()
        t2 = capture._next_token()
        t3 = capture._next_token()

        assert t1 == "aidesk_1"
        assert t2 == "aidesk_2"
        assert t3 == "aidesk_3"

    def test_on_session_created_success(self):
        capture = ScreenCastCapture()
        capture.loop = MagicMock()
        capture._select_sources = MagicMock()

        # Mock successful response with session handle
        mock_results = MagicMock()
        mock_variant = MagicMock()
        mock_variant.get_string.return_value = "/org/freedesktop/portal/desktop/session/123"
        mock_results.lookup_value.return_value = mock_variant

        capture._on_session_created(0, mock_results)

        assert capture.session_handle == "/org/freedesktop/portal/desktop/session/123"
        capture._select_sources.assert_called_once()
        capture.loop.quit.assert_not_called()

    def test_on_session_created_failure_response(self):
        capture = ScreenCastCapture()
        capture.loop = MagicMock()
        capture._write_status = MagicMock()

        capture._on_session_created(1, None)

        capture._write_status.assert_called_with({"state": "error", "error": "CreateSession denied"})
        capture.loop.quit.assert_called_once()

    def test_on_session_created_missing_handle(self):
        capture = ScreenCastCapture()
        capture.loop = MagicMock()
        capture._write_status = MagicMock()

        mock_results = MagicMock()
        mock_results.lookup_value.return_value = None

        capture._on_session_created(0, mock_results)

        capture.loop.quit.assert_called_once()

    def test_on_sources_selected_success(self):
        capture = ScreenCastCapture()
        capture._start_session = MagicMock()

        capture._on_sources_selected(0, None)

        capture._start_session.assert_called_once()

    def test_on_sources_selected_failure(self):
        capture = ScreenCastCapture()
        capture.loop = MagicMock()
        capture._write_status = MagicMock()

        capture._on_sources_selected(1, None)

        capture._write_status.assert_called_with({"state": "error", "error": "Source selection denied"})
        capture.loop.quit.assert_called_once()


# ===== Pipeline Tests =====

class TestPipeline:
    def test_start_pipeline_creates_pipeline(self):
        capture = ScreenCastCapture()
        capture.node_id = 123
        capture._log = MagicMock()

        with patch.object(wayland_screencast.Gst, 'parse_launch') as mock_parse:
            mock_pipeline = MagicMock()
            mock_sink = MagicMock()
            mock_pipeline.get_by_name.return_value = mock_sink
            mock_parse.return_value = mock_pipeline

            capture._start_pipeline()

            mock_parse.assert_called_once()
            assert capture.pipeline == mock_pipeline
            mock_sink.connect.assert_called_once_with('new-sample', capture._on_new_sample)

    def test_on_pipeline_error(self):
        capture = ScreenCastCapture()
        capture._write_status = MagicMock()
        capture.loop = MagicMock()
        capture._log = MagicMock()

        mock_bus = MagicMock()
        mock_msg = MagicMock()
        # Return a proper GLib.Error-like object with .message attribute
        mock_error = MagicMock()
        mock_error.message = "Test error"
        mock_msg.parse_error.return_value = (mock_error, "debug info")

        capture._on_pipeline_error(mock_bus, mock_msg)

        # The first call writes error, second call (from stop()) writes stopped
        capture._write_status.assert_any_call({"state": "error", "error": "Test error"})
        capture.loop.quit.assert_called_once()

    def test_on_pipeline_eos(self):
        capture = ScreenCastCapture()
        capture.loop = MagicMock()

        capture._on_pipeline_eos(None, None)

        capture.loop.quit.assert_called_once()


# ===== Main Function Tests =====

class TestMain:
    def test_main_creates_capture_and_starts(self):
        with patch.object(wayland_screencast, 'ScreenCastCapture') as mock_cls:
            mock_capture = MagicMock()
            mock_cls.return_value = mock_capture

            # Mock signal handler setup
            with patch('signal.signal') as mock_signal:
                wayland_screencast.main()

                mock_cls.assert_called_once()
                mock_capture.start.assert_called_once()
                mock_signal.assert_called()


# ===== Status and Logging Tests =====

class TestStatusAndLogging:
    def test_log_function(self, capsys):
        # _log is a static method on ScreenCastCapture, writes to stdout
        import sys
        old_stdout = sys.stdout
        sys.stdout = __import__('io').StringIO()
        try:
            ScreenCastCapture._log("Test message")
            output = sys.stdout.getvalue()
            assert "Test message" in output
            assert "[screencast]" in output
        finally:
            sys.stdout = old_stdout

    def test_start_method_logs_and_creates_session(self):
        capture = ScreenCastCapture()
        capture._log = MagicMock()
        capture._write_status = MagicMock()
        capture._create_session = MagicMock()
        capture.loop = MagicMock()

        # Run start but don't block
        def side_effect():
            pass  # Don't actually run the loop
        capture.loop.run = side_effect

        capture.start()

        capture._log.assert_any_call("Starting Wayland screen capture daemon")
        capture._write_status.assert_called_with({"state": "starting"})
        capture._create_session.assert_called_once()


# ===== Session Retry Tests =====

class TestSessionRetry:
    def test_on_session_started_invalid_token_retries(self):
        capture = ScreenCastCapture()
        capture.restore_token = "invalid_token"
        capture._save_restore_token = MagicMock()
        capture._create_session = MagicMock()
        capture.loop = MagicMock()

        capture._on_session_started(1, None)  # response != 0 means failure

        assert capture.restore_token is None
        capture._save_restore_token.assert_called_once_with(None)
        capture._create_session.assert_called_once()
        capture.loop.quit.assert_not_called()

    def test_on_session_started_success_saves_token(self):
        capture = ScreenCastCapture()
        capture._start_pipeline = MagicMock()
        capture._save_restore_token = MagicMock()
        capture.loop = MagicMock()

        mock_results = MagicMock()
        mock_token_variant = MagicMock()
        mock_token_variant.get_string.return_value = "new_restore_token_xyz"

        # Mock streams
        mock_stream = MagicMock()
        # First call for node_id, second for props
        mock_stream.get_child_value.side_effect = [
            MagicMock(get_uint32=lambda: 42),  # node_id at index 0
            MagicMock(lookup_value=lambda key, _: None),  # props at index 1
        ]
        mock_streams = MagicMock()
        mock_streams.n_children.return_value = 1
        mock_streams.get_child_value.return_value = mock_stream
        
        def lookup_side_effect(key, _):
            if key == 'restore_token':
                return mock_token_variant
            if key == 'streams':
                return mock_streams
            return None
        mock_results.lookup_value.side_effect = lookup_side_effect

        capture._on_session_started(0, mock_results)

        capture._save_restore_token.assert_called_once_with("new_restore_token_xyz")
        assert capture.restore_token == "new_restore_token_xyz"
        assert capture.node_id == 42
        capture._start_pipeline.assert_called_once()

    def test_on_session_started_no_streams_quits(self):
        capture = ScreenCastCapture()
        capture._write_status = MagicMock()
        capture.loop = MagicMock()

        mock_results = MagicMock()
        mock_streams = None
        
        def lookup_side_effect(key, _):
            if key == 'streams':
                return mock_streams
            return None
        mock_results.lookup_value.side_effect = lookup_side_effect

        capture._on_session_started(0, mock_results)

        capture.loop.quit.assert_called_once()

