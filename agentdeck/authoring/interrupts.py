"""Human-in-the-loop: LangGraph ``interrupt()`` pauses as a typed result.

A node calling ``langgraph.types.interrupt(payload)`` checkpoints the whole run and
hands control back to the caller. Resuming re-runs that node **from its start**, so
everything before the ``interrupt()`` call executes a second time: interrupt nodes
must be pure, and side effects (external mutations, sent messages) belong in
earlier nodes.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

INTERRUPT_KEY = "__interrupt__"


class InterruptResult(TypedDict):
    """A paused run: what the human is asked (``payload``) and which thread answers it.

    ``id`` is the canonical run id (docs/design/run-identity.md §8) — present whenever this
    came from a run the Runtime is tracking (``Deck.run``/``Deck.stream``), and absent when a
    :class:`~agentdeck.authoring.workflow.Workflow` is played directly with no Runtime in
    reach to mint or carry one. ``thread_id`` stays internal to the engine and is never the
    address a caller holding this dict addresses the run by.
    """

    type: Literal["interrupt"]
    payload: Any
    thread_id: str
    id: NotRequired[str | None]


def interrupt_result(payload: Any, thread_id: str, id: str | None = None) -> InterruptResult:
    result: InterruptResult = {"type": "interrupt", "payload": payload, "thread_id": thread_id}
    if id is not None:
        result["id"] = id
    return result


def as_interrupt(result: Any, thread_id: str) -> InterruptResult | None:
    """``result`` as an :class:`InterruptResult`, or ``None`` if the run reached its end."""
    interrupts = result.get(INTERRUPT_KEY) if isinstance(result, dict) else None
    if not interrupts:
        return None
    return interrupt_result(interrupts[0].value, thread_id)


__all__ = ["INTERRUPT_KEY", "InterruptResult", "as_interrupt", "interrupt_result"]
