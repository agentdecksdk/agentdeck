"""Golden fixture workflow: one side-effect-only node, so the ``delta: null`` frame is pinned.

A node that logs or notifies and returns nothing is the commonest LangGraph node shape there
is, and LangGraph reports its update as ``None`` — which v1's wire showed as ``"delta": null``.
"""

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from agentdeck.authoring import Workflow


class State(BaseModel):
    request: str = ""


def _notify(_state):
    """Side effects only — no state to merge back, so LangGraph reports no update."""
    return None


def _build_graph():
    g = StateGraph(State)
    g.add_node("notify", _notify)
    g.set_entry_point("notify")
    g.add_edge("notify", END)
    return g


side_effect_flow = Workflow(name="SideEffectFlow", state=State, graph=_build_graph)
