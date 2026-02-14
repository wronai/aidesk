"""Pipeline step protocol (Interface Segregation)."""
from typing import Protocol, runtime_checkable

from event_bus import EventBus
from .context import PipelineContext


@runtime_checkable
class PipelineStep(Protocol):
    """
    Protocol for pipeline steps (Interface Segregation Principle).

    Every step must:
    - Have a unique name
    - Define can_run() to check preconditions
    - Implement execute() to do work and mutate context
    """
    name: str

    def can_run(self, ctx: PipelineContext) -> bool:
        """Check if this step should run given current context."""
        ...

    async def execute(self, ctx: PipelineContext, bus: EventBus) -> PipelineContext:
        """Execute step, update context, emit events. Return updated context."""
        ...
