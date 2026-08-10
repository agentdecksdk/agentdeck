"""Golden fixture workflow: two deterministic nodes, no interrupt — the ``done`` path."""

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from agentdeck.authoring import Workflow


class State(BaseModel):
    text: str = ""
    upper: str = ""
    length: int = 0


def _build_graph():
    g = StateGraph(State)
    g.add_node("shout", lambda s: {"upper": s.text.upper()})
    g.add_node("measure", lambda s: {"length": len(s.text)})
    g.set_entry_point("shout")
    g.add_edge("shout", "measure")
    g.add_edge("measure", END)
    return g


echo_flow = Workflow(name="EchoFlow", state=State, graph=_build_graph)
