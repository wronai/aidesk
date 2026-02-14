"""
Plugin Loader — Discovers and initializes plugins.
"""
import asyncio
import importlib
import os
import pkgutil
import sys
from types import ModuleType
from typing import Dict, List, Type, Any, Optional

import structlog

from event_bus import EventBus
from plugins.interface import PluginInterface

logger = structlog.get_logger()


class PluginLoader:
    """
    Manages lifecycle of plugins: discovery, loading, registration, shutdown.
    """

    def __init__(self, plugin_dir: str = "plugins", bus: EventBus = None, app_state: Dict[str, Any] = None):
        self.plugin_dir = plugin_dir
        self.bus = bus
        self.app_state = app_state
        self.loaded_plugins: Dict[str, PluginInterface] = {}

    def discover_and_load(self):
        """
        Scan plugin directory and load all valid plugins.
        """
        if not os.path.isdir(self.plugin_dir):
            logger.info("Plugin directory not found, skipping", path=self.plugin_dir)
            return

        # Add plugin dir to sys.path to allow imports
        abs_path = os.path.abspath(self.plugin_dir)
        if abs_path not in sys.path:
            sys.path.insert(0, abs_path)

        count = 0
        for _, name, _ in pkgutil.iter_modules([abs_path]):
            try:
                module = importlib.import_module(name)
                plugin = self._instantiate_plugin(module)
                if plugin:
                    self._register_plugin(plugin)
                    count += 1
            except Exception as e:
                logger.error("Failed to load plugin", name=name, error=str(e))

        logger.info("Plugin discovery complete", loaded=count)

    def _instantiate_plugin(self, module: ModuleType) -> Optional[PluginInterface]:
        """Find and instantiate the Plugin class in a module."""
        if not hasattr(module, "Plugin"):
            return None
        
        cls = getattr(module, "Plugin")
        try:
            # Check if it looks like a plugin (duck typing sufficient here)
            if not hasattr(cls, "register"):
                logger.warning("Plugin class missing register method", module=module.__name__)
                return None
            
            instance = cls()
            return instance
        except Exception as e:
            logger.error("Failed to instantiate Plugin class", module=module.__name__, error=str(e))
            return None

    def _register_plugin(self, plugin: PluginInterface):
        """Register a plugin instance."""
        if not plugin.enabled:
            return

        try:
            plugin.register(self.bus, self.app_state)
            self.loaded_plugins[plugin.name] = plugin
            logger.info("Plugin loaded", name=plugin.name, version=plugin.version)
        except Exception as e:
            logger.error("Plugin registration failed", name=plugin.name, error=str(e))

    async def shutdown(self):
        """Shutdown all plugins."""
        for name, plugin in self.loaded_plugins.items():
            try:
                if hasattr(plugin, "shutdown") and asyncio.iscoroutinefunction(plugin.shutdown):
                    await plugin.shutdown()
            except Exception as e:
                logger.error("Plugin shutdown failed", name=name, error=str(e))
