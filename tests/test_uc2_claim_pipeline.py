"""UC2 — "the Friday approval": ClaimPipeline runs node ``validate``, node ``approve``
interrupts, the process dies and restarts, ``/pending`` lists the interrupt, resuming runs
node ``approve`` to completion. Same SSE route as UC1 (``surfaces/serve/app.py``, untouched
by this file or anything it imports) starts the run; ``surfaces/serve/workflows.py`` is the
new, additive ``/pending``/``/resume`` surface.

The "kill -9 and restart" is modeled two ways: the main test drops every Python object from
the first phase and builds entirely fresh ones reading the same two sqlite files (there is
no shared state left to cheat with — status has to come from disk); a second, smaller test
spans a real OS process boundary via ``subprocess``, mirroring
``test_workflow_durability.py``'s own restart test for v1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from typing import Any, TypedDict

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agentdeck.adapters.engines.langgraph import LangGraphEngine, resolve_checkpointer
from agentdeck.adapters.engines.langgraph.engine import _to_graph_input
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.events import check_contiguous, check_terminal, parse_event
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.status import RunStatus, status_of
from agentdeck.runtime.service import Runtime
from agentdeck.surfaces.serve.app import build_app
from agentdeck.surfaces.serve.workflows import build_workflow_app

pytest.importorskip("fastapi")
pytest.importorskip("langgraph.checkpoint.sqlite", reason="needs the [durability] extra")

TENANT = "demo"
PRINCIPAL = "user:demo"
SESSION_ID = "s1"


class ClaimState(TypedDict, total=False):
    """A ``TypedDict`` schema, not a bare ``dict``: langgraph gives each field its own
    channel only then, so ``approve``'s return shallow-merges into state instead of
    replacing it outright — the contract ``NodeUpdated.state_patch`` documents."""

    input: str
    claim_id: str
    decision: str


def _validate(state: ClaimState) -> ClaimState:
    return {"claim_id": state["input"].rsplit(" ", 1)[-1]}


def _approve(state: ClaimState) -> ClaimState:
    decision = interrupt({"reason": "approval", "claim_id": state["claim_id"], "question": "approve this claim?"})
    return {"decision": decision}


def _claim_pipeline_graph() -> StateGraph[Any]:
    g: StateGraph[Any] = StateGraph(ClaimState)
    g.add_node("validate", _validate)
    g.add_node("approve", _approve)
    g.add_edge(START, "validate")
    g.add_edge("validate", "approve")
    g.add_edge("approve", END)
    return g


def _spec() -> InvocableSpec:
    return InvocableSpec(
        name="ClaimPipeline", kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=_claim_pipeline_graph()
    )


def _runtime(db_path: str, checkpoint_path: str) -> tuple[Runtime, SqliteEventStore]:
    engine = LangGraphEngine(checkpointer=resolve_checkpointer("sqlite", checkpoint_path))
    store = SqliteEventStore(db_path)
    return Runtime([engine], store, {"ClaimPipeline": _spec()}), store


async def _post_events(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> list[Any]:
    events = []
    async with client.stream("POST", path, json=body) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(parse_event(json.loads(line.removeprefix("data: "))))
    return events


async def test_uc2_claim_pipeline_survives_a_restart(tmp_path: Any) -> None:
    db_path = str(tmp_path / "events.sqlite3")
    checkpoint_path = str(tmp_path / "checkpoints.sqlite3")

    runtime, store = _runtime(db_path, checkpoint_path)
    app = build_app(runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        opening = await _post_events(
            client, "/v2/invocables/ClaimPipeline/chat", {"session_id": SESSION_ID, "message": "claim 4412"}
        )

    assert [event.kind for event in opening] == ["run.started", "node.updated", "run.interrupted"]
    assert opening[-1].payload.reason == "approval"
    assert opening[-1].payload.payload["claim_id"] == "4412"

    # "kill -9": drop every reference this phase held, including the open connections.
    store.close()
    del runtime, store, app

    # "restart": brand-new Runtime, store and engine, reading only the two files on disk.
    runtime2, store2 = _runtime(db_path, checkpoint_path)
    status_ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="n/a", trace_id="t", session_id=SESSION_ID)
    assert status_of(await store2.read(status_ctx.log_key, status_ctx)) is RunStatus.WAITING_HUMAN

    workflow_app = build_workflow_app(runtime2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workflow_app), base_url="http://test") as client2:
        listed = (await client2.get("/v2/pending")).json()
        assert len(listed) == 1
        assert listed[0]["invocable"] == "ClaimPipeline"
        assert listed[0]["payload"]["claim_id"] == "4412"
        thread_id = listed[0]["thread_id"]

        resumed = await _post_events(client2, "/v2/resume", {"thread_id": thread_id, "value": "approved"})
        assert [event.kind for event in resumed] == ["run.resumed", "node.updated", "run.completed"]

        # a stray resume against the now-completed run is a no-op, not an error
        stray = await client2.post("/v2/resume", json={"thread_id": thread_id, "value": "again"})
        assert stray.json() == {"status": "no-op"}

    whole = opening + resumed
    assert check_terminal(whole) is None
    assert check_contiguous(whole) == []
    assert [event.seq for event in whole] == list(range(len(whole)))  # contiguous across the restart, no reset
    assert await store2.read(status_ctx.log_key, status_ctx) == whole  # one coherent story, no duplicates

    node_updates = [event.payload.node for event in whole if event.kind == "node.updated"]
    assert node_updates == ["validate", "approve"]  # validate did not re-run after the restart


async def test_langgraph_transcript_fidelity() -> None:
    """ADR-D5's contract for this engine: the checkpointer's own final state (execution
    state) must equal the event log's state, reconstructed by shallow-merging every
    ``node.updated`` patch onto the graph's initial input — nothing that entered or left
    execution state is missing from the log.
    """
    checkpointer = MemorySaver()
    engine = LangGraphEngine(checkpointer=checkpointer)
    store = SqliteEventStore()
    runtime = Runtime([engine], store, {"ClaimPipeline": _spec()})
    ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="fidelity-1", trace_id="t", session_id="fidelity")

    events = [event async for event in runtime.run("ClaimPipeline", coerce_input("claim 7777"), ctx)]
    thread_id = events[-1].payload.thread_id
    assert thread_id is not None
    events += [event async for event in runtime.resume("ClaimPipeline", thread_id, "approved", ctx)]

    log_state: dict[str, Any] = dict(_to_graph_input(events[0].payload.input))
    for event in events:
        if event.kind == "node.updated":
            log_state.update(event.payload.state_patch)

    compiled = _claim_pipeline_graph().compile(checkpointer=checkpointer)
    engine_state = await compiled.aget_state({"configurable": {"thread_id": thread_id}})

    assert log_state == engine_state.values
    assert log_state == {"input": "claim 7777", "claim_id": "7777", "decision": "approved"}


_RESTART_SCRIPT = """
import asyncio, sys
from agentdeck.adapters.engines.langgraph import LangGraphEngine, resolve_checkpointer
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.runtime.service import Runtime
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


def _validate(state):
    return {"claim_id": state["input"].rsplit(" ", 1)[-1]}


def _approve(state):
    return {"decision": interrupt({"reason": "approval", "claim_id": state["claim_id"]})}


def _spec():
    from typing import TypedDict

    class ClaimState(TypedDict, total=False):
        input: str
        claim_id: str
        decision: str

    g = StateGraph(ClaimState)
    g.add_node("validate", _validate)
    g.add_node("approve", _approve)
    g.add_edge(START, "validate")
    g.add_edge("validate", "approve")
    g.add_edge("approve", END)
    return InvocableSpec(name="ClaimPipeline", kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=g)


async def main():
    engine = LangGraphEngine(checkpointer=resolve_checkpointer("sqlite", sys.argv[2]))
    store = SqliteEventStore(sys.argv[1])
    runtime = Runtime([engine], store, {"ClaimPipeline": _spec()})
    ctx = RunContext(tenant="demo", principal="user:demo", run_id="uc2-restart", trace_id="t", session_id="s1")
    if sys.argv[3] == "interrupt":
        async for event in runtime.run("ClaimPipeline", coerce_input("claim 9911"), ctx):
            print(event.kind)
    else:
        async for event in runtime.resume("ClaimPipeline", sys.argv[4], "approved", ctx):
            print(event.kind)


asyncio.run(main())
"""


def test_uc2_claim_pipeline_survives_a_real_process_restart(tmp_path: Any) -> None:
    """The literal "kill -9, restart" from the milestone script: two separate ``python``
    processes, sharing nothing but the two sqlite files on disk.
    """
    db_path = str(tmp_path / "events.sqlite3")
    checkpoint_path = str(tmp_path / "checkpoints.sqlite3")
    env = {**os.environ}

    first = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_RESTART_SCRIPT), db_path, checkpoint_path, "interrupt"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=True,
    )
    assert first.stdout.split() == ["run.started", "node.updated", "run.interrupted"]

    store = SqliteEventStore(db_path)
    ctx = RunContext(tenant="demo", principal="user:demo", run_id="uc2-restart", trace_id="t", session_id="s1")

    async def _read_thread_id() -> str:
        history = await store.read(ctx.log_key, ctx)
        assert status_of(history) is RunStatus.WAITING_HUMAN
        interrupted = next(event for event in history if event.kind == "run.interrupted")
        assert interrupted.payload.thread_id is not None
        return interrupted.payload.thread_id

    import asyncio

    thread_id = asyncio.run(_read_thread_id())
    store.close()

    second = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_RESTART_SCRIPT), db_path, checkpoint_path, "resume", thread_id],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=True,
    )
    assert second.stdout.split() == ["run.resumed", "node.updated", "run.completed"]
