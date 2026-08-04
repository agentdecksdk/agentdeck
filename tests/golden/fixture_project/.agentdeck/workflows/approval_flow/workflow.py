"""Golden fixture workflow: pauses on one interrupt — the pending / resume path."""

from pydantic import BaseModel

from agentdeck.workflows import END, BaseWorkflow, StateGraph, interrupt


class State(BaseModel):
    request: str = ""
    decision: str = ""
    outcome: str = ""


class ApprovalFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
        g.add_node("settle", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
        g.set_entry_point("ask")
        g.add_edge("ask", "settle")
        g.add_edge("settle", END)
        return g
