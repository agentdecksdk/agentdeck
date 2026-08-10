"""A fan-out graph whose one branch interrupts while a sibling completes (#122).

LangGraph reports the pause as soon as the interrupting branch raises, without waiting for a
slower sibling in the same superstep — so the sibling's ``updates`` chunk lands on the astream
*after* the interrupt has already been detected. ``_play`` used to drain and discard everything
after that point (needed so the checkpoint commits before the pause is reported, see its
comment) which threw the sibling's report away with it: the engine's own checkpoint already has
``b: "B"`` at that instant, but the canonical event log never said so. A live caller lost the
only in-run notice that branch ran; the news would only resurface whenever the run eventually
resumes and reaches ``END``, which may be long after.

The sibling's node is deliberately slower than the interrupting one so the drop reproduces
every time, not on a race: LangGraph still finishes the whole superstep before signalling the
pause, but only after the fast branch's own task has already been reported.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.core.content import DataBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import NodeUpdated, RunCompleted, RunInterrupted
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.runtime.discovery import DURABLE_KEY

if TYPE_CHECKING:
    from agentdeck.core.content import Input
    from agentdeck.core.events import KnownPayload


class _State(TypedDict, total=False):
    text: str
    decision: str
    b: str


def _ask(state: _State) -> _State:
    return {"decision": interrupt({"question": state.get("text", "")})}


async def _sibling(_state: _State) -> _State:
    # Slower than `_ask`'s immediate interrupt, so its update always lands on the stream
    # after LangGraph has already reported the pause.
    await asyncio.sleep(0.2)
    return {"b": "B"}


def _spec() -> InvocableSpec:
    graph: StateGraph[Any] = StateGraph(_State)
    graph.add_node("ask", _ask)
    graph.add_node("fb", _sibling)
    graph.add_edge(START, "ask")
    graph.add_edge(START, "fb")
    graph.add_edge("ask", END)
    graph.add_edge("fb", END)
    return InvocableSpec(
        name="FanOut",
        kind=InvocableKind.WORKFLOW,
        engine=LangGraphEngine.engine,
        native=graph,
        metadata={DURABLE_KEY: True},
    )


def _ctx(run_id: str) -> RunContext:
    return RunContext(namespace="acme", run_id=run_id, session_id="thread-fanout")


async def _start(engine: LangGraphEngine, spec: InvocableSpec) -> list[KnownPayload]:
    input: Input = [DataBlock(data={"text": "approve?"})]
    return [payload async for payload in engine.start(spec, input, [], _ctx("r-1"))]


async def test_a_completed_siblings_update_reaches_the_log_before_the_pause() -> None:
    """The sibling's report is not dropped, and the pause is reported last."""
    engine = LangGraphEngine()
    spec = _spec()

    payloads = await _start(engine, spec)

    node_updates = [p for p in payloads if isinstance(p, NodeUpdated)]
    assert any(p.node == "fb" and p.state_patch == {"b": "B"} for p in node_updates), (
        f"the sibling's update never reached the log: {payloads}"
    )
    assert isinstance(payloads[-1], RunInterrupted), f"the pause was not the last payload: {payloads}"
    assert not any(isinstance(p, RunCompleted) for p in payloads), "a parked run must not report done"


async def test_resuming_still_reaches_the_merged_final_state() -> None:
    """Resuming re-enters `ask` and carries the sibling's already-committed write to `END`.

    Not a claim about what LangGraph's own replay reports mid-stream on resume (it replays the
    prior superstep's cached results too, which is its own well-established mechanism, not part
    of #122) — only that the two branches' writes both land in the state the run finishes with.
    """
    engine = LangGraphEngine()
    spec = _spec()

    first = await _start(engine, spec)
    pause = next(p for p in first if isinstance(p, RunInterrupted))

    resumed = [payload async for payload in engine.resume(spec, pause.thread_id, "yes", _ctx("r-1"))]

    terminal = resumed[-1]
    assert isinstance(terminal, RunCompleted), terminal
    state = terminal.output[0]
    assert isinstance(state, DataBlock)
    assert state.data == {"text": "approve?", "decision": "yes", "b": "B"}
