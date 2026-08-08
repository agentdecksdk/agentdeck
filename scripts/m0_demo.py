#!/usr/bin/env python3
"""Milestone-0 demo: discovery -> UC1 -> UC2 -> UC3, one continuous, deterministic run.

This is the "demo artifact" `milestone-0-walking-skeleton.md` §6 asks for, in script
form instead of a recording — deterministic and replayable beats a video that bit-rots.
Every Runtime here is assembled by `agentdeck.composition.build_runtime` — the same seam
`App` calls — so this script demonstrates the real wiring (real engines, real SQLite
stores, the real `surfaces/serve` FastAPI apps) instead of hand-assembling its own. It
lives under `scripts/`, not inside the `agentdeck` package, so it is not a new product
surface.

No network, no API keys: every model is a scripted fake, exactly like the automated UC1-3
tests it mirrors (`tests/test_uc1_handoff.py`, `tests/test_uc2_claim_pipeline.py`,
`tests/test_uc3_slowpoke.py`) — those tests are the falsifiable claims; this script is
their narrated, human-watchable replay. Run it with::

    python scripts/m0_demo.py

Everything runs against temporary SQLite files that are created and torn down inside one
`tempfile.TemporaryDirectory`; nothing is left behind and nothing pre-existing is touched.
Every section ends with `assert`s, so a broken skeleton fails this script loudly instead
of just looking like a nicer chat log.

The opening section is the one that goes through ``InvocableRegistry``: it authors a
throwaway ``.agentdeck/`` project and lets discovery produce the specs. UC1-UC3 keep
building their specs by hand, deliberately — each is pinned to a scripted fake model whose
exact turns are what makes the schema and ordering assertions falsifiable, and a bundle on
disk would only hide those fakes behind a file.

UC2's "kill -9, restart" is modeled by dropping every Python reference and rebuilding a
fresh `Runtime`/store/engine from the same two SQLite files, in this same process — not a
real OS-level `kill`. The real-subprocess version of that restart (two actual `python`
processes sharing only the files on disk) is covered by
`tests/test_uc2_claim_pipeline.py::test_uc2_claim_pipeline_survives_a_real_process_restart`,
not repeated here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from contextlib import chdir
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from agents import Agent, Model, function_tool
from agents.handoffs import Handoff
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.composition import build_runtime
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.events import RESULT_PREVIEW_MAX, check_contiguous, check_terminal, parse_event
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import ConfigError
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.surfaces.cli.chat import render, stream_chat
from agentdeck.surfaces.serve.app import build_app
from agentdeck.surfaces.serve.workflows import build_workflow_app

TENANT = "demo"
PRINCIPAL = "user:demo"
TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
LONG_RESULT = "Shipment 4412 was received damaged. " * 150


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _usage(input_tokens: int = 10, output_tokens: int = 5) -> ResponseUsage:
    return ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=input_tokens + output_tokens,
    )


def _response(output: list[Any]) -> Response:
    return Response(
        id="resp_demo",
        created_at=0.0,
        model="fake-demo",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=_usage(),
    )


# --------------------------------------------------------------------------------------
# Discovery — one `.agentdeck/` project becomes the Runtime's invocables
# --------------------------------------------------------------------------------------

# Two bundles in exactly the format a user authors, written to a scratch project dir. The
# agent needs no model here: the registry's job is to build it, and UC1 is where a built
# agent gets played.
BUNDLE_AGENT_PY = '''"""An agent bundle: `.agentdeck/agents/greeter/agent.py`."""

from agentdeck.agents import BaseAgent


class Greeter(BaseAgent):
    instructions = "Greet the user."
'''

BUNDLE_WORKFLOW_PY = '''"""A workflow bundle: `.agentdeck/workflows/shout/workflow.py`."""

from typing import TypedDict

from agentdeck.workflows import END, BaseWorkflow, StateGraph


class State(TypedDict, total=False):
    input: str
    shouted: str


class Shout(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        graph = StateGraph(cls.state)
        graph.add_node("shout", lambda state: {"shouted": state["input"].upper()})
        graph.set_entry_point("shout")
        graph.add_edge("shout", END)
        return graph
'''


async def run_discovery(tmp: Path) -> None:
    _banner("Discovery — InvocableRegistry over a `.agentdeck/` project (both engines)")
    from agentdeck.adapters.engines.langgraph import LangGraphEngine

    root = tmp / "project" / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(BUNDLE_AGENT_PY)
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(BUNDLE_WORKFLOW_PY)

    engines: list[Any] = [OpenAIAgentsEngine(), LangGraphEngine()]
    with chdir(root.parent):
        print("-- step 1: InvocableRegistry(engines).load() over ./.agentdeck --")
        specs = InvocableRegistry(engines).load()
        for name, spec in sorted(specs.items()):
            print(f"  {name}: kind={spec.kind.value} engine={spec.engine} native={type(spec.native).__name__}")
        assert sorted(specs) == ["Greeter", "Shout"]
        assert specs["Greeter"].engine == OpenAIAgentsEngine.engine
        assert specs["Shout"].engine == LangGraphEngine.engine

        print("-- step 2: the discovered workflow, played by the Runtime handed that mapping --")
        runtime = build_runtime(engines=engines, invocables=specs, store=MemoryEventStore())
        ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="run-discovery", trace_id="t", session_id="s-disc")
        kinds = [event.kind async for event in runtime.run("Shout", coerce_input("hello discovery"), ctx)]
        print(f"  {kinds}")
        assert kinds == ["run.started", "node.updated", "run.completed"]

        print("-- step 3: the same project, wired to a Runtime with no langgraph engine --")
        try:
            InvocableRegistry([OpenAIAgentsEngine()]).load()
        except ConfigError as exc:
            print(f"  refused at load, not at run: {exc}")
        else:
            raise AssertionError("a project needing an unregistered engine must fail at load")
    print("Discovery: PASS")


# --------------------------------------------------------------------------------------
# UC1 — the handoff chat (stresses the schema + ADR-D5)
# --------------------------------------------------------------------------------------


class FrontModel(Model):
    """Always speaks one sentence, then hands off to ClaimsAgent."""

    def __init__(self, handoff_tool: str) -> None:
        self._handoff_tool = handoff_tool
        self.calls = 0

    async def stream_response(self, _system_instructions: str | None, _input: Any, *_a: Any, **_k: Any):
        self.calls += 1
        message_id = f"msg_front_{self.calls}"
        text = "Connecting you to a claims specialist."
        yield ResponseTextDeltaEvent(
            content_index=0,
            delta=text,
            item_id=message_id,
            logprobs=[],
            output_index=0,
            sequence_number=0,
            type="response.output_text.delta",
        )
        output = [
            ResponseOutputMessage(
                id=message_id,
                content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            ),
            ResponseFunctionToolCall(
                id=f"fc_handoff_{self.calls}",
                call_id=f"call_handoff_{self.calls}",
                name=self._handoff_tool,
                arguments="{}",
                type="function_call",
            ),
        ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("the demo's fixtures only stream")


class ClaimsModel(Model):
    """Turn 1: calls the lookup tool, then answers. Turn 2: answers again, proving it still
    has turn 1's untruncated tool result in its own execution state."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_response(self, _system_instructions: str | None, input: Any, *_a: Any, **_k: Any):
        self.calls += 1
        if self.calls == 1:
            output: list[Any] = [
                ResponseFunctionToolCall(
                    id="fc_tool_1",
                    call_id="call_tool_1",
                    name="lookup_shipment",
                    arguments='{"shipment_id": "4412"}',
                    type="function_call",
                )
            ]
        else:
            if self.calls == 3:
                outputs = [
                    item.get("output")
                    for item in input
                    if isinstance(item, dict) and item.get("type") == "function_call_output"
                ]
                assert LONG_RESULT in outputs, "turn 2 lost turn 1's untruncated tool result"
            message_id = f"msg_claims_{self.calls}"
            text = "It was damaged; a refund is pending." if self.calls == 2 else "5 to 7 business days."
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=text,
                item_id=message_id,
                logprobs=[],
                output_index=0,
                sequence_number=0,
                type="response.output_text.delta",
            )
            output = [
                ResponseOutputMessage(
                    id=message_id,
                    content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("the demo's fixtures only stream")


@function_tool
def lookup_shipment(shipment_id: str) -> str:
    """Look up a shipment's status."""
    return LONG_RESULT


async def run_uc1(tmp: Path) -> None:
    _banner("UC1 — the handoff chat (FrontDesk -> ClaimsAgent, SQLite store)")
    claims_agent = Agent(name="ClaimsAgent", instructions="handle claims", tools=[lookup_shipment], model=ClaimsModel())
    handoff_tool = Handoff.default_tool_name(claims_agent)
    front_agent = Agent(
        name="FrontDesk", instructions="route to claims", handoffs=[claims_agent], model=FrontModel(handoff_tool)
    )
    spec = InvocableSpec(
        name="FrontDesk", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=front_agent
    )
    sessions = ExecutionStore()
    store = SqliteEventStore(str(tmp / "uc1-events.sqlite3"))
    runtime = build_runtime(
        engines=[OpenAIAgentsEngine(sessions)], invocables={"FrontDesk": spec}, store=store, clock=lambda: TS
    )
    app = build_app(runtime)

    session_id = "s1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as client:
        print("-- turn 1 --")
        await stream_chat(client, "FrontDesk", session_id, "my shipment 4412 is damaged")
        print("-- turn 2 --")
        await stream_chat(client, "FrontDesk", session_id, "and when will the refund arrive?")

    print("-- step 3: transcript read back from the store alone, no live stream --")
    ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="n/a", trace_id="t", session_id=session_id)
    log = await store.read(session_id, ctx)
    for event in log:
        if event.kind == "message.completed":
            print(f"  {event.origin} [{event.payload.message_id}]: {event.payload.text}")

    for run_id in {event.run_id for event in log}:
        run_events = [event for event in log if event.run_id == run_id]
        assert check_contiguous(run_events) == [], "seq must be contiguous from 0 per run"
        assert run_events[0].seq == 0
    for event in log:
        assert parse_event(json.loads(event.model_dump_json())) == event, "every event must round-trip"

    [tool_completed] = [event for event in log if event.kind == "tool.call.completed"]
    payload = tool_completed.payload
    assert payload.result_preview == LONG_RESULT[:RESULT_PREVIEW_MAX]
    assert payload.result_size == len(LONG_RESULT.encode())
    assert payload.result_sha256 == hashlib.sha256(LONG_RESULT.encode()).hexdigest()
    session = sessions.session_for(ctx)
    sdk_items = await session.get_items()
    assert any(isinstance(item, dict) and item.get("output") == LONG_RESULT for item in sdk_items), (
        "the SDK session must hold the full, untruncated tool result"
    )

    store.close()
    print("UC1: PASS")


# --------------------------------------------------------------------------------------
# UC2 — the Friday approval (stresses durability + engine substitutability)
# --------------------------------------------------------------------------------------


async def run_uc2(tmp: Path) -> None:
    _banner("UC2 — the Friday approval (ClaimPipeline: validate -> approve interrupt)")
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from agentdeck.adapters.engines.langgraph import LangGraphEngine, resolve_checkpointer

    def _validate(state: dict[str, Any]) -> dict[str, Any]:
        return {"claim_id": state["input"].rsplit(" ", 1)[-1]}

    def _approve(state: dict[str, Any]) -> dict[str, Any]:
        decision = interrupt({"reason": "approval", "claim_id": state["claim_id"], "question": "approve?"})
        return {"decision": decision}

    def _graph() -> Any:
        from typing import TypedDict

        class _State(TypedDict, total=False):
            input: str
            claim_id: str
            decision: str

        g: StateGraph[Any] = StateGraph(_State)
        g.add_node("validate", _validate)
        g.add_node("approve", _approve)
        g.add_edge(START, "validate")
        g.add_edge("validate", "approve")
        g.add_edge("approve", END)
        return g

    def _spec() -> InvocableSpec:
        return InvocableSpec(
            name="ClaimPipeline", kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=_graph()
        )

    db_path = str(tmp / "uc2-events.sqlite3")
    checkpoint_path = str(tmp / "uc2-checkpoints.sqlite3")
    session_id = "s1"

    engine = LangGraphEngine(checkpointer=resolve_checkpointer("sqlite", checkpoint_path))
    store = SqliteEventStore(db_path)
    runtime = build_runtime(engines=[engine], invocables={"ClaimPipeline": _spec()}, store=store)
    app = build_app(runtime)

    print("-- step 1: POST /v2/invocables/ClaimPipeline/chat --")
    opened: list[Any] = []
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as client,
        client.stream(
            "POST", "/v2/invocables/ClaimPipeline/chat", json={"session_id": session_id, "message": "claim 4412"}
        ) as response,
    ):
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                event = parse_event(json.loads(line.removeprefix("data: ")))
                opened.append(event)
                print(f"  {event.kind} seq={event.seq}")
    assert [event.kind for event in opened] == ["run.started", "node.updated", "run.interrupted"]

    print("-- step 2: (simulating) kill -9 the server process --")
    print("-- step 2: (simulating) restart: fresh Runtime/store/engine from the same two files --")
    store.close()
    del runtime, engine, store, app

    from agentdeck.core.status import RunStatus, status_of

    engine2 = LangGraphEngine(checkpointer=resolve_checkpointer("sqlite", checkpoint_path))
    store2 = SqliteEventStore(db_path)
    runtime2 = build_runtime(engines=[engine2], invocables={"ClaimPipeline": _spec()}, store=store2)
    status_ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="n/a", trace_id="t", session_id=session_id)
    assert status_of(await store2.read(status_ctx.log_key, status_ctx)) is RunStatus.WAITING_HUMAN
    print("  status read from disk after restart: WAITING_HUMAN")

    workflow_app = build_workflow_app(runtime2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workflow_app), base_url="http://demo") as client2:
        print("-- step 3: GET /v2/pending --")
        listed = (await client2.get("/v2/pending")).json()
        print(f"  {listed}")
        assert len(listed) == 1
        thread_id = listed[0]["thread_id"]

        print("-- step 4: POST /v2/resume {value: approved} --")
        resumed: list[Any] = []
        async with client2.stream("POST", "/v2/resume", json={"thread_id": thread_id, "value": "approved"}) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    event = parse_event(json.loads(line.removeprefix("data: ")))
                    resumed.append(event)
                    print(f"  {event.kind} seq={event.seq}")
        assert [event.kind for event in resumed] == ["run.resumed", "node.updated", "run.completed"]

        stray = await client2.post("/v2/resume", json={"thread_id": thread_id, "value": "again"})
        assert stray.json() == {"status": "no-op"}
        print("  a stray resume against the completed run: no-op, as expected")

    whole = opened + resumed
    assert check_terminal(whole) is None
    assert check_contiguous(whole) == []
    assert [event.seq for event in whole] == list(range(len(whole))), "seq must not reset across the restart"
    node_updates = [event.payload.node for event in whole if event.kind == "node.updated"]
    assert node_updates == ["validate", "approve"], "validate must not re-run after the restart"
    store2.close()
    print("UC2: PASS")


# --------------------------------------------------------------------------------------
# UC3 — the rude interruption (stresses control + ordering guarantees)
# --------------------------------------------------------------------------------------


class SlowPokeModel(Model):
    """Streams `chunk_count` text deltas with a small sleep before each — enough of a
    window for a signal to land mid-stream without slowing the demo down too much."""

    def __init__(self, chunk_count: int, delay: float) -> None:
        self._chunk_count = chunk_count
        self._delay = delay

    async def stream_response(self, _system_instructions: str | None, _input: Any, *_a: Any, **_k: Any):
        message_id = "msg_slowpoke"
        for i in range(self._chunk_count):
            await asyncio.sleep(self._delay)
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=f"chunk{i} ",
                item_id=message_id,
                logprobs=[],
                output_index=0,
                sequence_number=0,
                type="response.output_text.delta",
            )
        text = "".join(f"chunk{i} " for i in range(self._chunk_count))
        output = [
            ResponseOutputMessage(
                id=message_id,
                content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            )
        ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("the demo's fixtures only stream")


def _slowpoke_spec(chunk_count: int, delay: float) -> InvocableSpec:
    agent = Agent(name="SlowPoke", instructions="stall", model=SlowPokeModel(chunk_count, delay))
    return InvocableSpec(name="SlowPoke", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


async def _gap_recovering_consumer(source: Any, store: Any, log_key: str, ctx: RunContext) -> list[Any]:
    """Trusts the seq invariant: a gap in the live stream is detected the moment it
    arrives and closed by refetching the missing range from the store."""
    seen: dict[int, Any] = {}
    next_expected = 0
    async for event in source:
        if event.seq > next_expected:
            print(f"  !! gap detected: expected seq={next_expected}, got seq={event.seq} — refetching from store")
            for missing in await store.read_run(log_key, event.run_id, ctx, from_seq=next_expected):
                seen[missing.seq] = missing
        seen[event.seq] = event
        next_expected = event.seq + 1
    return [seen[seq] for seq in sorted(seen)]


async def run_uc3_chaos_gap_detection() -> None:
    print("-- chaos test (decision A): drop one mid-run event, recover it from the store --")
    control = MemoryControlPort()
    store = MemoryEventStore()
    runtime = build_runtime(
        engines=[OpenAIAgentsEngine()], invocables={"SlowPoke": _slowpoke_spec(10, 0.0)}, store=store, control=control
    )
    ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="run-chaos", trace_id="t", session_id="s-chaos")

    full_run = [event async for event in runtime.run("SlowPoke", coerce_input("go slow"), ctx)]
    delta_indices = [i for i, event in enumerate(full_run) if event.kind == "text.delta"]
    drop_seq = full_run[delta_indices[len(delta_indices) // 2]].seq

    async def lossy_stream() -> Any:
        for event in full_run:
            if event.seq == drop_seq:
                continue  # the transport silently loses exactly this one event
            yield event

    recovered = await _gap_recovering_consumer(lossy_stream(), store, ctx.log_key, ctx)
    assert recovered == full_run
    assert check_contiguous(recovered) == []
    print("  chaos gap-detection: PASS")


async def run_uc3_cross_process_cancel(tmp: Path) -> None:
    print("-- cross-process cancel: Terminal A streams here, Terminal B cancels in a real subprocess --")
    control_db = str(tmp / "uc3-control.sqlite3")
    events_db = str(tmp / "uc3-events.sqlite3")

    from agentdeck.adapters.control.sqlite import SqliteControlPort

    control = SqliteControlPort(control_db)
    store = SqliteEventStore(events_db)
    # 0.2s/chunk, not the chaos test's 0.0: Terminal B is a real `python -m` subprocess,
    # which costs over a second just importing agentdeck (v1's App included) before it
    # can write the signal — the delay gives that import time to land mid-stream.
    runtime = build_runtime(
        engines=[OpenAIAgentsEngine()], invocables={"SlowPoke": _slowpoke_spec(30, 0.2)}, store=store, control=control
    )
    ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="uc3-cross-process", trace_id="t")

    kinds: list[str] = []
    async for event in runtime.run("SlowPoke", coerce_input("go slow"), ctx):
        kinds.append(event.kind)
        print(f"  [Terminal A] {event.kind} seq={event.seq}")
        if event.kind == "run.started":
            run_id = event.run_id  # addressability: obtained from the stream, not assumed
            print(f"  [Terminal B] agentdeck runs signal {run_id} cancel --control-db {control_db}")
            result = subprocess.run(
                [sys.executable, "-m", "agentdeck.cli", "runs", "signal", run_id, "cancel", "--control-db", control_db],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            assert result.returncode == 0

    assert kinds[-1] == "run.cancelled"
    assert kinds.count("run.cancelled") == 1
    log = await store.read(ctx.log_key, ctx)
    assert check_terminal(log) is None
    assert check_contiguous(log) == []
    store.close()
    print("  cross-process cancel: PASS")


async def run_uc3_replay_and_render(tmp: Path) -> None:
    print("-- replay: the truncated-but-coherent session, through UC1's unedited renderer --")
    control = MemoryControlPort()
    store = MemoryEventStore()
    runtime = build_runtime(
        engines=[OpenAIAgentsEngine()], invocables={"SlowPoke": _slowpoke_spec(30, 0.0)}, store=store, control=control
    )
    ctx = RunContext(tenant=TENANT, principal=PRINCIPAL, run_id="run-replay", trace_id="t", session_id="s-replay")

    delta_count = 0
    async for event in runtime.run("SlowPoke", coerce_input("go slow"), ctx):
        if event.kind == "text.delta":
            delta_count += 1
            if delta_count == 3:
                await control.signal(ctx.run_id, Signal.CANCEL)

    log = await store.read(ctx.log_key, ctx)
    assert check_terminal(log) is None
    assert log[-1].kind == "run.cancelled"
    assert not any(event.kind == "message.completed" for event in log)

    async def lines() -> Any:
        for event in log:
            yield f"data: {event.model_dump_json()}\n\n"

    await render(lines())
    print("  replay: PASS")


async def run_uc3(tmp: Path) -> None:
    _banner("UC3 — the rude interruption (SlowPoke, cross-process cancel + chaos gap-detection)")
    await run_uc3_replay_and_render(tmp)
    await run_uc3_chaos_gap_detection()
    await run_uc3_cross_process_cancel(tmp)
    print("UC3: PASS")


async def main() -> None:
    with TemporaryDirectory(prefix="agentdeck-m0-demo-") as tmp:
        await run_discovery(Path(tmp))
        await run_uc1(Path(tmp))
        await run_uc2(Path(tmp))
        await run_uc3(Path(tmp))
    _banner("ALL PASS — discovery -> UC1 -> UC2 -> UC3 replayed end to end")


if __name__ == "__main__":
    asyncio.run(main())
