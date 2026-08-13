"""Your existing LangGraph agent. Nothing in this file knows agentdeck exists.

Written before AgentDeck was in the picture, and unchanged by adopting it: a `TypedDict` state,
three nodes, plain `langgraph` imports. That is the point of this example — the wrapping happens
next door, in `workflow.py`, and this file is the one that never had to move.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import StateGraph

_URGENT = ("outage", "down", "breach", "data loss")


class TicketState(TypedDict, total=False):
    """Whatever state your graph already has. A `TypedDict` here, deliberately: agentdeck does
    not require a pydantic model, and demanding one would be a rewrite of exactly the kind this
    example claims you do not need.
    """

    input: str
    severity: str
    queue: str
    reply: str


def classify(state: TicketState) -> dict:
    text = (state.get("input") or "").lower()
    return {"severity": "urgent" if any(word in text for word in _URGENT) else "normal"}


def route(state: TicketState) -> dict:
    return {"queue": "oncall" if state.get("severity") == "urgent" else "support"}


def draft_reply(state: TicketState) -> dict:
    return {"reply": f"Logged as {state.get('severity')}, routed to {state.get('queue')}."}


def build() -> StateGraph:
    """A `() -> StateGraph` factory, uncompiled.

    If your own module already ends in `graph = builder.compile()`, expose the builder as well —
    agentdeck compiles it itself so it can attach a checkpointer when a workflow is `durable`,
    and a graph that arrives already compiled has closed that door.
    """
    builder = StateGraph(TicketState)
    builder.add_node("classify", classify)
    builder.add_node("route", route)
    builder.add_node("draft_reply", draft_reply)
    builder.set_entry_point("classify")
    builder.add_edge("classify", "route")
    builder.add_edge("route", "draft_reply")
    return builder
