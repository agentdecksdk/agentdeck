"""Which checkpointer a graph is compiled around, and what each answer costs a second run.

``durable`` has three states and they are not two. Declared ``False`` must mean *no
checkpointer*  -  an in-memory one keyed by thread makes a second run on that thread resume
the first run's state, which is the opposite of what a workflow declaring itself
non-durable asked for. v1 compiled such a graph with no saver at all
(``BaseWorkflow.build``); this pins that the adapter still does.

Absent is the third state and is deliberately not ``False``: a spec built in code never
said, so it keeps the engine's own default  -  which is what lets a hand-wired
``LangGraphExecutor()`` interrupt at all, and what the contract suite's interrupt case runs on.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, TypedDict

import live_stores
import pytest
from langgraph.graph import END, START, StateGraph

from agentdeck.adapters.executors.langgraph import LangGraphExecutor
from agentdeck.adapters.executors.langgraph.checkpointer import resolve_checkpointer
from agentdeck.core.content import DataBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import RunCompleted
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import DOCS_URL, StoreError
from agentdeck.runtime.discovery import DURABLE_KEY

if TYPE_CHECKING:
    from pathlib import Path

    from agentdeck.core.content import Input


class _State(TypedDict, total=False):
    input: str
    seen: list[str]


def _accumulate(state: _State) -> _State:
    # Reads what is already there, so a resumed thread is visible in the output rather than
    # having to be inferred from the checkpointer's internals.
    return {"seen": [*state.get("seen", []), state.get("input", "")]}


def _spec(metadata: dict[str, Any]) -> InvocableSpec:
    graph: StateGraph[Any] = StateGraph(_State)
    graph.add_node("acc", _accumulate)
    graph.add_edge(START, "acc")
    graph.add_edge("acc", END)
    return InvocableSpec(
        name="Accumulator",
        kind=InvocableKind.WORKFLOW,
        executor=LangGraphExecutor.name,
        native=graph,
        metadata=metadata,
    )


def _ctx(run_id: str) -> RunContext:
    # One session_id across both runs: that is the thread a second turn would resume.
    return RunContext(namespace="acme", run_id=run_id, session_id="thread-1")


async def _seen(engine: LangGraphExecutor, spec: InvocableSpec, text: str, run_id: str) -> list[str]:
    input: Input = [DataBlock(data={"input": text})]
    payloads = [payload async for payload in engine.execute(spec, input, [], _ctx(run_id))]
    terminal = payloads[-1]
    assert isinstance(terminal, RunCompleted), terminal
    block = terminal.output[0]
    assert isinstance(block, DataBlock)
    return block.data["seen"]


async def test_a_non_durable_workflow_starts_fresh_on_a_thread_it_already_ran() -> None:
    """``durable=False`` twice on one thread is two independent runs, not a resume.

    The engine instance is shared on purpose: one process serving two requests is exactly
    where an in-memory saver keyed by thread would carry state across.
    """
    engine = LangGraphExecutor()
    spec = _spec({DURABLE_KEY: False})

    first = await _seen(engine, spec, "a", "r-1")
    second = await _seen(engine, spec, "b", "r-2")

    assert first == ["a"]
    assert second == ["b"], f"the second run resumed the first's state: {second}"


async def test_a_durable_workflow_with_no_session_id_raises_naming_the_docs() -> None:
    """A durable graph loads and persists its state by thread; a run with nothing to name that
    thread by is a lost run, refused before it starts rather than compiled with no checkpoint."""
    engine = LangGraphExecutor()
    spec = _spec({DURABLE_KEY: True})
    ctx = RunContext(namespace="acme", run_id="r-1", session_id=None)

    with pytest.raises(ValueError, match="thread_id") as excinfo:
        async for _ in engine.execute(spec, [DataBlock(data={"input": "a"})], [], ctx):
            pass

    assert f"{DOCS_URL}/build-your-deck/workflows" in str(excinfo.value)


async def test_a_spec_that_never_declared_durable_keeps_the_engines_own_checkpointer() -> None:
    """Absent metadata is not ``False``: a code-built spec keeps the engine default.

    Without it a hand-wired ``LangGraphExecutor()`` could not interrupt, which the contract
    suite's interrupt case depends on  -  so this pins the distinction the fix rests on.
    """
    engine = LangGraphExecutor()
    spec = _spec({})

    first = await _seen(engine, spec, "a", "r-1")
    second = await _seen(engine, spec, "b", "r-2")

    assert first == ["a"]
    assert second == ["a", "b"], f"the engine default was not in place: {second}"


# Nothing listens on port 1, so a connection fails immediately with the shape of a database
# gone unreachable  -  the same case ``test_postgres_store.py`` uses, without needing a live
# Postgres either locally or in CI. The password is a distinct string (not "postgres", which
# could coincidentally appear elsewhere) so a leak into the raised message is unambiguous.
_UNREACHABLE_DSN = "postgresql://agentdeck:s3cr3t-pw@127.0.0.1:1/nope"


async def test_an_unwritable_sqlite_checkpoint_path_raises_a_store_error(tmp_path: Path) -> None:
    """A bare ``sqlite3.OperationalError`` names neither the setting nor the path agentdeck
    resolved it to  -  the event store already answers this class of failure with
    ``StoreError``, and the checkpointer must too."""
    locked_dir = tmp_path / "no-permission"
    locked_dir.mkdir()
    locked_dir.chmod(0o000)
    checkpoint_path = locked_dir / "checkpoints.sqlite3"

    try:
        with pytest.raises(StoreError) as raised:
            resolve_checkpointer("sqlite", str(checkpoint_path))
    finally:
        locked_dir.chmod(0o700)

    assert "AGENTDECK_CHECKPOINT" in str(raised.value)
    assert str(checkpoint_path) in str(raised.value)
    assert isinstance(raised.value.__cause__, sqlite3.Error)


async def test_a_postgres_checkpoint_with_an_unreachable_dsn_raises_a_store_error() -> None:
    """Same gap on the other durable backend: a DSN nothing answers must not hand the caller
    a raw ``psycopg.OperationalError``.

    Unlike the sqlite path, the message never names the DSN  -  a DSN can carry a password,
    and a leaked one would land in every log line and traceback that carries this error.
    """
    psycopg = live_stores.require_psycopg()

    with pytest.raises(StoreError) as raised:
        resolve_checkpointer("postgres", _UNREACHABLE_DSN)

    assert "AGENTDECK_CHECKPOINT" in str(raised.value)
    assert "s3cr3t-pw" not in str(raised.value)
    assert isinstance(raised.value.__cause__, psycopg.Error)
