"""Golden fixture workflow: one node that fails, so the 500 / SSE-error paths are recorded.

The message is deliberately secret-shaped — `serve.py` must never echo it.
"""

from pydantic import BaseModel

from agentdeck.errors import SkillError
from agentdeck.workflows import END, BaseWorkflow, StateGraph

SECRET = "stderr: AGENTDECK_TOKEN=sk-do-not-leak"


def _explode(_state):
    raise SkillError(SECRET)


class State(BaseModel):
    text: str = ""


class BoomFlow(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("explode", _explode)
        g.set_entry_point("explode")
        g.add_edge("explode", END)
        return g
