"""Human-in-the-loop: a run paused for an answer, as a typed result.

A ``@workflow`` body calling ``ctx.ask(...)`` suspends where it stands and hands control back to
the caller. Answering it continues the body on its next line: its own locals are the checkpoint,
so nothing before the ``ask`` runs a second time.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class InterruptResult(TypedDict):
    """A paused run: what the human is asked (``payload``) and which thread answers it.

    ``id`` is the canonical run id (docs/design/run-identity.md §8)  -  present whenever this
    came from a run the Runtime is tracking (``Deck.run``/``Deck.stream``). ``thread_id`` stays
    internal to the engine and is never the address a caller holding this dict addresses the run
    by.
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


__all__ = ["InterruptResult", "interrupt_result"]
