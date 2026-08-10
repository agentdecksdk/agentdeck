"""Golden fixture workflow: pauses on one interrupt — the pending / resume path."""

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from agentdeck.authoring import Workflow


class State(BaseModel):
    request: str = ""
    decision: str = ""
    outcome: str = ""


def _build_graph():
    g = StateGraph(State)
    g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
    g.add_node("settle", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
    g.set_entry_point("ask")
    g.add_edge("ask", "settle")
    g.add_edge("settle", END)
    return g


approval_flow = Workflow(name="ApprovalFlow", state=State, durable=True, graph=_build_graph)
