"""Human-in-the-loop: LangGraph ``interrupt()`` pauses as a typed result.

A node calling ``langgraph.types.interrupt(payload)`` checkpoints the whole run and
hands control back to the caller. Resuming re-runs that node **from its start**, so
everything before the ``interrupt()`` call executes a second time: interrupt nodes
must be pure, and side effects (external mutations, sent messages) belong in
earlier nodes.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

INTERRUPT_KEY = "__interrupt__"


class InterruptResult(TypedDict):
    """A paused run: what the human is asked (``payload``) and which thread answers it."""

    type: Literal["interrupt"]
    payload: Any
    thread_id: str


def interrupt_result(payload: Any, thread_id: str) -> InterruptResult:
    return {"type": "interrupt", "payload": payload, "thread_id": thread_id}


def as_interrupt(result: Any, thread_id: str) -> InterruptResult | None:
    """``result`` as an :class:`InterruptResult`, or ``None`` if the run reached its end."""
    interrupts = result.get(INTERRUPT_KEY) if isinstance(result, dict) else None
    if not interrupts:
        return None
    return interrupt_result(interrupts[0].value, thread_id)


__all__ = ["INTERRUPT_KEY", "InterruptResult", "as_interrupt", "interrupt_result"]
