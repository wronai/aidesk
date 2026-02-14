"""
Example Plugin: Window Change Notifier

Demonstrates a plugin that reacts to window focus changes by emitting
a custom event. This is a reference implementation showing how plugins
can extend the system with new behavior via the EventBus.

Usage:
    Place this file in backend/plugins/ and restart the server.
    The plugin will automatically be discovered and loaded by PluginLoader.
"""
import structlog

from event_bus import Event, EventBus

logger = structlog.get_logger()


class Plugin:
    """Emits a custom event when the user switches between app categories."""

    name = "example_window_notifier"
    version = "0.1.0"
    enabled = True

    def __init__(self):
        self._bus = None
        self._last_category = None
        self._switch_count = 0

    def register(self, bus, app_state):
        """Subscribe to window scan events to detect category changes."""
        self._bus = bus
        bus.subscribe("pipeline.window_detected", self._on_window)
        logger.info("Plugin registered", plugin=self.name)

    async def _on_window(self, event):
        data = getattr(event, "data", {}) or {}
        category = data.get("category", "unknown")

        if self._last_category and category != self._last_category:
            self._switch_count += 1
            logger.info(
                "App category switch detected",
                plugin=self.name,
                from_category=self._last_category,
                to_category=category,
                total_switches=self._switch_count,
            )
            # Emit a custom event that other plugins or the ReadModel can consume
            if self._bus:
                await self._bus.publish(Event(
                    type="plugin.window_category_changed",
                    data={
                        "from": self._last_category,
                        "to": category,
                        "switch_count": self._switch_count,
                    },
                    source=self.name,
                ))

        self._last_category = category

    async def shutdown(self):
        logger.info(
            "Plugin shutdown",
            plugin=self.name,
            total_switches=self._switch_count,
        )
