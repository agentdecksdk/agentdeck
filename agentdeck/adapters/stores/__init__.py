"""Event-log stores  -  implementations of ``EventStorePort``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.status import RunStatus
from agentdeck.errors import RunStateError

if TYPE_CHECKING:
    from agentdeck.core.context import RunContext


def _refuse_if_sealed(status: RunStatus | None, ctx: RunContext) -> None:
    """Refuse the append a store is inside when the run's log is already sealed.

    Shared by all four stores rather than written out in each, and called from inside whatever
    makes that backend's write indivisible  -  the only place a write already suspended in there
    can still be stopped. Not on the port, because ``agentdeck.core`` may not name an error type.

    ``CANCELLED`` and ``COMPLETED`` only: whichever of the two landed first is the run's outcome,
    and a takeover's ``run.failed`` deliberately seals nothing (ADR-D11 §5).
    """
    if status in (RunStatus.CANCELLED, RunStatus.COMPLETED):
        raise RunStateError(f"run {ctx.run_id!r} is already {status}; nothing can be appended to it any more")
