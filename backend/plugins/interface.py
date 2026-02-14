"""
Plugin Interface — Extensibility contract for AI Desktop Assistant.

Plugins allow extending functionality without modifying core code.
They interact primarily via the EventBus (subscribing to events, emitting new ones).
"""
from typing import Protocol, Dict, Any, Optional
from event_bus import EventBus


class PluginInterface(Protocol):
    """
    Protocol that all plugins must implement.
    """
    name: str
    version: str = "0.1.0"
    enabled: bool = True

    def register(self, bus: EventBus, app_state: Dict[str, Any]) -> None:
        """
        Register the plugin with the system.
        
        Args:
            bus: The system EventBus for pub/sub.
            app_state: Read-only access to application state (e.g. for inspection).
        """
        ...

    async def shutdown(self) -> None:
        """
        Cleanup resources on system shutdown.
        """
        ...
