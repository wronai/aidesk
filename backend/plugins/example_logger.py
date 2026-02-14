"""
Example Plugin: Event Logger

Demonstrates the plugin interface by subscribing to pipeline events
and logging them. This is a reference implementation for plugin authors.

Usage:
    Place this file in backend/plugins/ and restart the server.
    The plugin will automatically be discovered and loaded by PluginLoader.
"""
import structlog

logger = structlog.get_logger()


class Plugin:
    """Logs selected pipeline events to structured log output."""

    name = "example_logger"
    version = "0.1.0"
    enabled = True

    def __init__(self):
        self._event_count = 0

    def register(self, bus, app_state):
        """Subscribe to pipeline events and log them."""
        bus.subscribe("pipeline.analysis", self._on_analysis)
        bus.subscribe("pipeline.context_built", self._on_context)
        logger.info("Plugin registered", plugin=self.name)

    async def _on_analysis(self, event):
        self._event_count += 1
        data = getattr(event, "data", {}) or {}
        logger.debug(
            "Pipeline analysis event",
            plugin=self.name,
            event_count=self._event_count,
            tokens=data.get("tokens"),
        )

    async def _on_context(self, event):
        self._event_count += 1
        data = getattr(event, "data", {}) or {}
        logger.debug(
            "Context built",
            plugin=self.name,
            context_length=data.get("context_length"),
            cached=data.get("cached", False),
        )

    async def shutdown(self):
        logger.info("Plugin shutdown", plugin=self.name, total_events=self._event_count)
