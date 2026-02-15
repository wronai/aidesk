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

import nfo
from nfo.models import LogEntry
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
from optimization_strategy import create_optimization_strategy
from plugins.loader import PluginLoader
from preflight import run_preflight

logger = structlog.get_logger()


def _get_nfo_logger():
    """Return the nfo default logger (set by nfo.configure)."""
    from nfo.decorators import _get_default_logger
    return _get_default_logger()


def _emit_boot(component: str, phase: str, ok: bool, elapsed_ms: float,
               summary: str = "", **extra) -> None:
    """Emit an nfo LogEntry for a bootstrap component init."""
    entry = LogEntry(
        timestamp=LogEntry.now(),
        level="INFO" if ok else "WARNING",
        function_name=f"boot.{component}",
        module="bootstrap",
        args=(),
        kwargs={},
        arg_types=[],
        kwarg_types={},
        duration_ms=round(elapsed_ms, 1),
        return_value=summary if ok else "FAILED",
        return_type="str",
        extra={"phase": phase, "component": component, "ok": ok, **extra},
    )
    _get_nfo_logger().emit(entry)


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

    # ── Generic component init with nfo tracing ──

    def _init_component(self, key: str, factory, phase: str, **kwargs) -> bool:
        """Initialize a component with timing, nfo logging, and error handling."""
        t0 = time.monotonic()
        try:
            self.state[key] = factory(**kwargs)
            elapsed = (time.monotonic() - t0) * 1000
            self._init_results[key] = True

            # Try to extract a summary from the component
            component = self.state[key]
            summary = ""
            if hasattr(component, "get_stats"):
                try:
                    stats = component.get_stats()
                    summary = ", ".join(f"{k}={v}" for k, v in list(stats.items())[:3])
                except Exception:
                    pass
            if not summary:
                summary = type(component).__name__

            _emit_boot(key, phase, ok=True, elapsed_ms=elapsed, summary=summary)
            return True
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            self._init_results[key] = False
            logger.warning(f"{key} initialization failed", error=str(e))
            _emit_boot(key, phase, ok=False, elapsed_ms=elapsed, error=str(e))
            return False

    # ── Phase 1: Core (required) ──

    def init_core(self):
        """Initialize capture, OCR manager, and analyzer."""
        for key, factory, kw in [
            ("capture", create_capture_from_env, {"settings": self.settings}),
            ("ocr_manager", create_ocr_manager_from_env, {"settings": self.settings}),
        ]:
            self._init_component(key, factory, "core", **kw)

        self._init_component(
            "analyzer",
            lambda **kw: create_analyzer_from_env(
                ocr_manager=self.state["ocr_manager"], **kw
            ),
            "core",
            settings=self.settings,
        )
        logger.info("Core components initialized")

    # ── Phase 2: Window awareness (optional) ──

    def init_window(self):
        """Initialize window manager, profile manager, and shell agent."""
        if self.settings.enable_window_aware:
            self._init_component("window_manager", create_window_manager_from_env, "window", settings=self.settings)
        self._init_component("profile_manager", create_profile_manager, "window", settings=self.settings)
        if self.settings.enable_shell_agent:
            self._init_component("shell_agent", create_shell_agent_from_env, "window", settings=self.settings)

    # ── Phase 3: Process scanner & window cropper (optional) ──

    def init_scanners(self):
        """Initialize process scanner and window cropper."""
        self._init_component(
            "process_scanner",
            lambda **kw: create_process_scanner(
                window_manager=self.state.get("window_manager"), **kw
            ),
            "scanners",
            settings=self.settings,
        )
        if self.state.get("process_scanner"):
            self._init_component(
                "window_cropper",
                lambda **kw: create_window_cropper(
                    process_scanner=self.state["process_scanner"], **kw
                ),
                "scanners",
                settings=self.settings,
            )
        else:
            self._init_results["window_cropper"] = False

    # ── Phase 4: Tier 1 modules (all optional, each independent) ──

    def init_tier1(self):
        """Initialize Tier 1 modules: multi-monitor, semantic memory, action templates, OCR post-process, predictive."""
        tier1_components = [
            ("multi_monitor", create_multi_monitor_from_env),
            ("semantic_memory", create_semantic_memory_from_env),
            ("action_library", create_action_library_from_env),
            ("ocr_enhancer", create_ocr_enhancer_from_env),
            ("predictive_engine", create_predictive_engine_from_env),
            ("clipboard_manager", create_clipboard_manager_from_env),
            ("cost_budget", create_cost_budget_from_env),
        ]
        for key, factory in tier1_components:
            self._init_component(key, factory, "tier1", settings=self.settings)

        # OptimizationStrategy depends on cost_budget — create after it
        self._init_component(
            "optimization_strategy",
            lambda **kw: create_optimization_strategy(
                settings=kw.get("settings"),
                cost_budget=self.state.get("cost_budget"),
            ),
            "tier1",
            settings=self.settings,
        )

    # ── Phase 5: Pipeline + CQRS (depends on phases 1-4) ──

    def init_pipeline(self):
        """Initialize event bus, pipeline orchestrator, CQRS handlers."""
        # Event Bus
        self.state["event_bus"] = create_event_bus(
            enable_store=True,
            db_path=os.getenv("EVENT_STORE_DB", "logs/events.db"),
        )

        # Pipeline Orchestrator
        opt_strategy = self.state.get("optimization_strategy")
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
            optimization_strategy=opt_strategy,
        )

        # Profile Selector (pass OCR manager + optimization strategy)
        self.state["profile_selector"] = create_profile_selector(
            ocr_manager=self.state.get("ocr_manager"),
            optimization_strategy=opt_strategy,
        )

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

        # Attach nfo bridge to Tracer (spans → nfo entries)
        try:
            from observability import attach_nfo_bridge
            attach_nfo_bridge()
        except Exception:
            pass  # observability bridge is optional

        self._init_results["event_bus"] = True
        self._init_results["pipeline"] = True
        self._init_results["read_model"] = True
        logger.info("Pipeline + CQRS initialized")

    # ── Phase 6: Plugins (optional) ──

    def init_plugins(self):
        """Discover and load plugins."""
        plugin_dir = getattr(self.settings, "plugins_dir", os.path.join(os.path.dirname(__file__), "plugins"))

        def _create_loader(**_kw):
            loader = PluginLoader(
                plugin_dir=plugin_dir,
                bus=self.state.get("event_bus"),
                app_state=self.state,
            )
            loader.discover_and_load()
            return loader

        self._init_component("plugin_loader", _create_loader, "plugins")
        self._init_results["plugins"] = self._init_results.get("plugin_loader", False)

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

    @nfo.log_call(level="INFO")
    async def startup(self, screen_loop_coro, on_transcript_cb):
        """Run all initialization phases in order."""
        logger.info("Starting Proxeen Assistant backend")
        boot_t0 = time.monotonic()

        self.init_core()
        self.init_window()
        self.init_scanners()
        self.init_tier1()
        self.init_pipeline()
        self.init_plugins()

        # Pre-startup model connectivity checks
        preflight_report = await run_preflight(self.settings)
        self.state["preflight"] = preflight_report
        self._init_results["preflight"] = preflight_report.get("all_ok", False)

        await self.start_tasks(screen_loop_coro, on_transcript_cb)

        # Emit boot summary
        boot_ms = (time.monotonic() - boot_t0) * 1000
        ok = [k for k, v in self._init_results.items() if v]
        failed = [k for k, v in self._init_results.items() if not v]
        _emit_boot(
            "summary", "boot", ok=len(failed) == 0, elapsed_ms=boot_ms,
            summary=f"{len(ok)} OK, {len(failed)} failed" if failed else f"{len(ok)} components ready",
            ok_components=ok, failed_components=failed,
            version=self.version,
        )

    # ── Shutdown ──

    @nfo.log_call(level="INFO")
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
