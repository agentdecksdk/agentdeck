"""UC3 — "the rude interruption": SlowPoke streams 30 text chunks with small sleeps;
a signal against its ``run_id`` cancels it cleanly, in-process and across a real OS
process boundary; replay after cancel is truncated but coherent; a dropped mid-run event
is detected by its ``seq`` gap and recovered from the store.

Every test below drives ``Runtime`` directly — nothing in the CLI renderer
(``surfaces/cli/chat.py``) changes for this file; it is fed a truncated replay as-is in
``test_uc3_replay_is_truncated_but_coherent_and_the_renderer_copes``. The cancel wiring
lives entirely in ``Runtime._bind``: a ``Runtime`` built with a ``ControlPort``
rebinds ``ctx.gate`` itself, so a caller building a plain ``RunContext`` — including
``surfaces/serve/app.py``'s chat route, also untouched — never has to know a control port
exists. (The chat route's own cancellability isn't exercised here: ``httpx.ASGITransport``
runs a request's whole ASGI call before returning anything, so it cannot interleave a
live signal with an in-flight response — a real ASGI server wouldn't have that limit, but
proving it needs one, which is out of scope for this unit-test suite.)
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

from agents import Agent, Model
from event_log_checks import check_contiguous, check_terminal
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.status import RunStatus, status_of
from agentdeck.runtime.service import Runtime
from agentdeck.surfaces.cli.chat import render

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from agentdeck.core.events import Event
    from agentdeck.core.ports import EventStorePort
    from agentdeck.core.ports.control import ControlPort

CHUNK_COUNT = 30
MESSAGE_ID = "msg_slowpoke"


def _usage() -> ResponseUsage:
    return ResponseUsage(
        input_tokens=1,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens=1,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=2,
    )


def _response(output: list[Any]) -> Response:
    return Response(
        id="resp_slowpoke",
        created_at=0.0,
        model="fake-slowpoke",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=_usage(),
    )


class SlowPokeModel(Model):
    """UC3's deliberately slow fake: 30 text chunks, a small sleep before each — enough of
    a window for a cross-process signal to land mid-stream without slowing the suite."""

    async def stream_response(self, _system_instructions: str | None, _input: Any, *_a: Any, **_k: Any):
        for i in range(CHUNK_COUNT):
            await asyncio.sleep(0.005)
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=f"chunk{i} ",
                item_id=MESSAGE_ID,
                logprobs=[],
                output_index=0,
                sequence_number=0,
                type="response.output_text.delta",
            )
        text = "".join(f"chunk{i} " for i in range(CHUNK_COUNT))
        output = [
            ResponseOutputMessage(
                id=MESSAGE_ID,
                content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            )
        ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("UC3's fixture only streams")


def _spec() -> InvocableSpec:
    agent = Agent(name="SlowPoke", instructions="stall", model=SlowPokeModel())
    return InvocableSpec(name="SlowPoke", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


def _build(control: ControlPort) -> tuple[Runtime, EventStorePort]:
    """A run that polls control at every safe point, which is not the shipped default.

    SlowPoke's whole answer is 30 chunks of 5ms — shorter than the 200ms a gate may reuse an
    answer for (``CONTROL_POLL_INTERVAL``), so a signal sent mid-stream here would be noticed
    after the run had already finished. What these tests are about is *where* a cancel lands
    and what the log looks like afterwards, not how soon the gate hears about it; the read
    bound itself is asserted off an injected clock in ``tests/test_run_control.py``, and the
    default interval runs end to end in ``test_uc3_cross_process_cancel``, whose subprocess
    streams for six seconds.
    """
    store = MemoryEventStore()
    runtime = Runtime([OpenAIAgentsEngine()], store, {"SlowPoke": _spec()}, control=control, control_poll_interval=0.0)
    return runtime, store


async def _gap_recovering_consumer(
    source: AsyncIterator[Event], store: EventStorePort, log_key: str, ctx: RunContext
) -> list[Event]:
    """A minimal consumer that trusts the seq invariant: a gap in the live stream is
    detected the moment it arrives and closed by refetching the missing range from the
    store — proving contiguous seq buys loss detection, not merely ordering."""
    seen: dict[int, Event] = {}
    next_expected = 0
    async for event in source:
        if event.seq > next_expected:
            for missing in await store.read_run(log_key, event.run_id, ctx, from_seq=next_expected):
                seen[missing.seq] = missing
        seen[event.seq] = event
        next_expected = event.seq + 1
    return [seen[seq] for seq in sorted(seen)]


async def test_uc3_cancel_lands_at_next_safe_point_stable_across_20_runs() -> None:
    """Signal CANCEL right as the run opens, 20 times over: every run must still close
    with exactly one terminal event, last, seq contiguous — flakiness here is a real
    ordering bug, not test noise."""
    for trial in range(20):
        control = MemoryControlPort()
        runtime, _store = _build(control)
        ctx = RunContext(namespace="demo", run_id=f"run-{trial}")

        events: list[Event] = []
        async for event in runtime.run(
            "SlowPoke",
            coerce_input("go slow"),
            run_id=(ctx).run_id,
            session_id=(ctx).session_id,
            namespace=(ctx).namespace,
        ):
            events.append(event)
            if event.kind == "run.started":
                await control.signal(ctx.id, Signal.CANCEL)

        kinds = [event.kind for event in events]
        assert check_terminal(events) is None, f"trial {trial}: {kinds}"
        assert check_contiguous(events) == [], f"trial {trial}: {kinds}"
        assert kinds[-1] == "run.cancelled", f"trial {trial}: {kinds}"
        assert kinds.count("run.cancelled") == 1, f"trial {trial}: {kinds}"
        # every text.delta is a whole chunk by construction (translate() never slices one),
        # so "the last delta is complete" holds for free; what matters is nothing trails it
        assert kinds.count("message.completed") == 0, f"trial {trial}: {kinds}"


async def test_uc3_run_cancelled_is_terminal_and_a_followup_signal_is_a_noop() -> None:
    control = MemoryControlPort()
    runtime, store = _build(control)
    ctx = RunContext(namespace="demo", run_id="run-noop")

    events: list[Event] = []
    async for event in runtime.run(
        "SlowPoke", coerce_input("go slow"), run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace
    ):
        events.append(event)
        if event.kind == "run.started":
            await control.signal(ctx.id, Signal.CANCEL)

    assert status_of(events) is RunStatus.CANCELLED
    before = await store.read(ctx.log_key, ctx)

    # Nobody polls the gate once the run is over; a signal against a finished run is a
    # no-op precisely because there is no more checkpoint left to raise on, not because
    # this test re-derives the status machine's rule.
    await control.signal(ctx.id, Signal.CANCEL)
    after = await store.read(ctx.log_key, ctx)
    assert after == before


async def test_uc3_replay_is_truncated_but_coherent_and_the_renderer_copes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    control = MemoryControlPort()
    runtime, store = _build(control)
    ctx = RunContext(namespace="demo", run_id="run-replay", session_id="s-replay")

    delta_count = 0
    async for event in runtime.run(
        "SlowPoke", coerce_input("go slow"), run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace
    ):
        if event.kind == "text.delta":
            delta_count += 1
            if delta_count == 3:  # let a few real chunks land before cutting the run off
                await control.signal(ctx.id, Signal.CANCEL)

    log = await store.read(ctx.log_key, ctx)
    assert check_terminal(log) is None
    assert log[-1].kind == "run.cancelled"
    assert sum(1 for event in log if event.kind == "text.delta") > 0
    assert not any(event.kind == "message.completed" for event in log)  # the message never finished

    async def lines() -> AsyncIterator[str]:
        for event in log:
            yield f"data: {event.model_dump_json()}\n\n"

    await render(lines())  # UC1's unedited renderer must not crash on a truncated run
    out = capsys.readouterr().out
    assert "run.cancelled" in out
    assert MESSAGE_ID not in out  # no message.completed line prints for the unfinished bubble


async def test_uc3_chaos_gap_detection_recovers_from_store() -> None:
    """Drop one mid-run event before "the consumer" sees it; the gap-detecting consumer
    above must notice the seq jump and close it by refetching from the store — the thing
    contiguous seq is for, demonstrated rather than merely argued."""
    control = MemoryControlPort()
    runtime, store = _build(control)
    ctx = RunContext(namespace="demo", run_id="run-chaos", session_id="s-chaos")

    full_run = [
        event
        async for event in runtime.run(
            "SlowPoke",
            coerce_input("go slow"),
            run_id=(ctx).run_id,
            session_id=(ctx).session_id,
            namespace=(ctx).namespace,
        )
    ]
    delta_indices = [i for i, event in enumerate(full_run) if event.kind == "text.delta"]
    assert len(delta_indices) >= 3  # need a genuine mid-run delta to drop, not the opening/terminal event
    drop_seq = full_run[delta_indices[len(delta_indices) // 2]].seq

    async def lossy_stream() -> AsyncIterator[Event]:
        for event in full_run:
            if event.seq == drop_seq:
                continue  # the transport silently loses exactly this one event
            yield event

    recovered = await _gap_recovering_consumer(lossy_stream(), store, ctx.log_key, ctx)
    assert recovered == full_run
    assert check_contiguous(recovered) == []


_SLOWPOKE_PROCESS_A_SCRIPT = """
import asyncio, sys
from agents import Agent, Model
from openai.types.responses import (
    Response, ResponseCompletedEvent, ResponseOutputMessage, ResponseOutputText,
    ResponseTextDeltaEvent, ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.runtime.service import Runtime


class SlowPokeModel(Model):
    async def stream_response(self, *_a, **_k):
        message_id = "msg_slowpoke"
        for i in range(30):
            # 0.2s, not the in-process fixture's 0.005s: Terminal B is a real "python -m"
            # subprocess here, and importing the agentdeck package (v1's App included)
            # costs it over a second before it can even write the signal.
            await asyncio.sleep(0.2)
            yield ResponseTextDeltaEvent(
                content_index=0, delta=f"chunk{i} ", item_id=message_id, logprobs=[],
                output_index=0, sequence_number=0, type="response.output_text.delta",
            )
        text = "".join(f"chunk{i} " for i in range(30))
        usage = ResponseUsage(
            input_tokens=1, input_tokens_details=InputTokensDetails(cached_tokens=0),
            output_tokens=1, output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
            total_tokens=2,
        )
        output = [ResponseOutputMessage(
            id=message_id, content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
            role="assistant", status="completed", type="message",
        )]
        response = Response(
            id="resp_slowpoke", created_at=0.0, model="fake-slowpoke", object="response",
            output=output, parallel_tool_calls=False, tool_choice="auto", tools=[], usage=usage,
        )
        yield ResponseCompletedEvent(response=response, sequence_number=0, type="response.completed")

    async def get_response(self, *_a, **_k):
        raise NotImplementedError("UC3's fixture only streams")


async def main():
    control = SqliteControlPort(sys.argv[1])
    store = SqliteEventStore(sys.argv[2])
    agent = Agent(name="SlowPoke", instructions="stall", model=SlowPokeModel())
    spec = InvocableSpec(name="SlowPoke", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)
    runtime = Runtime([OpenAIAgentsEngine()], store, {"SlowPoke": spec}, control=control)
    ctx = RunContext(run_id=sys.argv[3])
    async for event in runtime.run("SlowPoke", coerce_input("go slow"), run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace):
        print(event.kind, event.run_id, event.seq, flush=True)


asyncio.run(main())
"""


def test_uc3_cross_process_cancel(tmp_path: Any) -> None:
    """The literal UC3 script: Terminal A streams SlowPoke; a second, real OS process —
    the ``agentdeck runs signal`` CLI, not a Python object shared with A — cancels it by
    ``run_id`` alone, obtained from the stream A is already printing (addressability
    demonstrated, not assumed). Unnamespaced on both sides: the CLI has no ``--namespace``
    flag and never will (docs/design/run-identity.md), so it can only ever address an id
    that is byte-identical to a caller's own ``run_id`` — this is that case."""
    control_db = str(tmp_path / "control.sqlite3")
    events_db = str(tmp_path / "events.sqlite3")
    run_id = "uc3-cross-process"

    process_a = subprocess.Popen(
        [sys.executable, "-u", "-c", textwrap.dedent(_SLOWPOKE_PROCESS_A_SCRIPT), control_db, events_db, run_id],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        first_line = process_a.stdout.readline()  # type: ignore[union-attr]
        kind, streamed_run_id, _seq = first_line.split()
        assert kind == "run.started"
        assert streamed_run_id == run_id  # obtained from the stream, exactly what B signals

        terminal_b = subprocess.run(
            [sys.executable, "-m", "agentdeck.cli", "runs", "signal", run_id, "cancel", "--control-db", control_db],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert terminal_b.returncode == 0, terminal_b.stderr

        remaining = process_a.stdout.read()  # type: ignore[union-attr]
        process_a.wait(timeout=10)
    finally:
        if process_a.poll() is None:
            process_a.kill()

    assert process_a.returncode == 0
    kinds = [line.split()[0] for line in [first_line, *remaining.splitlines()] if line.strip()]
    assert kinds[-1] == "run.cancelled"
    assert kinds.count("run.cancelled") == 1

    store = SqliteEventStore(events_db)
    ctx = RunContext(run_id=run_id)

    async def _read_back() -> list[Event]:
        return await store.read_run(ctx.log_key, run_id, ctx)

    logged = asyncio.run(_read_back())
    assert check_terminal(logged) is None
    assert check_contiguous(logged) == []
