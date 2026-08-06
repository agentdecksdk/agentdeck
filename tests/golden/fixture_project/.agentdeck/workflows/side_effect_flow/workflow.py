"""Golden fixture workflow: one side-effect-only node, so the ``delta: null`` frame is pinned.

A node that logs or notifies and returns nothing is the commonest LangGraph node shape there
is, and LangGraph reports its update as ``None`` — which v1's wire showed as ``"delta": null``.
"""

from pydantic import BaseModel

from agentdeck.workflows import END, BaseWorkflow, StateGraph


class State(BaseModel):
    request: str = ""


def _notify(_state):
    """Side effects only — no state to merge back, so LangGraph reports no update."""
    return None


class SideEffectFlow(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("notify", _notify)
        g.set_entry_point("notify")
        g.add_edge("notify", END)
        return g
