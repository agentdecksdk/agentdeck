"""Golden fixture workflow: two deterministic nodes, no interrupt — the ``done`` path."""

from pydantic import BaseModel

from agentdeck.workflows import END, BaseWorkflow, StateGraph


class State(BaseModel):
    text: str = ""
    upper: str = ""
    length: int = 0


class EchoFlow(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("shout", lambda s: {"upper": s.text.upper()})
        g.add_node("measure", lambda s: {"length": len(s.text)})
        g.set_entry_point("shout")
        g.add_edge("shout", "measure")
        g.add_edge("measure", END)
        return g
