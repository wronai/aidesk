"""
AppBootstrap — testable startup/shutdown orchestration.

Extracts the monolithic lifespan() function (CC=21) into discrete,
independently testable initialization phases. Each phase is a method
that populates app_state with one component group.

Usage in server.py:
    bootstrap = AppBootstrap(app_state, broadcast_fn)
    await bootstrap.startup()
    yield
    await bootstrap.shutdown()
"""
import asyncio
import os
import time
from typing import Callable, Dict, List, Optional

import structlog

from capture import create_capture_from_env
from analyzer import create_analyzer_from_env
from ocr_engines import create_ocr_manager_from_env
from context import ContextManager
from diagnostics import AutoDiagnostics
from window_aware import create_window_manager_from_env
from app_profiles import create_profile_manager
from shell_agent import create_shell_agent_from_env
from process_scanner import create_process_scanner
from window_cropper import create_window_cropper
from event_bus import EventBus, Event, EventType, create_event_bus
from typed_events import typed_event, SystemStartupPayload, SystemShutdownPayload
from pipeline import create_pipeline, create_profile_selector
from command_handlers import CommandHandlers
from query_handlers import QueryHandlers, ReadModel
from multi_monitor import create_multi_monitor_from_env
from semantic_memory import create_semantic_memory_from_env
from action_templates import create_action_library_from_env
from ocr_post_process import create_ocr_enhancer_from_env
from predictive_engine import create_predictive_engine_from_env
from clipboard_intel import create_clipboard_manager_from_env
from cost_budget import create_cost_budget_from_env
from plugins.loader import PluginLoader

logger = structlog.get_logger()


def _init_optional(state: dict, key: str, factory, **kwargs) -> bool:
    """
    Try to initialize an optional component. Returns True on success.

    On failure, logs a warning and leaves state[key] as None.
    """
    try:
        state[key] = factory(**kwargs)
        return True
    except Exception as e:
        logger.warning(f"{key} initialization failed", error=str(e))
        return False


class AppBootstrap:
    """
    Orchestrates application startup and shutdown in testable phases.

    Phases:
        1. init_core()      — capture, OCR, analyzer (required)
        2. init_window()    — window manager, profiles, shell agent (optional)
        3. init_scanners()  — process scanner, window cropper (optional)
        4. init_tier1()     — multi-monitor, semantic memory, action templates,
                              OCR post-process, predictive engine (optional)
        5. init_pipeline()  — event bus, pipeline orchestrator, CQRS handlers
        6. start_tasks()    — screen loop, STT, diagnostics (async tasks)

    Each phase can be tested independently by providing a mock app_state dict.
    """

    def __init__(self, app_state: dict, broadcast_fn: Callable, version: str = ""):
        from settings import get_settings
        self.state = app_state
        self.broadcast = broadcast_fn
        self.version = version
        self._tasks: List[asyncio.Task] = []
        self._init_results: Dict[str, bool] = {}
        self.settings = get_settings()

    # ── Phase 1: Core (required) ──

    def init_core(self):
        """Initialize capture, OCR manager, and analyzer."""
        self.state["capture"] = create_capture_from_env(settings=self.settings)
        self.state["ocr_manager"] = create_ocr_manager_from_env(settings=self.settings)
        self.state["analyzer"] = create_analyzer_from_env(
            ocr_manager=self.state["ocr_manager"],
            settings=self.settings,
        )
        self._init_results["capture"] = True
        self._init_results["ocr_manager"] = True
        self._init_results["analyzer"] = True
        logger.info("Core components initialized")

    # ── Phase 2: Window awareness (optional) ──

    def init_window(self):
        """Initialize window manager, profile manager, and shell agent."""
        if self.settings.enable_window_aware:
            ok = _init_optional(self.state, "window_manager", create_window_manager_from_env, settings=self.settings)
            self._init_results["window_manager"] = ok
            if ok:
                logger.info("Window awareness enabled")

        self.state["profile_manager"] = create_profile_manager(settings=self.settings)
        self._init_results["profile_manager"] = True

        if self.settings.enable_shell_agent:
            ok = _init_optional(self.state, "shell_agent", create_shell_agent_from_env, settings=self.settings)
            self._init_results["shell_agent"] = ok
            if ok:
                logger.info("Shell agent enabled")

    # ── Phase 3: Process scanner & window cropper (optional) ──

    def init_scanners(self):
        """Initialize process scanner and window cropper."""
        try:
            self.state["process_scanner"] = create_process_scanner(
                window_manager=self.state.get("window_manager"),
                settings=self.settings,
            )
            self.state["window_cropper"] = create_window_cropper(
                process_scanner=self.state["process_scanner"],
                settings=self.settings,
            )
            self._init_results["process_scanner"] = True
            self._init_results["window_cropper"] = True
            logger.info("Process scanner & window cropper enabled")
        except Exception as e:
            logger.warning("Process scanner/cropper initialization failed", error=str(e))
            self._init_results["process_scanner"] = False
            self._init_results["window_cropper"] = False

    # ── Phase 4: Tier 1 modules (all optional, each independent) ──

    def init_tier1(self):
        """Initialize Tier 1 modules: multi-monitor, semantic memory, action templates, OCR post-process, predictive."""
        tier1_components = [
            ("multi_monitor", create_multi_monitor_from_env, {"settings": self.settings}),
            ("semantic_memory", create_semantic_memory_from_env, {"settings": self.settings}),
            ("action_library", create_action_library_from_env, {"settings": self.settings}),
            ("ocr_enhancer", create_ocr_enhancer_from_env, {"settings": self.settings}),
            ("predictive_engine", create_predictive_engine_from_env, {"settings": self.settings}),
            ("clipboard_manager", create_clipboard_manager_from_env, {"settings": self.settings}),
            ("cost_budget", create_cost_budget_from_env, {"settings": self.settings}),
        ]
        for key, factory, kwargs in tier1_components:
            ok = _init_optional(self.state, key, factory, **kwargs)
            self._init_results[key] = ok
            if ok:
                logger.info(f"{key} enabled")

    # ── Phase 5: Pipeline + CQRS (depends on phases 1-4) ──

    def init_pipeline(self):
        """Initialize event bus, pipeline orchestrator, CQRS handlers."""
        # Event Bus
        self.state["event_bus"] = create_event_bus(
            enable_store=True,
            db_path=os.getenv("EVENT_STORE_DB", "logs/events.db"),
        )

        # Pipeline Orchestrator
        self.state["pipeline"] = create_pipeline(
            bus=self.state["event_bus"],
            capture=self.state["capture"],
            analyzer=self.state["analyzer"],
            context_mgr=self.state["context"],
            window_mgr=self.state.get("window_manager"),
            profile_mgr=self.state.get("profile_manager"),
            shell_agent=self.state.get("shell_agent"),
            process_scanner=self.state.get("process_scanner"),
            window_cropper=self.state.get("window_cropper"),
            app_state_ref=self.state,
            multi_monitor=self.state.get("multi_monitor"),
            semantic_memory=self.state.get("semantic_memory"),
            action_library=self.state.get("action_library"),
            ocr_enhancer=self.state.get("ocr_enhancer"),
            predictive_engine=self.state.get("predictive_engine"),
            clipboard_manager=self.state.get("clipboard_manager"),
            cost_budget=self.state.get("cost_budget"),
        )

        # Profile Selector
        self.state["profile_selector"] = create_profile_selector()

        # CQRS Read Model (restore from snapshot if available)
        read_model = ReadModel()
        read_model.load_snapshot()
        self.state["read_model"] = read_model

        # Command handlers (write side)
        cmd_handlers = CommandHandlers(self.state["event_bus"], self.state)
        cmd_handlers.set_broadcast(self.broadcast)
        cmd_handlers.register_all()
        self.state["command_handlers"] = cmd_handlers

        # Query handlers (read side)
        qry_handlers = QueryHandlers(self.state["event_bus"], self.state, read_model)
        qry_handlers.register_all()
        self.state["query_handlers"] = qry_handlers

        self._init_results["event_bus"] = True
        self._init_results["pipeline"] = True
        self._init_results["read_model"] = True
        logger.info("Pipeline + CQRS initialized")

    # ── Phase 6: Plugins (optional) ──

    def init_plugins(self):
        """Discover and load plugins."""
        # Use plugins_dir from settings if available, else default to 'plugins' relative to backend
        plugin_dir = getattr(self.settings, "plugins_dir", os.path.join(os.path.dirname(__file__), "plugins"))
        
        loader = PluginLoader(
            plugin_dir=plugin_dir,
            bus=self.state.get("event_bus"),
            app_state=self.state,
        )
        loader.discover_and_load()
        self.state["plugin_loader"] = loader
        self._init_results["plugins"] = True

    # ── Phase 7: Start async tasks ──

    async def start_tasks(self, screen_loop_coro, on_transcript_cb):
        """Start screen analysis loop, STT, and diagnostics as async tasks."""
        # Emit startup event
        component_names = [
            k for k, v in self.state.items()
            if v is not None and k not in (
                "stats", "subscribers", "latest_analysis",
                "latest_transcript", "latest_window",
                "latest_organized_screen",
            )
        ]
        await self.state["event_bus"].publish(typed_event(
            EventType.SYSTEM_STARTUP,
            SystemStartupPayload(
                version=self.version,
                components=component_names,
            ),
            source="bootstrap",
        ))

        # Screen analysis loop
        screen_task = asyncio.create_task(screen_loop_coro())
        self._tasks.append(screen_task)

        # STT
        if self.settings.enable_stt:
            try:
                from stt import create_stt_from_env
                create_stt = create_stt_from_env
            except (ImportError, OSError) as e:
                logger.warning("STT module unavailable", error=str(e))
                create_stt = None

            if create_stt:
                try:
                    self.state["stt"] = create_stt(settings=self.settings)
                    if self.state["stt"]:
                        stt_task = asyncio.create_task(
                            self.state["stt"].start(on_transcript_cb)
                        )
                        self._tasks.append(stt_task)
                        logger.info("STT enabled and started")
                except Exception as e:
                    logger.warning("STT initialization failed", error=str(e))

        # Auto-diagnostics
        diag_interval = self.settings.diag_interval
        self.state["diagnostics"] = AutoDiagnostics(self.state, interval=diag_interval)
        diag_task = asyncio.create_task(
            self.state["diagnostics"].run_loop(self.broadcast)
        )
        self._tasks.append(diag_task)

        logger.info("Backend fully initialized and running")

    # ── Full startup (convenience) ──

    async def startup(self, screen_loop_coro, on_transcript_cb):
        """Run all initialization phases in order."""
        logger.info("Starting AI Desktop Assistant backend")
        self.init_core()
        self.init_window()
        self.init_scanners()
        self.init_tier1()
        self.init_pipeline()
        self.init_plugins()
        await self.start_tasks(screen_loop_coro, on_transcript_cb)

    # ── Shutdown ──

    async def shutdown(self):
        """Cancel all tasks and emit shutdown event."""
        logger.info("Shutting down backend")

        # Save ReadModel snapshot before shutdown
        rm = self.state.get("read_model")
        if rm:
            rm.save_snapshot()

        # Shutdown plugins
        loader = self.state.get("plugin_loader")
        if loader:
            await loader.shutdown()

        bus = self.state.get("event_bus")
        store = getattr(bus, "store", None) if bus else None
        if bus:
            try:
                await bus.publish(typed_event(
                    EventType.SYSTEM_SHUTDOWN,
                    SystemShutdownPayload(
                        uptime_seconds=round(
                            time.time() - self.state["stats"]["start_time"], 1
                        )
                    ),
                    source="bootstrap",
                ))
            except Exception as e:
                logger.warning("Failed to publish shutdown event", error=str(e))

        for task in self._tasks:
            task.cancel()

        stt = self.state.get("stt")
        if stt:
            await stt.stop()

        # Flush batched EventStore writes and close DB handle.
        if store and hasattr(store, "close"):
            store.close()

        logger.info("Backend shutdown complete")

    # ── Introspection ──

    @property
    def init_report(self) -> Dict[str, bool]:
        """Return init success/failure for each component."""
        return dict(self._init_results)
