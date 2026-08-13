"""Golden fixture workflow: one node that fails with a plain exception — not an
``AgentdeckError`` — so the catch-all 500 path is recorded too, alongside ``boom_flow``'s
``AgentdeckError`` one.

The message is deliberately secret-shaped — `serve.py` must never echo it.
"""

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from agentdeck.authoring import Workflow

SECRET = "stderr: AGENTDECK_TOKEN=sk-do-not-leak-2"


def _crash(_state):
    raise ValueError(SECRET)


class State(BaseModel):
    text: str = ""


def _build_graph():
    g = StateGraph(State)
    g.add_node("crash", _crash)
    g.set_entry_point("crash")
    g.add_edge("crash", END)
    return g


crash_flow = Workflow(name="CrashFlow", state=State, graph=_build_graph)
