"""
Plugin Loader — Discovers and initializes plugins.
"""
import asyncio
import importlib
import os
import pkgutil
import sys
from types import ModuleType
from typing import Any, Dict, Optional, Set

import nfo
import structlog

from event_bus import EventBus
from plugins.interface import PluginInterface

logger = structlog.get_logger()


class PluginLoader:
    """
    Manages lifecycle of plugins: discovery, loading, registration, shutdown.
    """

    _INTERNAL_MODULES = {"__init__", "interface", "loader"}

    def __init__(self, plugin_dir: str = "plugins", bus: EventBus = None, app_state: Dict[str, Any] = None):
        # Resolve default relative path to backend/plugins regardless of process CWD.
        if plugin_dir == "plugins":
            plugin_dir = os.path.dirname(__file__)
        self.plugin_dir = plugin_dir
        self.bus = bus
        self.app_state = app_state
        self.loaded_plugins: Dict[str, PluginInterface] = {}
        self._loaded_modules: Set[str] = set()

    def _import_module(self, name: str) -> ModuleType:
        """Import a plugin module by name (extracted for easier testing/mocking)."""
        return importlib.import_module(name)

    @nfo.log_call(level="INFO")
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

        module_names = [
            name
            for _, name, _ in pkgutil.iter_modules([abs_path])
            if name not in self._INTERNAL_MODULES and not name.startswith("_")
        ]

        count = 0
        skipped = 0
        for name in module_names:
            if name in self._loaded_modules:
                skipped += 1
                continue

            try:
                module = self._import_module(name)
                plugin = self._instantiate_plugin(module)
                if plugin and self._register_plugin(plugin):
                    self._loaded_modules.add(name)
                    count += 1
            except Exception as e:
                logger.error("Failed to load plugin", name=name, error=str(e))

        logger.info(
            "Plugin discovery complete",
            loaded=count,
            scanned=len(module_names),
            skipped=skipped,
        )

    def _instantiate_plugin(self, module: ModuleType) -> Optional[PluginInterface]:
        """Find and instantiate the Plugin class in a module."""
        if not hasattr(module, "Plugin"):
            return None
        
        cls = getattr(module, "Plugin")
        try:
            # Check if it looks like a plugin (duck typing sufficient here)
            if not callable(getattr(cls, "register", None)):
                logger.warning("Plugin class missing register method", module=module.__name__)
                return None
            
            instance = cls()
            return instance
        except Exception as e:
            logger.error("Failed to instantiate Plugin class", module=module.__name__, error=str(e))
            return None

    def _register_plugin(self, plugin: PluginInterface) -> bool:
        """Register a plugin instance."""
        if not getattr(plugin, "enabled", True):
            return False

        plugin_name = getattr(plugin, "name", plugin.__class__.__name__)
        if plugin_name in self.loaded_plugins:
            logger.warning("Plugin already loaded, skipping duplicate", name=plugin_name)
            return False

        try:
            plugin.register(self.bus, self.app_state)
            self.loaded_plugins[plugin_name] = plugin
            logger.info("Plugin loaded", name=plugin_name, version=getattr(plugin, "version", "unknown"))
            return True
        except Exception as e:
            logger.error("Plugin registration failed", name=plugin_name, error=str(e))
            return False

    @nfo.log_call(level="INFO")
    async def shutdown(self):
        """Shutdown all plugins."""
        for name, plugin in self.loaded_plugins.items():
            try:
                if hasattr(plugin, "shutdown") and asyncio.iscoroutinefunction(plugin.shutdown):
                    await plugin.shutdown()
            except Exception as e:
                logger.error("Plugin shutdown failed", name=name, error=str(e))
