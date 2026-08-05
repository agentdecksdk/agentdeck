"""The two processes ``test_crash_reconciliation.py`` needs to kill one and restart the other.

Started as ``python -u tests/crash_worker.py <victim|successor> <dir>``. Both open the same
two files in ``<dir>`` — the SQLite event log and a *durable* engine session — because a gap
between the two writes only exists if the second store survives the process:

- ``victim`` finishes turn 1 into both stores, then blocks on turn 2's opening log write, so
  the test can SIGKILL it with the log holding a question the session on disk does not. That
  is the ADR window, across processes, and the kill is what puts it there.
- ``successor`` is the restart: a new process on that log and that session file. It plays the
  next turn and prints the transcript its model was actually handed, which is the only place
  the repair is observable from outside the process.

Both sides script the same conversation, so the model, the spec and the transcript reader
live here rather than in the test module.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, Model, SQLiteSession
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

# A conversation that cannot be answered from the current turn alone: everything after turn 1
# is only answerable if turn 1 survived the crash.
QUESTION_1 = "remember the code violet-19"
ANSWER_1 = "noted, the code is violet-19"
QUESTION_2 = "what was the code?"
QUESTION_3 = "and is it still valid?"
ANSWER_3 = "yes, violet-19 is still valid"

FIRST_RUN = "victim-turn-1"
KILLED_RUN = "victim-turn-2"
SUCCESSOR_RUN = "successor-turn-3"

# Where the victim dies: the log append of the second turn's own opening event. At that
# instant the log holds that turn's question and the durable session does not — the window
# the whole exercise is about — and the SDK has not been called, so nothing can write it.
STALL_RUN = KILLED_RUN
STALL_KIND = "run.started"
MARKER = "asked"

# A turn that outlives this is wedged, not slow — raising exits non-zero, which the test
# reads as a failure instead of waiting out its own subprocess timeout.
TURN_TIMEOUT = 60.0


def events_db(root: Path) -> Path:
    return root / "events.sqlite3"


def session_db(root: Path) -> Path:
    return root / "session.sqlite3"


def marker_file(root: Path) -> Path:
    return root / MARKER


def session_key() -> str:
    return f"{TENANT}:{SESSION_ID}"


def durable_session(root: Path) -> SQLiteSession:
    """The engine's execution state as a file both processes (and the test) can open."""
    return SQLiteSession(session_key(), session_db(root))


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


class DurableSessions(ExecutionStore):
    """Execution state in a file instead of memory.

    ``ExecutionStore``'s own fallback is an in-process ``SQLiteSession`` on ``:memory:``, which
    evaporates on any exit at all — with it, a killed process and a cleanly finished one leave
    an identical (empty) session behind, and there is no gap between the two writes left to
    test. A file keeps what the victim really did write, so what it *didn't* write is the only
    thing missing.
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._path = session_db(root)
        self._open: dict[str, SQLiteSession] = {}

    def session_for(self, ctx: RunContext) -> SQLiteSession:
        key = f"{ctx.tenant}:{ctx.log_key}"
        session = self._open.get(key)
        if session is None:
            session = SQLiteSession(key, self._path)
            self._open[key] = session
        return session


class StallingStore(SqliteEventStore):
    """Blocks forever once the log holds ``STALL_RUN``'s ``STALL_KIND`` event, so the test can
    kill this process at exactly that point. Copied in spirit from the concurrency suite's own
    stalling store: a SIGKILL is the only way out, which is the point."""

    def __init__(self, path: Path, marker: Path) -> None:
        super().__init__(path)
        self._marker = marker

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        await super().append(log_key, events, ctx)
        if any(event.run_id == STALL_RUN and event.kind == STALL_KIND for event in events):
            self._marker.touch()
            while True:
                await asyncio.sleep(0.05)


def spec(model: Model) -> InvocableSpec:
    agent = Agent(name=AGENT, instructions="remember what you are told", model=model)
    return InvocableSpec(name=AGENT, kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


def runtime_over(store: SqliteEventStore, sessions: ExecutionStore, model: Model) -> Runtime:
    """A whole stack over one log and one durable session — a server, as far as a turn cares."""
    return Runtime([OpenAIAgentsEngine(sessions)], store, {AGENT: spec(model)})


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
    """Turn 1 all the way into both stores, then turn 2 up to its first log write and no
    further: the log has the new question, the session on disk still ends at turn 1."""
    runtime = runtime_over(
        StallingStore(events_db(root), marker_file(root)), DurableSessions(root), ScriptedModel(ANSWER_1)
    )
    async with asyncio.timeout(TURN_TIMEOUT):
        async for _event in runtime.run(AGENT, coerce_input(QUESTION_1), context(FIRST_RUN)):
            pass
    async for _event in runtime.run(AGENT, coerce_input(QUESTION_2), context(KILLED_RUN)):
        pass  # the store blocks on this turn's opening log write; the kill is the only exit


async def _successor(root: Path) -> None:
    """The restart: same log, same session file, a new turn — and it prints what its model
    was handed, which is the only place the repair is visible from outside."""
    model = ScriptedModel(ANSWER_3)
    runtime = runtime_over(SqliteEventStore(events_db(root)), DurableSessions(root), model)
    async with asyncio.timeout(TURN_TIMEOUT):
        async for _event in runtime.run(AGENT, coerce_input(QUESTION_3), context(SUCCESSOR_RUN)):
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
