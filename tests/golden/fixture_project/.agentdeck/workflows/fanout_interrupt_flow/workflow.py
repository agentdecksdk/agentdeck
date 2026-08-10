"""Golden fixture workflow: a fan-out whose one branch interrupts while a sibling completes.

Pins #122's corrected shape: `node_update` for the completed sibling, then `interrupt` in
place of `done` — not `done` (the run is parked, not finished) and not `interrupt` alone (the
sibling's report is not dropped). `settle` is deliberately slower than `ask`'s immediate
`interrupt()` so the ordering reproduces every time, not on a race between the two branches.
"""

import asyncio

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from agentdeck.authoring import Workflow


class State(BaseModel):
    request: str = ""
    decision: str = ""
    b: str = ""


def _ask(state: State):
    return {"decision": interrupt({"question": state.request})}


async def _settle(_state: State):
    await asyncio.sleep(0.2)
    return {"b": "B"}


def _build_graph():
    g = StateGraph(State)
    g.add_node("ask", _ask)
    g.add_node("settle", _settle)
    g.add_edge(START, "ask")
    g.add_edge(START, "settle")
    g.add_edge("ask", END)
    g.add_edge("settle", END)
    return g


fanout_interrupt_flow = Workflow(name="FanoutInterruptFlow", state=State, durable=True, graph=_build_graph)
