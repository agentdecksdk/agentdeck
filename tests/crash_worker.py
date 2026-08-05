"""The two processes ``test_crash_reconciliation.py`` needs to kill one and restart the other.

Started as ``python -u tests/crash_worker.py <victim|successor> <dir>``, both over the
SQLite event log in ``<dir>``:

- ``victim`` plays one turn and blocks forever the instant the log holds that turn's answer,
  so the test can SIGKILL it with the engine's own copy of the conversation never made
  durable — a real death between the two writes, not a tidy one between two turns.
- ``successor`` is the restart: a new process, an empty engine session, the dead one's log.
  It plays the next turn and prints the transcript its model was actually handed, which is
  the only place the repair is observable from outside the process.

Both sides script the same conversation, so the model, the spec and the transcript reader
live here rather than in the test module.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, Model
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.core.events import Event

TENANT = "demo"
PRINCIPAL = "user:demo"
SESSION_ID = "crash-session"
AGENT = "Rememberer"

# A conversation that cannot be answered from the current turn alone: turn 2 is only
# answerable if turn 1 survived the crash.
QUESTION_1 = "remember the code violet-19"
ANSWER_1 = "noted, the code is violet-19"
QUESTION_2 = "what was the code?"
ANSWER_2 = "violet-19"

VICTIM_RUN = "victim-run"
SUCCESSOR_RUN = "successor-run"

# The log write the victim dies on: at that instant its answer is durable in the log and
# the engine session holding it is about to go away with the process.
STALL_KIND = "message.completed"
MARKER = "answered"

# A turn that outlives this is wedged, not slow — raising exits non-zero, which the test
# reads as a failure instead of waiting out its own subprocess timeout.
TURN_TIMEOUT = 60.0


def events_db(root: Path) -> Path:
    return root / "events.sqlite3"


def marker_file(root: Path) -> Path:
    return root / MARKER


def context(run_id: str) -> RunContext:
    return RunContext(tenant=TENANT, principal=PRINCIPAL, run_id=run_id, trace_id=f"t-{run_id}", session_id=SESSION_ID)


def transcript_of(items: Sequence[Any]) -> list[list[str]]:
    """``[role, text]`` for every message in a list of SDK items — the model's input, a
    session's contents, either way. Lists, not tuples, so a subprocess can print it as JSON.
    """
    transcript: list[list[str]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        else:
            text = ""
        transcript.append([str(item["role"]), text])
    return transcript


class ScriptedModel(Model):
    """Answers with one fixed line and keeps every input it was handed.

    ``inputs`` is the proof a reconciliation worked: what the model actually received, not
    what the adapter believes it sent.
    """

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.inputs: list[list[Any]] = []

    async def stream_response(self, _system_instructions: str | None, input: Any, *_a: Any, **_k: Any):
        self.inputs.append(list(input) if isinstance(input, list) else [input])
        message = ResponseOutputMessage(
            id=f"msg_{len(self.inputs)}",
            content=[ResponseOutputText(annotations=[], text=self._answer, type="output_text")],
            role="assistant",
            status="completed",
            type="message",
        )
        yield ResponseCompletedEvent(response=_response([message]), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("the crash fixtures only stream")


class StallingStore(SqliteEventStore):
    """Blocks forever once the log holds a ``STALL_KIND`` event, so the test can kill this
    process at exactly that point. Copied in spirit from the concurrency suite's own
    stalling store: a SIGKILL is the only way out, which is the point."""

    def __init__(self, path: Path, marker: Path) -> None:
        super().__init__(path)
        self._marker = marker

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        await super().append(log_key, events, ctx)
        if any(event.kind == STALL_KIND for event in events):
            self._marker.touch()
            while True:
                await asyncio.sleep(0.05)


def spec(model: Model) -> InvocableSpec:
    agent = Agent(name=AGENT, instructions="remember what you are told", model=model)
    return InvocableSpec(name=AGENT, kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


def runtime_over(store: SqliteEventStore, model: Model) -> Runtime:
    """A whole stack over one log: fresh engine session state, as a new process always has."""
    return Runtime([OpenAIAgentsEngine(ExecutionStore())], store, {AGENT: spec(model)})


def _response(output: list[Any]) -> Response:
    return Response(
        id="resp_crash",
        created_at=0.0,
        model="fake-crash",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=ResponseUsage(
            input_tokens=1,
            input_tokens_details=InputTokensDetails(cached_tokens=0),
            output_tokens=1,
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
            total_tokens=2,
        ),
    )


async def _victim(root: Path) -> None:
    runtime = runtime_over(StallingStore(events_db(root), marker_file(root)), ScriptedModel(ANSWER_1))
    async for _event in runtime.run(AGENT, coerce_input(QUESTION_1), context(VICTIM_RUN)):
        pass  # the store blocks this turn once the answer is durable; the kill is the only exit


async def _successor(root: Path) -> None:
    model = ScriptedModel(ANSWER_2)
    runtime = runtime_over(SqliteEventStore(events_db(root)), model)
    async with asyncio.timeout(TURN_TIMEOUT):
        async for _event in runtime.run(AGENT, coerce_input(QUESTION_2), context(SUCCESSOR_RUN)):
            pass
    print(json.dumps(transcript_of(model.inputs[-1])))


async def main() -> None:
    mode, root = sys.argv[1], Path(sys.argv[2])
    if mode == "victim":
        await _victim(root)
    elif mode == "successor":
        await _successor(root)
    else:
        raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    asyncio.run(main())
