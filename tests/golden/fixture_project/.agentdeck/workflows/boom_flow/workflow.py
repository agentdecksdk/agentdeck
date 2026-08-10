"""Golden fixture workflow: one node that fails, so the 500 / SSE-error paths are recorded.

The message is deliberately secret-shaped — `serve.py` must never echo it.
"""

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from agentdeck.authoring import Workflow
from agentdeck.errors import SkillError

SECRET = "stderr: AGENTDECK_TOKEN=sk-do-not-leak"


def _explode(_state):
    raise SkillError(SECRET)


class State(BaseModel):
    text: str = ""


def _build_graph():
    g = StateGraph(State)
    g.add_node("explode", _explode)
    g.set_entry_point("explode")
    g.add_edge("explode", END)
    return g


boom_flow = Workflow(name="BoomFlow", state=State, graph=_build_graph)
