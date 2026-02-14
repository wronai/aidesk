"""Tests for PluginLoader and lifecycle."""
import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.loader import PluginLoader
from plugins.interface import PluginInterface


# ===== Mocks =====

class MockPlugin:
    def __init__(self):
        self.name = "mock_plugin"
        self.version = "1.0.0"
        self.enabled = True
        self.registered = False
        self.shutdown_called = False

    def register(self, bus, app_state):
        self.registered = True
        self.bus = bus
        self.state = app_state

    async def shutdown(self):
        self.shutdown_called = True


class MockDisabledPlugin(MockPlugin):
    def __init__(self):
        super().__init__()
        self.name = "disabled_plugin"
        self.enabled = False


class MockBadPlugin:
    # Missing register method
    pass


# ===== Tests =====

class TestPluginLoader:
    def test_init(self):
        bus = MagicMock()
        state = {}
        loader = PluginLoader(plugin_dir="plugins", bus=bus, app_state=state)
        assert loader.bus is bus
        assert loader.app_state is state
        assert loader.loaded_plugins == {}

    def test_default_plugin_dir_resolves_to_backend_plugins(self):
        loader = PluginLoader()
        expected = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")
        assert loader.plugin_dir == expected

    @patch("pkgutil.iter_modules")
    @patch("importlib.import_module")
    def test_discover_and_load_success(self, mock_import, mock_iter_modules):
        # Mock pkgutil to find one module "my_plugin"
        mock_iter_modules.return_value = [(None, "my_plugin", None)]
        
        # Mock importlib to return a module with a Plugin class
        mock_module = MagicMock()
        mock_module.Plugin = MockPlugin
        mock_import.return_value = mock_module

        loader = PluginLoader()
        loader.discover_and_load()

        assert "mock_plugin" in loader.loaded_plugins
        plugin = loader.loaded_plugins["mock_plugin"]
        assert plugin.registered is True
        assert plugin.bus is loader.bus

    @patch("pkgutil.iter_modules")
    @patch("importlib.import_module")
    def test_discover_and_load_disabled(self, mock_import, mock_iter_modules):
        mock_iter_modules.return_value = [(None, "disabled_plugin", None)]
        
        mock_module = MagicMock()
        mock_module.Plugin = MockDisabledPlugin
        mock_import.return_value = mock_module

        loader = PluginLoader()
        loader.discover_and_load()

        assert "disabled_plugin" not in loader.loaded_plugins

    @patch("pkgutil.iter_modules")
    @patch("importlib.import_module")
    def test_discover_and_load_invalid_class(self, mock_import, mock_iter_modules):
        mock_iter_modules.return_value = [(None, "bad_plugin", None)]
        
        mock_module = MagicMock()
        mock_module.Plugin = MockBadPlugin
        mock_import.return_value = mock_module

        loader = PluginLoader()
        loader.discover_and_load()

        assert len(loader.loaded_plugins) == 0

    @patch("pkgutil.iter_modules")
    @patch("importlib.import_module")
    def test_discover_and_load_no_plugin_class(self, mock_import, mock_iter_modules):
        mock_iter_modules.return_value = [(None, "no_class_plugin", None)]
        
        mock_module = MagicMock()
        del mock_module.Plugin  # Ensure no Plugin class
        mock_import.return_value = mock_module

        loader = PluginLoader()
        loader.discover_and_load()

        assert len(loader.loaded_plugins) == 0

    @pytest.mark.asyncio
    async def test_shutdown(self):
        loader = PluginLoader()
        plugin = MockPlugin()
        loader.loaded_plugins["mock"] = plugin
        
        await loader.shutdown()
        assert plugin.shutdown_called is True

    @pytest.mark.asyncio
    async def test_shutdown_handles_errors(self):
        loader = PluginLoader()
        plugin = MockPlugin()
        plugin.shutdown = AsyncMock(side_effect=RuntimeError("fail"))
        loader.loaded_plugins["mock"] = plugin
        
        # Should not raise
        await loader.shutdown()
        plugin.shutdown.assert_called_once()
