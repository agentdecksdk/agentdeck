"""``Deck`` — the v3 composition root: agents, workflows, skills and MCP servers become one
catalog, either handed in directly or discovered from a project directory.

    from agentdeck.authoring import Agent
    from agentdeck.deck import Deck

    booking_agent = Agent(name="booking", instructions="...", tools=[find_slots])
    deck = Deck(agents=[booking_agent], skills="./skills", mcp=".mcp.json")
    deck.build()

    async with deck:
        result = await deck.run("booking", "hello")

Two constructors, one primitive: ``Deck(...)`` (code-first) and ``Deck.from_project(path)``
(today's ``.agentdeck/`` directory layout, unchanged) — ``from_project`` discovers the same four
arguments the plain constructor takes and hands them to it, so there is exactly one catalog
mechanism underneath either front door.

Lifecycle: ``NEW -> build() -> BUILT -> (async with) -> OPEN -> CLOSED``. ``build()`` validates
every name a catalog references (skills, MCP servers, workflow-as-tool) and compiles every
agent/workflow to an ``InvocableSpec`` — reading local files, never the network, and idempotent.
The catalog is immutable from construction: :attr:`agents` and :attr:`workflows` are read-only
mappings, so nothing after ``build()`` can invalidate what it already checked. Opening starts
what ``build()`` deliberately left alone — the MCP lifecycle, the Runtime's engines and event
store — and closing tears down only what this Deck itself started (the ownership rule:
configuration this Deck instantiated is its to close; an object the caller constructed and
handed in stays the caller's).
"""

from __future__ import annotations

import uuid
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine, SessionFactory
from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle
from agentdeck.authoring.agent import Agent
from agentdeck.authoring.interrupts import interrupt_result
from agentdeck.authoring.skills import skills_resolver
from agentdeck.authoring.timers import wake_at_of
from agentdeck.authoring.workflow import Workflow
from agentdeck.composition import (
    build_runtime,
    resolve_checkpoint,
    resolve_control_port,
    resolve_event_store,
    resolve_run_settings,
)
from agentdeck.core.content import DataBlock, TextBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.events import Custom, NodeUpdated, RunCompleted, RunInterrupted
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.mcp import MCP
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.runtime.registry import PROJECT_DIR, PluginRegistry, mount_project_dir
from agentdeck.runtime.settings import Settings, get_settings
from agentdeck.skills import Skills

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence

    from agents.memory.session import Session
    from agents.tool import FunctionTool

    from agentdeck.authoring.interrupts import InterruptResult
    from agentdeck.core.events import Event, Usage
    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import EnginePort, EventStorePort
    from agentdeck.core.status import RunStatus
    from agentdeck.runtime.service import PendingRun, Runtime

# The two engine names a Deck's catalog always targets — read off each engine's own ``ClassVar``,
# never an instance, so ``build()`` can validate "an engine is registered" without constructing
# anything that could touch the network. See the module docstring's lifecycle note.
_DEFAULT_ENGINE_NAMES: tuple[str, str] = (OpenAIAgentsEngine.engine, LangGraphEngine.engine)

# Duplicated from ``app.py`` rather than imported: v1's `App` is deleted in the same effort this
# class replaces it for, so importing from a module about to disappear would only have to be
# undone again. See the openai-agents engine's own copy for why the constant is spelled out
# rather than imported from there.
_LEGACY_STRUCTURED_OUTPUT = "openai_agents.structured_output"

_State = Literal["NEW", "BUILT", "OPEN", "CLOSED"]


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError(f"due_resumes/tick require a timezone-aware `now`; got naive {now!r}.")
    return now


class TurnResult:
    """One agent turn's outcome, assembled from its own ``run.completed`` — never the SDK's
    own result object, so a caller depends on agentdeck's event schema rather than on
    whichever engine ran the turn.

    ``run_id`` (and ``session_id``, for a conversational turn) name the run this came from,
    so a caller who wants more than ``output`` and ``usage`` can read the rest of it back
    from the event log instead of this object growing a field for everything the log already
    carries.
    """

    __slots__ = ("output", "run_id", "session_id", "usage")

    def __init__(self, *, output: Any, usage: Usage, run_id: str, session_id: str | None = None) -> None:
        self.output = output
        self.usage = usage
        self.run_id = run_id
        self.session_id = session_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TurnResult):
            return NotImplemented
        return (self.output, self.usage, self.run_id, self.session_id) == (
            other.output,
            other.usage,
            other.run_id,
            other.session_id,
        )

    def __repr__(self) -> str:
        return f"TurnResult(output={self.output!r}, usage={self.usage!r}, run_id={self.run_id!r}, session_id={self.session_id!r})"


def _new_context(session_id: str | None = None) -> RunContext:
    """A context for the internal ports that still take one — the execution store, the event
    store. The Runtime does not: it takes run options and mints its own.
    """
    return RunContext(run_id=str(uuid.uuid4()), session_id=session_id)


async def _turn_result(events: AsyncGenerator[Event, None]) -> TurnResult:
    """A run's own ``run.completed`` (plus whatever it names, en route), as a :class:`TurnResult`.

    Drains ``events`` to its natural end rather than returning the moment ``run.completed``
    is seen — closing the Runtime's generator any earlier throws ``GeneratorExit`` into it one
    line before it notices its own terminal event, recording a spurious ``run.cancelled`` right
    after a run that in fact finished cleanly.

    Raises if the stream ends without one: the engine's own exception already reached the
    caller in that case, so the only way this is hit is a run suspended by a pause or a cancel.
    """
    structured: Any = None
    result: TurnResult | None = None
    async with aclosing(events):
        async for event in events:
            payload = event.payload
            if isinstance(payload, Custom) and payload.name == _LEGACY_STRUCTURED_OUTPUT:
                structured = payload.data.get("output")
            elif isinstance(payload, RunCompleted):
                data = next((block.data for block in payload.output if isinstance(block, DataBlock)), None)
                if data is not None:
                    output = data
                elif structured is not None:
                    output = structured
                else:
                    output = "".join(block.text for block in payload.output if isinstance(block, TextBlock))
                result = TurnResult(
                    output=output, usage=payload.usage, run_id=event.run_id, session_id=event.session_id
                )
    if result is None:
        raise RuntimeError(
            "the run ended without completing (paused or cancelled) — resume it with "
            "Deck.resume, or inspect the event log for what happened."
        )
    return result


async def _workflow_result(events: AsyncGenerator[Event, None]) -> tuple[Any, bool]:
    """A workflow run's final state (or the interrupt it paused on), plus whether the graph
    actually did anything for this call.

    Mirrors ``surfaces/serve/compat.py``'s own ``_terminal`` (duplicated rather than imported,
    for the reason above): a lost resume claim or a thread already at ``END`` both produce an
    empty or update-free stream, and ``applied`` is what keeps that from reading as the stale
    success langgraph would otherwise hand back.
    """
    result: Any = None
    applied = False
    async with aclosing(events):
        async for event in events:
            payload = event.payload
            if isinstance(payload, RunInterrupted):
                result, applied = interrupt_result(payload.payload, payload.thread_id or ""), True
            elif isinstance(payload, NodeUpdated):
                applied = True
            elif isinstance(payload, RunCompleted):
                result = next((block.data for block in payload.output if isinstance(block, DataBlock)), None)
    return result, applied


def _as_state_block(state: Any) -> DataBlock:
    # `None`'s default meaning here is "no updates", which a data block can only carry as
    # `{}` — `DataBlock(data=None)` would reach the langgraph engine as a null state and fail
    # its own "must be a JSON object" check.
    return DataBlock(data=state if state is not None else {})


async def _aclose_store(store: EventStorePort) -> None:
    """Best-effort teardown for a store this Deck built itself — never one passed in.

    The stores are inconsistent in shape (``SqliteEventStore.close`` is sync, the Redis/Postgres
    stores are ``async aclose``, and the port itself requires neither), so this checks for
    either rather than asking every caller to know which store it got.
    """
    if hasattr(store, "aclose"):
        await store.aclose()  # ty: ignore[call-non-callable] — duck-typed: EventStorePort itself declares neither
    elif hasattr(store, "close"):
        store.close()  # ty: ignore[call-non-callable] — same reason


def _coerce_skills(value: str | Path | Sequence[str | Path] | Skills | None) -> Skills | None:
    if value is None or isinstance(value, Skills):
        return value
    if isinstance(value, str | Path):
        return Skills(value)
    return Skills(*value)


def _coerce_mcp(value: str | Path | MCP | None) -> MCP | None:
    if value is None or isinstance(value, MCP):
        return value
    return MCP(value)


class Deck:
    """One catalog of agents, workflows, skills and MCP servers, and the lifecycle over it.

    Construct with ``agents=``/``workflows=`` (bare :class:`~agentdeck.authoring.agent.Agent` /
    :class:`~agentdeck.authoring.workflow.Workflow` instances — never wrapped, per
    ``docs/delivery/deck-capability-wrapper-pattern.md``) and ``skills=``/``mcp=`` (a bare path,
    a sequence of paths, or the capability object itself — coerced either way). ``context=``
    declares the type a run's ``context=`` must satisfy; this slice stores it but does not yet
    validate or inject it (``docs/delivery/plan-context-injection.md`` is its own, larger effort),
    so a non-``None`` ``context=`` at :meth:`run`/:meth:`stream`/:meth:`resume` raises rather than
    silently doing nothing with it.

    Public properties are :attr:`agents`, :attr:`workflows`, :attr:`skills` and :attr:`settings`
    only — never ``runtime`` or ``store``, the infrastructure this class exists to hide.
    """

    def __init__(
        self,
        *,
        agents: Sequence[Agent] = (),
        workflows: Sequence[Workflow] = (),
        skills: str | Path | Sequence[str | Path] | Skills | None = None,
        mcp: str | Path | MCP | None = None,
        context: type[Any] | None = None,
        session_factory: SessionFactory | None = None,
        # Private-by-name test seams — never part of the documented constructor, exactly like
        # ``tests/contract/``'s need for ``_engines=`` on the Runtime this composes. A bare
        # engine-name string restricts `build()`'s "is this engine registered" check without
        # constructing anything (see `_DEFAULT_ENGINE_NAMES`); a live `EnginePort` is what
        # `__aenter__` needs to actually play a run on — a string-only override never opens.
        _engines: Sequence[EnginePort | str] | None = None,
        _store: EventStorePort | None = None,
        _session_factory: SessionFactory | None = None,
    ) -> None:
        self._agents: Mapping[str, Agent] = MappingProxyType({a.name: a for a in agents})
        self._workflows: Mapping[str, Workflow] = MappingProxyType({w.name: w for w in workflows})
        self._skills_obj = _coerce_skills(skills)
        self._mcp_obj = _coerce_mcp(mcp)
        self._context_type = context
        self._session_factory_arg = session_factory if session_factory is not None else _session_factory
        self._engines_arg = _engines
        self._store_arg = _store

        self._state: _State = "NEW"
        self._invocables: Mapping[str, InvocableSpec] | None = None
        self._engine_instances: tuple[EnginePort, ...] | None = None
        self._runtime: Runtime | None = None
        self._sessions: ExecutionStore | None = None
        self._owns_store = False
        self._started_mcp = False
        self._closed = False

    @classmethod
    def from_project(cls, path: str | Path = PROJECT_DIR, **kwargs: Any) -> Deck:
        """The ``./.agentdeck`` (or ``path``) directory layout, unchanged — discovers the same
        ``agents=``/``workflows=``/``skills=``/``mcp=`` the plain constructor takes and hands
        them to it, so both front doors build the same catalog.

        ``**kwargs`` forwards anything else (``context=``, the private test seams) straight to
        the constructor, same as calling it directly.
        """
        package = mount_project_dir(path)
        agents = list(
            PluginRegistry(package, base_class=Agent, module_name="agent", type_dir="agents", label="agent")
            .list(refresh=True)
            .values()
        )
        workflows = list(
            PluginRegistry(package, base_class=Workflow, module_name="workflow", type_dir="workflows", label="workflow")
            .list(refresh=True)
            .values()
        )
        project_root = Path(path).resolve()
        # ``.mcp.json`` lives at the project root — a sibling of ``.agentdeck/``, not inside it,
        # matching where ``config.yaml``/``.env`` already resolve from. Its absence means "no
        # servers" rather than a configuration error, the same fail-open rule an empty
        # ``mcp.servers`` map always had.
        mcp_json = project_root.parent / ".mcp.json"
        return cls(
            agents=agents,
            workflows=workflows,
            skills=Skills(project_root / "skills"),
            mcp=MCP(mcp_json) if mcp_json.is_file() else None,
            **kwargs,
        )

    @property
    def agents(self) -> Mapping[str, Agent]:
        return self._agents

    @property
    def workflows(self) -> Mapping[str, Workflow]:
        return self._workflows

    @property
    def skills(self) -> Skills | None:
        return self._skills_obj

    @property
    def settings(self) -> Settings:
        return get_settings()

    def build(self) -> Deck:
        """Validate the whole catalog and compile every agent/workflow to an ``InvocableSpec``.

        Idempotent: a second call is a no-op once ``BUILT`` (or later). Reads local files
        (every ``SKILL.md``, the MCP file) but opens no connection and starts no MCP server —
        engines are named, never constructed, until :meth:`__aenter__` actually needs one.
        """
        if self._state != "NEW":
            return self
        skills_by_name = self._skills_obj.build() if self._skills_obj is not None else {}
        mcp_names = frozenset(self._mcp_obj.build()) if self._mcp_obj is not None else frozenset()
        for agent in self._agents.values():
            self._validate_agent_skills(agent, skills_by_name)
            self._validate_agent_mcp(agent, mcp_names)
            self._validate_agent_workflow_tools(agent)
        engine_names = tuple(self._engines_arg) if self._engines_arg is not None else _DEFAULT_ENGINE_NAMES
        registry = InvocableRegistry(engine_names)
        self._invocables = registry.load(
            agents=list(self._agents.values()),
            workflows=list(self._workflows.values()),
            resolve_skills=skills_resolver(self._skills_obj) if self._skills_obj is not None else None,
            resolve_workflow_tool=self._resolve_workflow_tool,
        )
        self._state = "BUILT"
        return self

    def _validate_agent_skills(self, agent: Agent, skills_by_name: Mapping[str, Any]) -> None:
        if not agent.skills:
            return
        if self._skills_obj is None:
            raise ConfigError(
                f"agent {agent.name!r} declares skills={list(agent.skills)!r}, but this Deck has no skills= configured."
            )
        if unknown := sorted(set(agent.skills) - set(skills_by_name)):
            raise ConfigError(
                f"agent {agent.name!r} declares unknown skill(s) {unknown}. Available: {sorted(skills_by_name)}."
            )

    def _validate_agent_mcp(self, agent: Agent, mcp_names: frozenset[str]) -> None:
        if not agent.mcp:
            return
        if self._mcp_obj is None:
            raise ConfigError(
                f"agent {agent.name!r} declares mcp={list(agent.mcp)!r}, but this Deck has no mcp= configured."
            )
        if unknown := sorted(set(agent.mcp) - mcp_names):
            raise ConfigError(
                f"agent {agent.name!r} declares unknown MCP server(s) {unknown}. Available: {sorted(mcp_names)}."
            )

    def _validate_agent_workflow_tools(self, agent: Agent) -> None:
        for tool in agent.tools:
            if isinstance(tool, Workflow) and tool.name not in self._workflows:
                raise ConfigError(
                    f"agent {agent.name!r} uses workflow {tool.name!r} as a tool, but it is not in "
                    f"this Deck's workflows=. Available: {sorted(self._workflows)}."
                )

    def _resolve_workflow_tool(self, workflow: Workflow) -> FunctionTool:
        # A safety net behind ``_validate_agent_workflow_tools`` above — reachable only if a
        # future caller compiles an agent against a different Deck's catalog than it validated.
        if workflow.name not in self._workflows:
            raise ConfigError(f"workflow {workflow.name!r} is used as a tool but is not registered in workflows=.")
        return workflow.as_tool()

    async def __aenter__(self) -> Deck:
        """Open: build (if not yet), start the MCP lifecycle, and compose the Runtime.

        Everything ``build()`` deliberately left alone happens here — constructing the real
        engines, the event store, the session factory, and connecting every configured MCP
        server (soft per-server failure, same as today).
        """
        self.build()
        if self._state == "OPEN":
            return self
        if self._engines_arg is not None:
            live = [e for e in self._engines_arg if not isinstance(e, str)]
            if len(live) != len(self._engines_arg):
                raise ConfigError(
                    "_engines= given as bare engine-name strings only restricts build()'s "
                    "validation; opening a Deck needs live EnginePort instances to run on."
                )
            self._engine_instances = tuple(live)
        else:
            self._engine_instances = (
                OpenAIAgentsEngine(self._ensure_sessions(), settings=resolve_run_settings()),
                LangGraphEngine(durable_checkpoint=resolve_checkpoint()),
            )
        self._owns_store = self._store_arg is None
        store = self._store_arg if self._store_arg is not None else resolve_event_store()
        self._runtime = build_runtime(
            engines=self._engine_instances,
            invocables=self._invocables,
            store=store,
            control=resolve_control_port(),
        )
        await MCPLifecycle.startup(self._mcp_obj.config() if self._mcp_obj is not None else None)
        self._started_mcp = True
        self._state = "OPEN"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Flush the Runtime's sinks, close what this Deck itself opened, leave the rest.

        The ownership rule with no exemption: an ``MCP(...)`` this Deck holds is configuration
        it is the one to shut down, regardless of whether the caller built the object or a bare
        path was coerced into one. A store passed in via the private ``_store=`` seam is never
        touched — it was the caller's before this Deck ever saw it. Idempotent.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self._runtime is not None:
                await self._runtime.drain()
            if self._sessions is not None:
                await self._sessions.aclose()
            if self._owns_store and self._runtime is not None:
                await _aclose_store(self._runtime.store)
        finally:
            if self._started_mcp:
                self._started_mcp = False
                await MCPLifecycle.shutdown()
            self._state = "CLOSED"

    def _ensure_sessions(self) -> ExecutionStore:
        if self._sessions is None:
            factory = self._session_factory_arg
            if factory is None:
                factory = SessionFactory.from_settings(self.settings.session)
            self._sessions = ExecutionStore(factory)
        return self._sessions

    def _require_open(self) -> Runtime:
        if self._state != "OPEN" or self._runtime is None:
            raise ConfigError("this Deck is not open: use `async with deck:` (or `await deck.__aenter__()`) first.")
        return self._runtime

    def _agent(self, name: str) -> Agent:
        try:
            return self._agents[name]
        except KeyError:
            raise NotFoundError(f"No agent named {name!r}. Available: {sorted(self._agents)}.") from None

    def _workflow(self, name: str) -> Workflow:
        try:
            return self._workflows[name]
        except KeyError:
            raise NotFoundError(f"No workflow named {name!r}. Available: {sorted(self._workflows)}.") from None

    def _root(self, name: str) -> Agent | Workflow:
        if name in self._agents:
            return self._agents[name]
        if name in self._workflows:
            return self._workflows[name]
        raise NotFoundError(
            f"No agent or workflow named {name!r}. Available: {sorted({*self._agents, *self._workflows})}."
        )

    def _require_no_context(self, context: Any) -> None:
        # ponytail: ``Context[T]`` injection (docs/delivery/plan-context-injection.md) is its own
        # multi-step epic — no core `RunContext.data`, no engine bridge — out of scope for this
        # slice. Refusing a non-`None` value beats the alternative of silently doing nothing with
        # an application's context, the same reason `compile_agent` refuses undeclared skills.
        if context is not None:
            raise ConfigError("context injection is not wired in this slice (docs/delivery/plan-context-injection.md).")

    # --- the flat run-control surface -----------------------------------------------------

    async def run(
        self,
        name: str,
        input: Any,
        *,
        session_id: str | None = None,
        namespace: str | None = None,
        run_id: str | None = None,
        context: Any = None,
    ) -> TurnResult | Any:
        """Run ``name`` — an agent or a workflow, whichever this catalog holds it as — and
        return its outcome: a :class:`TurnResult` for an agent, the final state (or an
        :class:`~agentdeck.authoring.interrupts.InterruptResult`) for a workflow.
        """
        self._require_no_context(context)
        root = self._root(name)
        runtime = self._require_open()
        content = coerce_input(input) if isinstance(root, Agent) else [_as_state_block(input)]
        run = runtime.run(name, content, session_id=session_id, namespace=namespace, run_id=run_id)
        if isinstance(root, Agent):
            return await _turn_result(run)
        result, _ = await _workflow_result(run)
        return result

    async def stream(
        self,
        name: str,
        input: Any,
        *,
        session_id: str | None = None,
        namespace: str | None = None,
        run_id: str | None = None,
        context: Any = None,
    ) -> AsyncIterator[Event]:
        """Streaming counterpart to :meth:`run`: yields the run's own canonical events."""
        self._require_no_context(context)
        root = self._root(name)
        runtime = self._require_open()
        content = coerce_input(input) if isinstance(root, Agent) else [_as_state_block(input)]
        async with aclosing(
            runtime.run(name, content, session_id=session_id, namespace=namespace, run_id=run_id)
        ) as run:
            async for event in run:
                yield event

    async def pause(self, run_id: str, reason: str | None = None) -> bool:
        """Ask the run to stop at its next safe point, and record why — recorded, not stopped:
        a run inside a tool call stops at its own next safe point, and its own ``run.paused``
        event is what reports that it did.
        """
        return await self._require_open().signal(run_id, Signal.PAUSE, reason)

    async def cancel(self, run_id: str, reason: str | None = None) -> bool:
        """Ask the run to stop for good at its next safe point. Cancellation is terminal."""
        return await self._require_open().signal(run_id, Signal.CANCEL, reason)

    async def resume(self, run_id: str, reason: str | None = None, *, context: Any = None) -> list[Event]:
        """Continue a paused run, returning every event the continuation produced. Empty means
        nothing was resumed — this run is not paused."""
        self._require_no_context(context)
        return [event async for event in self._require_open().resume_run(run_id, reason=reason)]

    async def status(self, run_id: str) -> RunStatus | None:
        """This run's current status, or ``None`` if the log has never heard of it."""
        runtime = self._require_open()
        ctx = _new_context()
        for summary in await runtime.store.list_runs(ctx):
            if summary.run_id == run_id:
                return summary.status
        return None

    async def pending(self, namespace: str | None = None) -> list[PendingRun]:
        """Every run currently waiting on a human, across this Deck's whole catalog."""
        return await self._require_open().pending(namespace=namespace)

    # --- ports carried across from v1's `App`, unchanged in behavior ----------------------

    async def run_agent(self, name: str, message: Any) -> TurnResult:
        """One-shot run of a discovered agent, recorded on the Runtime."""
        self._agent(name)
        return await _turn_result(self._require_open().run(name, coerce_input(message)))

    async def chat(self, name: str, session_id: str, message: Any) -> TurnResult:
        """One conversational turn: same ``session_id`` -> same history across calls."""
        self._agent(name)
        return await _turn_result(self._require_open().run(name, coerce_input(message), session_id=session_id))

    async def chat_stream(self, name: str, session_id: str, message: Any) -> AsyncIterator[Event]:
        """Streaming counterpart to :meth:`chat`."""
        self._agent(name)
        async with aclosing(self._require_open().run(name, coerce_input(message), session_id=session_id)) as run:
            async for event in run:
                yield event

    async def run_workflow(self, name: str, state: Any = None, *, thread_id: str | None = None) -> Any:
        """One run of a discovered workflow, recorded on the Runtime; returns the final state
        or an :class:`~agentdeck.authoring.interrupts.InterruptResult` while paused.
        """
        self._workflow(name)
        runtime = self._require_open()
        run = runtime.run(name, [_as_state_block(state)], session_id=thread_id)
        result, _ = await _workflow_result(run)
        return result

    async def resume_workflow(self, name: str, thread_id: str, value: Any) -> Any:
        """Answer the interrupt paused on ``thread_id``; returns the final state or the next
        interrupt."""
        self._workflow(name)
        runtime = self._require_open()
        paused = await self._paused_workflow_run(runtime, name, thread_id)
        result, applied = await _workflow_result(
            runtime.resume(name, thread_id, value, run_id=paused.run_id, session_id=paused.session_id)
        )
        if not applied:
            raise NotFoundError(f"No paused run of {name!r} on thread {thread_id!r}.")
        return result

    async def run_workflow_stream(
        self, name: str, state: Any = None, *, thread_id: str | None = None
    ) -> AsyncIterator[dict[str, Any] | InterruptResult]:
        """Streaming counterpart to :meth:`run_workflow`: a ``node_update``/``custom``/``done``
        event per the workflow's own graph stream, or an :class:`InterruptResult` on a pause.

        Unlike :meth:`run_workflow`, this drives the graph directly rather than the Runtime —
        the same trade v1's ``App.run_workflow_stream`` made — so a run started here writes
        nothing to the event log and cannot be found by :meth:`resume_workflow`'s own lookup.
        """
        workflow = self._workflow(name)
        async for event in workflow.run_stream(state, thread_id=thread_id):
            yield event

    async def _paused_workflow_run(self, runtime: Runtime, name: str, thread_id: str) -> PendingRun:
        paused = next(
            (run for run in await runtime.pending() if run.invocable == name and run.thread_id == thread_id),
            None,
        )
        if paused is None:
            raise NotFoundError(f"No paused run of {name!r} on thread {thread_id!r}.")
        return paused

    async def pending_interrupts(self, name: str | None = None) -> list[InterruptResult]:
        """Approval inbox: every thread paused on an interrupt, for one workflow or all of them."""
        workflows = [self._workflow(name)] if name else list(self._workflows.values())
        pending: list[InterruptResult] = []
        for workflow in workflows:
            pending.extend(await workflow.pending())
        return pending

    async def due_resumes(self, now: datetime | None = None) -> list[InterruptResult]:
        """Timer-paused threads (``sleep_until``) whose wake time has passed."""
        now = _require_aware(now) if now is not None else datetime.now(UTC)
        pending = await self.pending_interrupts()
        return [p for p in pending if (wake_at := wake_at_of(p["payload"])) is not None and wake_at <= now]

    async def tick(self, now: datetime | None = None) -> list[Any]:
        """Resume every thread whose ``sleep_until`` timer is due."""
        now = _require_aware(now) if now is not None else datetime.now(UTC)
        results = []
        for workflow in self._workflows.values():
            for pending in await workflow.pending():
                wake_at = wake_at_of(pending["payload"])
                if wake_at is not None and wake_at <= now:
                    results.append(await workflow.resume(pending["thread_id"], wake_at))
        return results

    def session_for(self, session_id: str) -> Session:
        """Conversation memory for ``session_id`` — the engine's own store, so a turn started
        here and one started over HTTP land in the same conversation."""
        return self._ensure_sessions().session_for(_new_context(session_id))

    def asgi(self) -> Any:
        """The ASGI app ``agentdeck serve`` runs: a FastAPI app whose lifespan opens this Deck
        on startup and closes it on shutdown, so a mounted Deck needs no separate
        ``async with``. The HTTP contract is v1's own, unchanged (``tests/golden/`` proves it
        byte-for-byte) — building it lives in ``agentdeck.serve`` (the one module allowed to
        import FastAPI), not here, so ``agentdeck.deck`` stays free of that dependency.
        """
        from agentdeck.serve import build_asgi_app

        return build_asgi_app(self)


__all__ = ["Deck", "TurnResult"]
