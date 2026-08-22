"""The runs the contract suite plays, and the two types the harness passes around.

One flat list: adding an engine means appending its cases, and every invariant then runs
against it unchanged  -  that is what makes them engine contracts rather than stub tests.
A case declares how its run ends so the suite knows which invariant to hold it to; nothing
else about the engine is visible to the tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from case_types import Case
from openai_agents_cases import openai_agents_cases

from agentdeck.adapters.executors.stub import StubExecutor, stub_spec
from agentdeck.core.content import TextBlock
from agentdeck.core.events import (
    MessageCompleted,
    RunCompleted,
    RunInterrupted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageReported,
)
from agentdeck.core.invocable import InvocableKind

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
USAGE = Usage(input_tokens=7, output_tokens=11)
SHA = "ab" * 32


def _stub_cases() -> list[Case]:
    """Every shape a run can take. The last three are misbehaving engines on purpose: the
    Runtime has to close a run its engine left open, and refuse to let one reopen a closed one."""
    deltas = [TextDelta(message_id="m1", text=chunk) for chunk in ("one ", "two ", "three")]
    return [
        Case(
            id="stub/completes",
            executor=StubExecutor(),
            spec=stub_spec(
                "Chatty",
                *deltas,
                MessageCompleted(message_id="m1", text="one two three"),
                RunCompleted(output=[TextBlock(text="one two three")], usage=USAGE),
            ),
            ends="terminal",
        ),
        Case(
            id="stub/calls-a-tool",
            executor=StubExecutor(),
            spec=stub_spec(
                "Looker",
                ToolCallStarted(call_id="c1", tool="lookup_shipment", args={"id": "4412"}),
                ToolCallCompleted(
                    call_id="c1", tool="lookup_shipment", result_preview="damaged", result_size=7, result_sha256=SHA
                ),
                MessageCompleted(message_id="m1", text="it was damaged"),
                RunCompleted(output=[TextBlock(text="it was damaged")], usage=USAGE),
            ),
            ends="terminal",
        ),
        Case(
            id="stub/interrupts",
            executor=StubExecutor(),
            spec=stub_spec(
                "Approver",
                TextDelta(message_id="m1", text="needs a signature"),
                RunInterrupted(interrupt_id="i1", reason="approval", payload={"request": "tue 9am"}, thread_id="t1"),
                kind=InvocableKind.WORKFLOW,
            ),
            ends="suspended",
        ),
        Case(
            id="stub/raises-midstream",
            executor=StubExecutor(),
            spec=stub_spec("Boom", TextDelta(message_id="m1", text="almost"), RuntimeError("engine blew up")),
            ends="terminal",
        ),
        Case(
            id="stub/stops-without-a-terminal-event",
            executor=StubExecutor(),
            spec=stub_spec("Quitter", TextDelta(message_id="m1", text="and then nothing")),
            ends="terminal",
        ),
        Case(
            id="stub/yields-after-a-terminal-event",
            executor=StubExecutor(),
            spec=stub_spec(
                "Chatterbox",
                MessageCompleted(message_id="m1", text="done"),
                RunCompleted(output=[TextBlock(text="done")], usage=USAGE),
                UsageReported(model="fake", usage=USAGE),
                RunCompleted(output=[TextBlock(text="done twice")], usage=USAGE),
            ),
            ends="terminal",
        ),
    ]


CASES: list[Case] = _stub_cases() + openai_agents_cases()
