"""
Command Handlers — CQRS Write Side.

Handles state-changing operations as typed commands via the EventBus.
Each handler:
- Receives a command Event
- Performs the mutation
- Emits result events

Single Responsibility: each handler does exactly one thing.
Open/Closed: add new handlers without modifying existing ones.
Dependency Inversion: handlers receive dependencies, don't import them.
"""
import structlog
from typing import Any, Callable, Dict, Optional

from event_bus import Event, EventBus, EventType, EventCategory

logger = structlog.get_logger()


class CommandHandlers:
    """
    Registry of CQRS command handlers.

    Subscribes to cmd.* events on the EventBus and dispatches
    to the appropriate handler method.
    """

    def __init__(self, bus: EventBus, app_state: Dict[str, Any]):
        self.bus = bus
        self.state = app_state
        self._broadcast_fn: Optional[Callable] = None

    def set_broadcast(self, broadcast_fn: Callable):
        """Inject the SSE broadcast function (avoids circular import)."""
        self._broadcast_fn = broadcast_fn

    def register_all(self):
        """Subscribe all command handlers to the EventBus."""
        self.bus.subscribe(EventType.CMD_SWITCH_OCR_ENGINE.value, self.handle_switch_ocr)
        self.bus.subscribe(EventType.CMD_SWITCH_MODE.value, self.handle_switch_mode)
        self.bus.subscribe(EventType.CMD_EXECUTE_ACTION.value, self.handle_execute_action)
        self.bus.subscribe(EventType.CMD_APPROVE_ACTION.value, self.handle_approve_action)
        self.bus.subscribe(EventType.CMD_RUN_SAFE.value, self.handle_run_safe)
        self.bus.subscribe(EventType.CMD_RUN_BENCHMARK.value, self.handle_run_benchmark)
        logger.info("Command handlers registered", count=6)

    async def handle_switch_ocr(self, event: Event):
        """Switch OCR engine."""
        engine_name = event.data.get("engine", "")
        ocr = self.state.get("ocr_manager")
        if not ocr:
            logger.warning("CMD switch_ocr: OCR manager not available")
            return

        try:
            success = ocr.switch_engine(engine_name)
            if success:
                logger.info("OCR engine switched via command", engine=engine_name)
                if self._broadcast_fn:
                    await self._broadcast_fn("ocr_engine_changed", {
                        "engine": engine_name,
                        "available": list(ocr.engines.keys()),
                    })
        except Exception as e:
            logger.error("CMD switch_ocr failed", error=str(e))

    async def handle_switch_mode(self, event: Event):
        """Switch analysis mode."""
        mode_name = event.data.get("mode", "")
        analyzer = self.state.get("analyzer")
        if not analyzer:
            logger.warning("CMD switch_mode: Analyzer not available")
            return

        try:
            valid_modes = ["vision_only", "ocr_only", "hybrid", "ocr_plus_vision"]
            if mode_name not in valid_modes:
                logger.warning("CMD switch_mode: invalid mode", mode=mode_name)
                return

            analyzer.set_mode(mode_name)
            logger.info("Analysis mode switched via command", mode=mode_name)
            if self._broadcast_fn:
                await self._broadcast_fn("mode_changed", {"mode": mode_name})
        except Exception as e:
            logger.error("CMD switch_mode failed", error=str(e))

    async def handle_approve_action(self, event: Event):
        """Approve a pending agent action."""
        action_id = event.data.get("action_id", "")
        agent = self.state.get("shell_agent")
        if not agent:
            return

        success = agent.approve_action(action_id)
        logger.info("Agent action approved via command", action_id=action_id, success=success)

    async def handle_execute_action(self, event: Event):
        """Execute an approved agent action."""
        action_id = event.data.get("action_id", "")
        cwd = event.data.get("cwd")
        agent = self.state.get("shell_agent")
        if not agent:
            return

        try:
            if not cwd:
                latest_window = self.state.get("latest_window")
                if latest_window and latest_window.get("cwd"):
                    cwd = latest_window["cwd"]

            result = agent.execute_action(action_id, cwd=cwd)
            logger.info("Agent action executed via command", action_id=action_id)
            if self._broadcast_fn:
                await self._broadcast_fn("agent_result", result.to_dict())
        except Exception as e:
            logger.error("CMD execute_action failed", error=str(e), action_id=action_id)

    async def handle_run_safe(self, event: Event):
        """Run a safe (read-only) command."""
        command = event.data.get("command", "").strip()
        cwd = event.data.get("cwd")
        agent = self.state.get("shell_agent")
        if not agent or not command:
            return

        try:
            result = agent.execute_safe(command, cwd=cwd)
            logger.info("Safe command executed via command bus", command=command)
        except Exception as e:
            logger.error("CMD run_safe failed", error=str(e))

    async def handle_run_benchmark(self, event: Event):
        """Run OCR benchmark."""
        image_b64 = event.data.get("image_b64", "")
        ocr = self.state.get("ocr_manager")
        if not ocr or not image_b64:
            return

        try:
            result = ocr.benchmark(image_b64)
            logger.info("OCR benchmark completed via command bus")
            if self._broadcast_fn:
                await self._broadcast_fn("ocr_benchmark", result)
        except Exception as e:
            logger.error("CMD run_benchmark failed", error=str(e))
