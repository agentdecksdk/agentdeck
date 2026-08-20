"""The langgraph engine: ``EnginePort`` over a compiled ``StateGraph``.

``spec.native`` is an *uncompiled* ``StateGraph``  -  this adapter compiles it itself, with
its own checkpointer, and caches the result per invocable name (ADR-D5: an engine's
checkpointer is its own working memory, never shared with or read by an outer ring), so
nothing outside this directory ever sees a ``StateGraph``, a checkpointer, or a
``thread_id``'s raw graph state. ``astream(..., stream_mode=["updates", "custom", "values"])``
maps onto the payloads this adapter yields: a ``{node: patch}`` update becomes
``node.updated`` (an update langgraph reports as ``None``, which is what a node that returned
nothing looks like, becomes an empty patch), a ``{"__interrupt__": (...)}`` update becomes
``run.interrupted`` and ends the stream (the graph suspends there; resuming re-enters the same
``astream`` call with a ``Command(resume=value)``), a ``get_stream_writer()`` write becomes one
namespaced ``custom`` event, and the stream simply ending means the graph reached ``END``.

Both ends of a run are the graph's state: a ``DataBlock`` in is the initial state as posted,
and the final state leaves as a ``DataBlock`` on ``run.completed``  -  structured going in,
structured coming out. Text in keeps the single ``{"input": text}`` channel. That final state
is the last ``values`` chunk rather than a checkpoint read, so a graph compiled without a
checkpointer still reports one.

A gate checkpoint between two ``updates`` chunks (issue #128) is this engine's ``node_boundary``
safe point: nothing is mid-execution there, so a pause lands somewhere well defined. Resuming one
is not a fresh ``start``: langgraph's own idiom for continuing a thread from its checkpoint is an
``astream(None, config)`` call, which is what a *resumed pause* passes instead of the run's
original input  -  see ``start``'s use of ``history`` and ``_continue_from_pause``. An ``interrupt()``
suspension is unrelated and untouched: it resumes with ``Command(resume=value)``, always through
``resume``, never through this path.

The ``StateGraph``'s schema must be a ``TypedDict`` (or pydantic model), never a bare
``dict``: langgraph treats a bare ``dict`` as one opaque channel, so a node's return
replaces the *entire* state instead of merging into it, which would silently break the
shallow-merge every ``node.updated`` promises its readers.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from contextlib import aclosing, nullcontext
from typing import TYPE_CHECKING, Any, ClassVar, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import StateGraph
from langgraph.types import Command
from pydantic import BaseModel

from agentdeck.adapters.engines.langgraph.checkpointer import resolve_checkpointer
from agentdeck.core.content import DataBlock, TextBlock
from agentdeck.core.control import ControlSignalled
from agentdeck.core.events import Custom, NodeUpdated, RunCompleted, RunInterrupted, Usage
from agentdeck.core.ports import EnginePort
from agentdeck.core.status import LIFECYCLE_KINDS
from agentdeck.errors import DOCS_URL, ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.types import StreamMode

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec

_INTERRUPT_KEY = "__interrupt__"
_METADATA_KEY = "__metadata__"
_KNOWN_REASONS = frozenset({"human", "pause", "approval"})
_WORKFLOWS_DOCS = f"{DOCS_URL}/build-your-deck/workflows"

DURABLE_KEY = "durable"
"""``spec.metadata[DURABLE_KEY]``: whether this workflow's state must outlive the process.

Absent  -  a spec built in code that never said  -  leaves the engine's own default checkpointer
in place, which is what a caller wiring ``LangGraphEngine()`` by hand already gets. ``True``
is what makes the configured (sqlite/postgres) checkpointer be resolved *at all*, and only at
the first durable run, so the ``[durability]`` extra  -  needed only for the Postgres backend  -
stays optional for a project that only chats."""

STREAM_CONFIGURABLE_KEY = "agentdeck_stream"
"""``config["configurable"][STREAM_CONFIGURABLE_KEY]``: this run's nodes may stream.

A node that drives an agent of its own checks this before using the SDK's streaming API, so
without it a nested agent's text deltas never reach the custom stream. Always on here, unlike
v1's opt-in: one run produces one canonical stream, and what a consumer does with it is not
the engine's business."""

REPORTER_KEY = "reporter"
"""Where a node finds this run's ``Reporter``: ``config["configurable"]["reporter"]``.

langgraph's own injection channel, used rather than a channel of our own: a node that declares
a ``config: RunnableConfig`` parameter reaches the reporter there, so reporting costs the graph
schema nothing and a workflow that never reports is written exactly as before. Non-scalar
``configurable`` values are excluded from checkpoint metadata by langgraph itself, so a durable
graph does not try to serialize it.

Distinct from ``STREAM_WRITE`` below on purpose: a ``get_stream_writer()`` payload is whatever a
node felt like writing, so it travels as a namespaced ``custom``, while status and progress are
canonical kinds every consumer already understands  -  D10's promotion, taken.
"""
# A list, not a tuple: langgraph switches to ``(mode, chunk)`` chunks on a list specifically,
# and a tuple of the same modes streams the bare single-mode shape instead.
_STREAM_MODES: list[StreamMode] = ["updates", "custom", "values"]

# A ``get_stream_writer()`` write, which no kind describes: the graph author chose the value,
# so it is namespaced ``custom`` rather than a minted kind (D10). Wrapped under one key
# because a write is any JSON value and ``Custom.data`` is an object.
STREAM_WRITE = "langgraph.stream_write"
STREAM_WRITE_KEY = "value"


class LangGraphEngine(EnginePort):
    """Plays ``spec.native`` (an uncompiled ``StateGraph``) through ``astream``.

    ``checkpointer`` is what a non-durable graph compiles around  -  a fresh in-memory one by
    default, never ``resolve_checkpointer("memory")``'s shared instance, because two engines
    must not silently see each other's threads. ``durable_checkpoint`` is the
    ``(backend, url)`` a ``durable`` workflow gets instead, resolved from settings at the
    composition root but built here, lazily, at the first durable run: naming the ``postgres``
    backend must not cost a project that only chats the ``[durability]`` extra.

    ``workspace`` is the scope a run's nodes execute inside  -  injected, because a sandbox is
    a capability rather than an engine concern, and unset for a project whose nodes need none.
    """

    engine: ClassVar[str] = "langgraph"
    suspendable: ClassVar[bool] = True

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        *,
        durable_checkpoint: tuple[str, str] | None = None,
        workspace: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self._checkpointer = checkpointer or MemorySaver()
        self._durable_checkpoint = durable_checkpoint
        self._workspace = workspace
        self._compiled: dict[str, CompiledStateGraph[Any, Any, Any, Any]] = {}

    async def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        thread_id = self._thread_id(ctx)
        # ``resume_run`` re-enters a paused run through this same method (ADR-D5: the engine
        # loads its own execution state, the Runtime does not carry it)  -  its own claim is the
        # ``run.resumed`` this history now ends on, right after the ``run.paused`` this engine
        # wrote. Filtered to ``ctx.run_id``, not just the kind: ``history`` is
        # ``Runtime.run()``'s whole *session* log (``log_key`` is ``session_id or run_id``),
        # read before this run's own claim closes out whatever the session's previous run left
        # behind  -  so an abandoned run's stale ``[..., "run.paused", "run.resumed"]`` tail is
        # still sitting there when a genuinely new run starts on the same session. Without the
        # ``run_id`` filter that stranger's tail reads as this run continuing a pause, and a
        # fresh run's own input is silently discarded in favor of someone else's checkpoint.
        lifecycle = [event.kind for event in history if event.kind in LIFECYCLE_KINDS and event.run_id == ctx.run_id]
        graph_input = (
            await self._continue_from_pause(spec, thread_id)
            if lifecycle[-2:] == ["run.paused", "run.resumed"]
            else _to_graph_input(input)
        )
        # aclosing at every delegation: closing an async generator unwinds its own frame only,
        # so an inner one iterated with a bare `async for` is abandoned to the GC  -  which
        # finalizes it in a fresh context, where a ContextVar reset (the workspace scope
        # ``_drive`` opens) raises instead of releasing.
        async with aclosing(self._drive(spec, graph_input, thread_id, ctx)) as stream:
            async for payload in stream:
                yield payload

    async def _continue_from_pause(self, spec: InvocableSpec, thread_id: str) -> None:
        """``None`` is langgraph's own idiom for continuing a thread from its last checkpoint  -
        what a resumed pause needs, since (unlike an interrupt) there is no value to hand back.
        Returning it here, instead of the run's original input, is what keeps a completed node
        from running twice.

        Refused rather than silently replayed from the entry node when there is nothing to
        continue from: ``durable=False`` never compiles with a checkpointer at all (mirrors the
        ``interrupt()`` refusal in ``_drive``, for the same reason), and a non-durable graph's
        in-memory one (``engine.py``'s own default) does not survive a resume landing in another
        process  -  ADR-D5 makes an engine's checkpointer its own working memory, never shared with
        another engine instance.
        """
        durable = spec.metadata.get(DURABLE_KEY)
        if durable is False:
            raise ConfigError(
                f"{spec.name} paused at a node boundary but is durable=False: with no checkpointer "
                f"the paused run cannot be resumed. Set `durable = True` on the workflow  -  see {_WORKFLOWS_DOCS}",
            )
        checkpointer = self._checkpointer_for(spec)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        if checkpointer is None or await checkpointer.aget_tuple(config) is None:
            raise ConfigError(
                f"{spec.name} paused at a node boundary, but this process has no checkpoint for "
                f"thread {thread_id!r}: a non-durable checkpoint does not survive across processes. "
                f"Set `durable = True` on the workflow, with a durable checkpoint backend  -  see {_WORKFLOWS_DOCS}",
            )
        return None

    async def resume(
        self,
        spec: InvocableSpec,
        thread_id: str,
        value: Any,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        async with aclosing(self._drive(spec, Command(resume=value), thread_id, ctx)) as stream:
            async for payload in stream:
                yield payload

    def _thread_id(self, ctx: RunContext) -> str:
        """Which langgraph thread this run plays on: the session's, or its own.

        A caller that names the thread keeps resuming it  -  ``POST /workflows/X?thread_id=t``
        then ``POST /workflows/X/t/resume`` is two runs on one thread, which ``ctx.run_id``
        could not express. A run with no session falls back to its own id, which a resume
        names back via the ``RunInterrupted`` it got.
        """
        return ctx.session_id or ctx.run_id

    async def _drive(
        self,
        spec: InvocableSpec,
        graph_input: Any,
        thread_id: str,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        """Play ``graph_input`` on ``spec``'s graph, inside whatever scope its nodes need.

        The one place the config is built, so ``start`` and ``resume`` hand a node the same
        reporter and the same streaming permission without either of them mentioning it.
        """
        durable = spec.metadata.get(DURABLE_KEY)
        if durable is True and ctx.session_id is None:
            # A durable graph loads and persists its state by thread, so running one under a
            # thread nobody can name back is a lost run.
            raise ValueError(
                f"{spec.name} is durable=True; a thread_id is required to load/persist checkpointed state "
                f" -  see {_WORKFLOWS_DOCS}",
            )
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                REPORTER_KEY: ctx.reporter,
                STREAM_CONFIGURABLE_KEY: True,
            }
        }
        # The workspace is a ContextVar scope, so the stream it wraps has to be closed from
        # inside it  -  an abandoned generator releases it from the wrong context.
        scope = self._workspace() if self._workspace is not None else nullcontext(None)
        async with scope, aclosing(self._play(self._graph_for(spec), graph_input, config, ctx)) as stream:
            async for payload in stream:
                if isinstance(payload, RunInterrupted) and durable is False:
                    raise ConfigError(
                        f"{spec.name} called interrupt() but is durable=False: with no checkpointer "
                        "the paused run cannot be resumed. Set `durable = True` on the workflow.",
                    )
                yield payload

    def _graph_for(self, spec: InvocableSpec) -> CompiledStateGraph[Any, Any, Any, Any]:
        compiled = self._compiled.get(spec.name)
        if compiled is None:
            if not isinstance(spec.native, StateGraph):
                raise ConfigError(
                    f"{spec.name!r} has no langgraph StateGraph: expected native=StateGraph, got {type(spec.native)}"
                )
            compiled = spec.native.compile(checkpointer=self._checkpointer_for(spec))
            self._compiled[spec.name] = compiled
        return compiled

    def _checkpointer_for(self, spec: InvocableSpec) -> BaseCheckpointSaver | None:
        """This graph's checkpointer. Three answers, because ``durable`` has three states.

        Declared ``False`` means **no checkpointer at all**  -  not an in-memory one. A saver
        keyed by thread would make a second run on a thread resume the first's state instead
        of starting fresh, which is the opposite of what a workflow declaring itself
        non-durable asked for.

        Absent is not the same as ``False``: a spec built in code never said, so it keeps the
        engine's own default (see ``DURABLE_KEY``), which is what a hand-wired
        ``LangGraphEngine()`` already gets and what lets such a graph interrupt at all.

        The configured saver is resolved here and not in ``__init__``: the ``postgres`` saver
        lives in the ``[durability]`` extra, so a composition root that merely names a backend
        must not import one until a workflow that needs it actually runs.
        """
        durable = spec.metadata.get(DURABLE_KEY)
        if durable is False:
            return None
        if durable is True and self._durable_checkpoint is not None:
            return resolve_checkpointer(*self._durable_checkpoint)
        return self._checkpointer

    async def _play(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        graph_input: Any,
        config: RunnableConfig,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        thread_id = config["configurable"]["thread_id"]
        # A values chunk always precedes the first update (the initial state), so the empty
        # default is only ever the state of a graph that ran nothing at all.
        state: Any = {}
        # astream's stub only declares the single-mode shape; multi-mode yields (mode, chunk) tuples.
        stream = cast(
            "AsyncIterator[tuple[str, Any]]",
            # The run context travels as langgraph's own runtime context, which is what reaches
            # a node: `authoring.graphs` compiles a node declaring ``Context[...]`` into one
            # declaring ``runtime``, and reads the carrier back off ``runtime.context`` there.
            # Distinct from ``configurable`` on purpose  -  langgraph draws the same
            # state-vs-runtime-context line, and application data does not belong beside a
            # thread id. Resuming goes through the same call, so a resumed run is resupplied.
            graph.astream(graph_input, config=config, context=ctx, stream_mode=_STREAM_MODES),
        )
        async for mode, chunk in stream:
            if mode == "values":
                state = chunk  # tracked for the terminal event, not an event of its own
            elif mode == "custom":
                yield Custom(name=STREAM_WRITE, data={STREAM_WRITE_KEY: self._as_json(chunk)})
            else:
                interrupted = chunk.get(_INTERRUPT_KEY)
                if interrupted is not None:
                    pause = self._interrupted(interrupted[0], thread_id)
                    # Let langgraph finish before reporting the pause. Returning here instead
                    # abandons ``astream`` mid-flight, and with an *async* checkpointer the
                    # pause is then not written: the resume finds the checkpoint from before
                    # the interrupt, re-runs the node and interrupts all over again, so a
                    # durable workflow silently never resumes. The in-memory saver hides it,
                    # having nothing to await. Draining first also orders the two records the
                    # only safe way round  -  the engine's checkpoint is durable before the
                    # canonical log says the run is waiting on a human.
                    #
                    # A sibling branch in the same superstep that is still running when the
                    # interrupt is detected finishes somewhere in this drain  -  langgraph
                    # reports the pause as soon as one branch asks for it, not once the whole
                    # step is done. Forwarding what the drain turns up (#122) means such a
                    # branch's report is not silently thrown away with the rest of the drain:
                    # its checkpoint write already landed (that is what draining guarantees),
                    # so the canonical log should say so too. The pause itself is still last  -
                    # every other event report the wire renders is well-defined for a run that
                    # then went on to finish; the pause is the one report a client must be able
                    # to treat as "nothing more is coming this call", so it never precedes
                    # events that actually happened.
                    async for payload in self._drain_after_interrupt(stream):
                        yield payload
                    yield pause
                    return  # the graph suspended; its terminal event arrives on resume
                if graph_input is None:
                    # Only a resumed *pause* passes ``None`` (an interrupt resumes with
                    # ``Command(resume=value)``, a fresh start with the converted input)  -  so
                    # this is the one path where langgraph's own re-announcement of the step its
                    # checkpoint was loaded from, marked ``cached``, can turn up. Reporting it
                    # would tell a reader the node that already ran, ran again. An interrupt
                    # resume gets the identical artifact from langgraph today and keeps it
                    # unfiltered  -  the same "not part of this issue" call
                    # ``test_langgraph_fanout_interrupt.py`` already made for it (#122).
                    #
                    # Verified against langgraph 1.2.11: ``pregel/_loop.py`` sets ``cached`` at
                    # the start of the tick that re-enters a checkpoint, for every task with
                    # pending writes from before, and ``pregel/_io.py`` renders it onto this key.
                    # It is not part of ``UpdatesStreamPart``'s public contract (that docstring
                    # names ``__metadata__`` but not ``cached``), so a version bump is the one
                    # thing that can move this seam: if the key disappears, a resumed run goes
                    # back to re-reporting the pre-pause node as a duplicate ``node.updated``; if
                    # its meaning shifts, a genuine update could be swallowed instead. Both are
                    # what ``tests/test_langgraph_node_boundary_pause.py`` would catch.
                    metadata = chunk.get(_METADATA_KEY)
                    if isinstance(metadata, Mapping) and metadata.get("cached"):
                        continue
                for node, patch in chunk.items():
                    yield NodeUpdated(node=node, state_patch=self._as_patch(patch, node))
                try:
                    # Between two ``updates`` chunks, never inside one: this superstep's writes
                    # are already checkpointed, so a pause honored here lands somewhere well
                    # defined (``node_boundary``) rather than mid-node.
                    await ctx.gate.checkpoint("node_boundary")
                except ControlSignalled as signalled:
                    for payload in signalled.payloads:
                        yield payload
                    return
        yield RunCompleted(
            output=[DataBlock(data=self._as_data(state, "final state"))],
            usage=Usage(input_tokens=0, output_tokens=0),
        )

    async def _drain_after_interrupt(
        self, stream: AsyncIterator[tuple[str, Any]]
    ) -> AsyncGenerator[KnownPayload, None]:
        """The rest of ``stream`` once one branch has already asked to pause.

        Reported the same way the main loop would report it  -  ``NodeUpdated`` per node,
        ``Custom`` per stream write  -  except a second interrupt in the same superstep is not
        one this engine acts on: ``_play`` already reports only the first (``_interrupted``
        reads ``interrupt[0]``), so two branches interrupting at once is out of scope here
        rather than silently mis-paired with the wrong pause. A trailing ``values`` chunk is
        the graph's state at drain time, not an event any consumer reads  -  the run that
        produced it never reaches ``RunCompleted``, so there is nothing to attach it to.
        """
        async for mode, chunk in stream:
            if mode == "values":
                continue
            if mode == "custom":
                yield Custom(name=STREAM_WRITE, data={STREAM_WRITE_KEY: self._as_json(chunk)})
                continue
            for node, patch in chunk.items():
                if node == _INTERRUPT_KEY:
                    continue
                yield NodeUpdated(node=node, state_patch=self._as_patch(patch, node))

    def _interrupted(self, interrupt: Any, thread_id: str) -> RunInterrupted:
        value = interrupt.value
        reason = value.get("reason") if isinstance(value, Mapping) else None
        return RunInterrupted(
            interrupt_id=str(interrupt.id),
            reason=reason if reason in _KNOWN_REASONS else "human",
            payload=self._as_data(value, "interrupt"),
            thread_id=thread_id,
        )

    def _as_patch(self, patch: Any, node: str) -> dict[str, Any]:
        """One node's state update, where *no* update is a legitimate answer.

        langgraph reports a node that changed nothing  -  the side-effect-only node that logs or
        notifies and returns ``None``, and equally one returning ``{}``  -  as ``{node: None}``.
        An absent patch is not a malformed one, so it is the empty patch rather than a refusal.
        """
        return {} if patch is None else self._as_data(patch, node)

    def _as_data(self, value: Any, source: str) -> dict[str, Any]:
        """One node update / interrupt payload / graph state as the JSON object an event
        carries and a store writes.

        Crude on purpose (M0): the value must be a plain mapping to round-trip through the
        event schema's ``dict[str, Any]`` fields, so a graph returning something else is
        refused rather than silently misserialized.
        """
        if not isinstance(value, Mapping):
            raise ConfigError(f"langgraph engine (M0) only supports dict-shaped {source} values, got {type(value)}")
        return self._as_json(dict(value))

    def _as_json(self, value: Any) -> Any:
        """``value`` as plain JSON data.

        A leaf JSON cannot hold becomes its ``str()``, non-finite floats included
        (``parse_constant`` catches the ``NaN``/``Infinity`` tokens that ``default`` never
        sees, because those leaves *are* floats): the same fidelity ceiling the previous
        ``str(dict(values))`` had for the whole state, so a graph that completed before does
        not start failing here.
        """
        return json.loads(json.dumps(value, default=self._leaf), parse_constant=str)

    def _leaf(self, value: Any) -> Any:
        """A single value JSON cannot carry, as data.

        A pydantic model or a dataclass is a state channel a node legitimately returns, so it
        reaches a consumer as the object it is rather than as its ``repr``; ``__dict__`` covers
        the plain object a node built by hand. Everything else is ``str()``  -  the ceiling.
        """
        if isinstance(value, BaseModel):
            return value.model_dump()
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return dataclasses.asdict(value)
        if hasattr(value, "__dict__"):
            return value.__dict__
        return str(value)


def _to_graph_input(input: Input) -> dict[str, Any]:
    """A graph's input is its state, so a single ``DataBlock`` *is* that state; text keeps
    the one-channel shape (``{"input": text}``) a text-in workflow was written against."""
    data = [block for block in input if isinstance(block, DataBlock)]
    if data:
        if len(input) != 1:
            raise ConfigError("langgraph engine: a state-shaped input is one data block and nothing else")
        state = data[0].data
        if not isinstance(state, dict):
            raise ConfigError(f"langgraph engine: a data block input must be a JSON object, got {type(state)}")
        return dict(state)
    # Images/resources are a follow-up, not a silent drop  -  better to raise now than feed a
    # node a blank string.
    texts = [block.text for block in input if isinstance(block, TextBlock)]
    if len(texts) != len(input):
        raise ConfigError("langgraph engine only supports text or data input blocks")
    return {"input": "\n".join(texts)}


__all__ = [
    "DURABLE_KEY",
    "REPORTER_KEY",
    "STREAM_CONFIGURABLE_KEY",
    "STREAM_WRITE",
    "STREAM_WRITE_KEY",
    "LangGraphEngine",
]
