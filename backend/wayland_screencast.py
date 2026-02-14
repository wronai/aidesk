#!/usr/bin/env python3
"""
Wayland screen capture daemon using ScreenCast portal + PipeWire + GStreamer.

Must run with SYSTEM Python (requires gi/PyGObject, not available in venv).
Continuously captures the Wayland compositor screen and saves frames to a
shared JPEG file that the main capture module reads.

Usage:
    python3 wayland_screencast.py

Environment:
    AIDESK_FRAME_PATH   - Output file path (default: /tmp/aidesk_wayland_frame.jpg)
    AIDESK_FRAME_QUALITY - JPEG quality 1-100 (default: 80)
    AIDESK_MAX_FPS       - Max frames per second (default: 1.0)
"""
import os
import sys
import signal
import time
import json

import gi
gi.require_version('Gst', '1.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gst, GLib, Gio

OUTPUT_PATH = os.environ.get('AIDESK_FRAME_PATH', '/tmp/aidesk_wayland_frame.jpg')
QUALITY = int(os.environ.get('AIDESK_FRAME_QUALITY', '80'))
MAX_FPS = float(os.environ.get('AIDESK_MAX_FPS', '1.0'))
STATUS_PATH = OUTPUT_PATH + '.status'

SCREENCAST_IFACE = 'org.freedesktop.portal.ScreenCast'
REQUEST_IFACE = 'org.freedesktop.portal.Request'
PORTAL_BUS = 'org.freedesktop.portal.Desktop'
PORTAL_PATH = '/org/freedesktop/portal/desktop'


class ScreenCastCapture:
    """Captures Wayland screen via ScreenCast portal + PipeWire + GStreamer."""

    def __init__(self):
        Gst.init(None)
        self.loop = GLib.MainLoop()
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.session_handle = None
        self.node_id = None
        self.pipeline = None
        self.request_count = 0
        self.last_save_time = 0
        self.frame_count = 0
        self.last_width = 0
        self.last_height = 0

        # Unique sender name for request paths
        self.sender_name = (
            self.connection.get_unique_name().replace('.', '_').lstrip(':')
        )

    def _next_token(self):
        self.request_count += 1
        return f'aidesk_{self.request_count}'

    def _subscribe_response(self, token, callback):
        """Subscribe to portal Response signal for a specific request."""
        request_path = (
            f'{PORTAL_PATH}/request/{self.sender_name}/{token}'
        )

        def on_signal(conn, sender, obj_path, iface, sig_name, params):
            response = params.get_child_value(0).get_uint32()
            results = params.get_child_value(1)
            callback(response, results)

        self.connection.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE,
            'Response',
            request_path,
            None,
            Gio.DBusSignalFlags.NO_MATCH_RULE,
            on_signal,
        )

    def _portal_call(self, method, args, variant_type='(o)'):
        """Make a synchronous portal D-Bus call."""
        return self.connection.call_sync(
            PORTAL_BUS,
            PORTAL_PATH,
            SCREENCAST_IFACE,
            method,
            args,
            GLib.VariantType.new(variant_type),
            Gio.DBusCallFlags.NONE,
            30000,  # 30s timeout
            None,
        )

    def start(self):
        """Start screen capture (blocks until stopped)."""
        self._log("Starting Wayland screen capture daemon")
        self._log(f"Output: {OUTPUT_PATH}, Quality: {QUALITY}, MaxFPS: {MAX_FPS}")
        self._write_status({"state": "starting"})
        self._create_session()
        try:
            self.loop.run()
        except Exception as e:
            self._log(f"Main loop error: {e}")
            self._write_status({"state": "error", "error": str(e)})

    def _create_session(self):
        token = self._next_token()
        self._subscribe_response(token, self._on_session_created)

        self._portal_call(
            'CreateSession',
            GLib.Variant('(a{sv})', [{
                'handle_token': GLib.Variant('s', token),
                'session_handle_token': GLib.Variant('s', 'aidesk_capture'),
            }]),
        )
        self._log("CreateSession called, waiting for user permission...")

    def _on_session_created(self, response, results):
        if response != 0:
            self._log(f"CreateSession failed (response={response})")
            self._write_status({"state": "error", "error": "CreateSession denied"})
            self.loop.quit()
            return

        variant = results.lookup_value('session_handle', GLib.VariantType.new('s'))
        if variant:
            self.session_handle = variant.get_string()
        else:
            self._log("No session_handle in response")
            self.loop.quit()
            return

        self._log(f"Session created: {self.session_handle}")
        self._select_sources()

    def _select_sources(self):
        token = self._next_token()
        self._subscribe_response(token, self._on_sources_selected)

        self._portal_call(
            'SelectSources',
            GLib.Variant('(oa{sv})', [
                self.session_handle,
                {
                    'handle_token': GLib.Variant('s', token),
                    'types': GLib.Variant('u', 1),       # 1 = MONITOR
                    'multiple': GLib.Variant('b', False),
                    'persist_mode': GLib.Variant('u', 2), # 2 = persist until revoked
                },
            ]),
        )
        self._log("SelectSources called")

    def _on_sources_selected(self, response, results):
        if response != 0:
            self._log(f"SelectSources failed (response={response})")
            self._write_status({"state": "error", "error": "Source selection denied"})
            self.loop.quit()
            return

        self._log("Sources selected, starting session...")
        self._start_session()

    def _start_session(self):
        token = self._next_token()
        self._subscribe_response(token, self._on_session_started)

        self._portal_call(
            'Start',
            GLib.Variant('(osa{sv})', [
                self.session_handle,
                '',  # parent window identifier
                {
                    'handle_token': GLib.Variant('s', token),
                },
            ]),
        )
        self._log("Start called, waiting for stream...")

    def _on_session_started(self, response, results):
        if response != 0:
            self._log(f"Start failed (response={response})")
            self._write_status({"state": "error", "error": "Start denied"})
            self.loop.quit()
            return

        # Extract PipeWire node ID from streams
        streams = results.lookup_value('streams', None)
        if not streams or streams.n_children() == 0:
            self._log("No streams in Start response")
            self.loop.quit()
            return

        stream = streams.get_child_value(0)
        self.node_id = stream.get_child_value(0).get_uint32()

        # Try to get stream properties (size, source_type)
        props = stream.get_child_value(1)
        size_variant = props.lookup_value('size', None)
        if size_variant:
            w = size_variant.get_child_value(0).get_int32()
            h = size_variant.get_child_value(1).get_int32()
            self._log(f"Stream size: {w}x{h}")

        self._log(f"PipeWire node ID: {self.node_id}")
        self._start_pipeline()

    def _start_pipeline(self):
        """Start GStreamer pipeline to capture frames from PipeWire."""
        pipeline_str = (
            f'pipewiresrc path={self.node_id} do-timestamp=true keepalive-time=1000 '
            f'! videoconvert '
            f'! video/x-raw,format=RGB '
            f'! appsink name=sink emit-signals=true max-buffers=2 drop=true'
        )

        self._log(f"GStreamer pipeline: {pipeline_str}")
        self.pipeline = Gst.parse_launch(pipeline_str)

        # Connect to appsink signals
        sink = self.pipeline.get_by_name('sink')
        sink.connect('new-sample', self._on_new_sample)

        # Watch for pipeline errors
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_pipeline_error)
        bus.connect('message::eos', self._on_pipeline_eos)

        self.pipeline.set_state(Gst.State.PLAYING)
        self._log("Pipeline PLAYING — capturing frames")
        self._write_status({"state": "capturing", "node_id": self.node_id})

    def _on_new_sample(self, sink):
        """Called for each new frame from PipeWire."""
        now = time.time()

        # Rate limit
        if now - self.last_save_time < (1.0 / MAX_FPS):
            return Gst.FlowReturn.OK

        sample = sink.emit('pull-sample')
        if not sample:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        structure = caps.get_structure(0)

        width = structure.get_value('width')
        height = structure.get_value('height')

        success, map_info = buf.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK

        try:
            from PIL import Image
            from io import BytesIO

            img = Image.frombytes('RGB', (width, height), bytes(map_info.data))

            # Save as JPEG (atomic write)
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=QUALITY, optimize=True)

            tmp_path = OUTPUT_PATH + '.tmp'
            with open(tmp_path, 'wb') as f:
                f.write(buffer.getvalue())
            os.replace(tmp_path, OUTPUT_PATH)

            self.last_save_time = now
            self.last_width = width
            self.last_height = height
            self.frame_count += 1
            size_kb = len(buffer.getvalue()) / 1024

            if self.frame_count % 10 == 1:
                self._log(f"Frame #{self.frame_count}: {width}x{height}, {size_kb:.1f} KB")

            self._write_status({
                "state": "capturing",
                "node_id": self.node_id,
                "frame_count": self.frame_count,
                "width": width,
                "height": height,
                "size_kb": round(size_kb, 1),
                "timestamp": now,
            })

        except Exception as e:
            self._log(f"Frame save error: {e}")
        finally:
            buf.unmap(map_info)

        return Gst.FlowReturn.OK

    def _on_pipeline_error(self, bus, msg):
        err, debug = msg.parse_error()
        self._log(f"Pipeline error: {err.message} ({debug})")
        self._write_status({"state": "error", "error": err.message})
        self.stop()

    def _on_pipeline_eos(self, bus, msg):
        self._log("Pipeline EOS")
        self.stop()

    def stop(self):
        """Stop pipeline and quit main loop."""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        self._write_status({"state": "stopped"})
        if self.loop and self.loop.is_running():
            self.loop.quit()

    def _write_status(self, data):
        """Write status JSON for the main process to read."""
        try:
            data['pid'] = os.getpid()
            data['updated'] = time.time()
            with open(STATUS_PATH, 'w') as f:
                json.dump(data, f)
        except Exception:
            pass

    @staticmethod
    def _log(msg):
        print(f"[screencast] {msg}", flush=True)


def main():
    capture = ScreenCastCapture()

    def on_signal(sig, frame):
        capture._log(f"Received signal {sig}, shutting down...")
        capture.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        capture.start()
    except KeyboardInterrupt:
        capture.stop()
    except Exception as e:
        print(f"[screencast] Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
