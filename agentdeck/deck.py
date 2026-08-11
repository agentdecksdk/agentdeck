"""``Deck`` — the v3 composition root: agents, workflows, skills and MCP servers become one
catalog, either handed in directly or discovered from a project directory.

    from agents import function_tool

    from agentdeck import Agent, Deck

    @function_tool
    def find_slots(day: str) -> str:
        \"\"\"Find free appointment slots on a given day.\"\"\"
        ...

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
what ``build()`` deliberately left alone — the MCP lifecycle, the Runtime's engines, event
store and event sinks — and closing tears down only what this Deck itself started (the
ownership rule: configuration this Deck instantiated is its to close; an object the caller
constructed and handed in stays the caller's).
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
from agentdeck.authoring.compile import refresh_mcp_status
from agentdeck.authoring.interrupts import interrupt_result
from agentdeck.authoring.skills import skills_resolver
from agentdeck.authoring.timers import WAKE_AT_KEY, wake_at_of
from agentdeck.authoring.workflow import Workflow
from agentdeck.composition import (
    build_runtime,
    resolve_checkpoint,
    resolve_control_port,
    resolve_event_store,
    resolve_run_settings,
    resolve_sinks,
)
from agentdeck.core.content import DataBlock, TextBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.events import NodeUpdated, RunCompleted, RunInterrupted
from agentdeck.core.ports import EventSinkPort
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.mcp import MCP
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.runtime.registry import PROJECT_DIR, PluginRegistry, mount_project_dir
from agentdeck.runtime.settings import Settings, get_settings
from agentdeck.skills import Skills

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping, Sequence

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

_State = Literal["NEW", "BUILT", "OPEN", "CLOSED"]


def _validate_sinks(sinks: Sequence[EventSinkPort] | None) -> None:
    """Refuse anything in ``sinks=`` that is not an ``EventSinkPort``, at build() time.

    Cheap, and it is the difference between a name in a traceback and a run that reaches an
    ``await sink.emit(...)`` on an object with no ``emit`` — where the dispatch's own breaker
    would swallow it as "this sink keeps failing" and the events would just go missing.
    """
    for sink in sinks or ():
        if not isinstance(sink, EventSinkPort):
            raise ConfigError(
                f"sinks= takes EventSinkPort instances; got {type(sink).__name__}. A sink "
                "implements `async def emit(self, event)` and subclasses "
                "`agentdeck.core.ports.EventSinkPort`."
            )


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
    """A run's own ``run.completed`` as a :class:`TurnResult`.

    Drains ``events`` to its natural end rather than returning the moment ``run.completed``
    is seen — closing the Runtime's generator any earlier throws ``GeneratorExit`` into it one
    line before it notices its own terminal event, recording a spurious ``run.cancelled`` right
    after a run that in fact finished cleanly.

    Raises if the stream ends without one: the engine's own exception already reached the
    caller in that case, so the only way this is hit is a run suspended by a pause or a cancel.
    """
    result: TurnResult | None = None
    async with aclosing(events):
        async for event in events:
            payload = event.payload
            if isinstance(payload, RunCompleted):
                data = next((block.data for block in payload.output if isinstance(block, DataBlock)), None)
                if data is not None:
                    output = data
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


def _named_mapping(items: Sequence[Agent] | Sequence[Workflow], arg_name: str) -> Mapping[str, Any]:
    # Mirrors PluginRegistry's own collision rule: `{a.name: a for a in agents}` would collapse
    # a duplicate to whichever came last with no error, the same silent shadow this rule refuses
    # on the discovery path.
    found: dict[str, Any] = {}
    for item in items:
        if item.name in found:
            raise ConfigError(
                f"two entries in {arg_name}= both use the name {item.name!r}; one name is one "
                f"invocable — rename one of them."
            )
        found[item.name] = item
    return MappingProxyType(found)


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


# The one Deck currently holding the process, or ``None``. Discovery mounts every project under
# a single module alias and the MCP lifecycle keys its servers class-wide, so a second live Deck
# reads the first one's bundles and shares its servers. Enforced here rather than made to work:
# v3 is single-deck by ruling, and this refuses the case loudly instead of failing open.
_live_deck: Deck | None = None


def _origin(project_path: Path | None) -> str:
    return str(project_path) if project_path is not None else "a code-first Deck(...), no project dir"


def _refuse_second_deck(incoming: str) -> None:
    """Raise if a Deck already holds the process. Separate from :func:`_claim_process` because
    ``from_project`` has to refuse *before* it mounts: mounting evicts the live Deck's cached
    bundle modules and rebinds the alias to the new root, so refusing afterwards would leave the
    surviving Deck pointing at a project it does not own — fine until a durable workflow resumes
    and re-imports a bundle class, which would then resolve against the wrong directory."""
    if _live_deck is None:
        return
    raise ConfigError(
        f"a Deck is already live in this process ({_origin(_live_deck._project_path)}); "
        f"agentdeck v3 supports one Deck per process, so this one ({incoming}) would read the "
        "first one's bundles and share its MCP servers. Close the first with "
        "`await deck.aclose()` before constructing another. Two decks side by side is "
        "deferred — agentdeck issue #213."
    )


def _claim_process(deck: Deck) -> None:
    global _live_deck
    _refuse_second_deck(_origin(deck._project_path))
    _live_deck = deck


def _release_process(deck: Deck) -> None:
    # Only its own claim: a Deck closed after a later one took the process must not free that
    # later one's slot.
    global _live_deck
    if _live_deck is deck:
        _live_deck = None


class Deck:
    """One catalog of agents, workflows, skills and MCP servers, and the lifecycle over it.

    Construct with ``agents=``/``workflows=`` (bare :class:`~agentdeck.authoring.agent.Agent` /
    :class:`~agentdeck.authoring.workflow.Workflow` instances — never wrapped, per
    ``docs/delivery/deck-capability-wrapper-pattern.md``) and ``skills=``/``mcp=`` (a bare path,
    a sequence of paths, or the capability object itself — coerced either way).

    ``sinks=`` are the read-only taps on this Deck's event stream — telemetry, cost, audit, any
    :class:`~agentdeck.core.ports.EventSinkPort`. They open once, when the Deck opens, before any
    run can start. Three states, and the default is not a fourth: ``None`` (the default) opens
    the configured Langfuse sink if ``AGENTDECK_LANGFUSE_*`` names one and nothing otherwise;
    a sequence opens exactly those, in order, and suppresses the settings-derived one; ``()``
    opens none at all. A sink is fire-and-forget by the port's contract — one that is slow or
    raises costs its own backlog and never a run.

    There is no ``deck.sinks`` property, for the reason there is no ``runtime`` or ``store``:
    nothing needs one, and a property is additive later while removing one is not.

    There is no ``context=``: declaring a context type is meaningless until something injects
    one, and a constructor parameter that cannot be used is worse than an absent one. It returns
    with ``Context[T]`` (``docs/delivery/plan-context-injection.md``), which is additive.

    Public properties are :attr:`agents`, :attr:`workflows`, :attr:`skills` and :attr:`settings`
    only — never ``runtime`` or ``store``, the infrastructure this class exists to hide.

    **One Deck per process.** Constructing a second one while the first is still open raises
    ``ConfigError`` naming both projects. Sequential decks are fine: close one, construct the
    next. Two side by side is deferred to agentdeck issue #213.
    """

    def __init__(
        self,
        *,
        agents: Sequence[Agent] = (),
        workflows: Sequence[Workflow] = (),
        skills: str | Path | Sequence[str | Path] | Skills | None = None,
        mcp: str | Path | MCP | None = None,
        sinks: Sequence[EventSinkPort] | None = None,
        session_factory: SessionFactory | None = None,
        # Private-by-name test seams — never part of the documented constructor, exactly like
        # ``tests/contract/``'s need for ``_engines=`` on the Runtime this composes. A bare
        # engine-name string restricts `build()`'s "is this engine registered" check without
        # constructing anything (see `_DEFAULT_ENGINE_NAMES`); a live `EnginePort` is what
        # `__aenter__` needs to actually play a run on — a string-only override never opens.
        _engines: Sequence[EnginePort | str] | None = None,
        _store: EventStorePort | None = None,
        _session_factory: SessionFactory | None = None,
        # Not a test seam like the two below: the bundle path each discovered ``agents``/
        # ``workflows`` entry came from, so a compile failure at build() can still name it —
        # ``from_project`` is the only caller, since a code-first entry has no bundle to name.
        _bundle_of: Mapping[str, str] | None = None,
        # Likewise ``from_project``'s alone: the project this catalog was discovered from, so
        # the one-Deck-per-process refusal can name it.
        _project_path: Path | None = None,
    ) -> None:
        self._agents: Mapping[str, Agent] = _named_mapping(agents, "agents")
        self._workflows: Mapping[str, Workflow] = _named_mapping(workflows, "workflows")
        self._skills_obj = _coerce_skills(skills)
        self._mcp_obj = _coerce_mcp(mcp)
        self._sinks_arg = sinks
        self._session_factory_arg = session_factory if session_factory is not None else _session_factory
        self._engines_arg = _engines
        self._store_arg = _store
        self._bundle_of = _bundle_of or {}
        self._project_path = _project_path

        self._state: _State = "NEW"
        self._invocables: Mapping[str, InvocableSpec] | None = None
        self._engine_instances: tuple[EnginePort, ...] | None = None
        self._runtime: Runtime | None = None
        self._sessions: ExecutionStore | None = None
        self._owns_store = False
        self._started_mcp = False
        self._closed = False
        # Last, so a constructor that raises above (a duplicate name, an unreadable capability)
        # leaves the process free for the next attempt instead of poisoning it.
        _claim_process(self)

    @classmethod
    def from_project(cls, path: str | Path = PROJECT_DIR, **kwargs: Any) -> Deck:
        """The ``./.agentdeck`` (or ``path``) directory layout, unchanged — discovers the same
        ``agents=``/``workflows=``/``skills=``/``mcp=`` the plain constructor takes and hands
        them to it, so both front doors build the same catalog.

        ``**kwargs`` forwards anything else (``sinks=``, the private test seams) straight to
        the constructor, same as calling it directly.
        """
        # Before mounting, not after: see :func:`_refuse_second_deck`. The constructor claims
        # the process, but by then this method has already evicted and rebound the alias.
        _refuse_second_deck(str(Path(path).resolve()))
        package = mount_project_dir(path)
        agent_registry = PluginRegistry(
            package, base_class=Agent, module_name="agent", type_dir="agents", label="agent"
        )
        agents = list(agent_registry.list(refresh=True).values())
        workflow_registry = PluginRegistry(
            package, base_class=Workflow, module_name="workflow", type_dir="workflows", label="workflow"
        )
        workflows = list(workflow_registry.list(refresh=True).values())
        project_root = Path(path).resolve()
        # ``.mcp.json`` lives at the project root — a sibling of ``.agentdeck/``, not inside it.
        # For the default ``path`` this is also where ``config.yaml``/``.env`` resolve from
        # (both read off ``Path.cwd()``); an explicit non-default ``path`` only matches that if
        # the caller also runs from its parent. Its absence means "no servers" rather than a
        # configuration error, the same fail-open rule an empty ``mcp.servers`` map always had.
        mcp_json = project_root.parent / ".mcp.json"
        return cls(
            agents=agents,
            workflows=workflows,
            skills=Skills(project_root / "skills"),
            mcp=MCP(mcp_json) if mcp_json.is_file() else None,
            _bundle_of={**agent_registry.bundle_files(), **workflow_registry.bundle_files()},
            _project_path=project_root,
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

        Registering the MCP server specs (``MCPLifecycle.configure``, itself network-free) here
        means an agent's ``mcp=`` compiles against known-but-not-yet-connected names rather than
        unknown ones — the only warning this can still log is a genuine open-time drop, not a
        false "not found in config" for a server that will, in fact, connect once opened.

        ``sinks=`` are shape-checked here for the same reason, and *only* shape-checked: no
        sink is opened, no telemetry client is constructed and no exporter is contacted, so a
        deck with Langfuse configured still validates where Langfuse is unreachable.
        """
        if self._state != "NEW":
            return self
        _validate_sinks(self._sinks_arg)
        skills_by_name = self._skills_obj.build() if self._skills_obj is not None else {}
        mcp_names = frozenset(self._mcp_obj.build()) if self._mcp_obj is not None else frozenset()
        if self._mcp_obj is not None:
            MCPLifecycle.configure(self._mcp_obj.config())
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
            bundle_of=self._bundle_of,
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
            if not isinstance(tool, Workflow):
                continue
            if tool.name not in self._workflows:
                raise ConfigError(
                    f"agent {agent.name!r} uses workflow {tool.name!r} as a tool, but it is not in "
                    f"this Deck's workflows=. Available: {sorted(self._workflows)}."
                )
            if tool.durable:
                # `as_tool()` calls `run(args)` with no thread_id, and a durable workflow needs
                # one to load and persist its checkpoint — so the tool raises the moment a model
                # calls it. Refusing at build() turns that into a validation error rather than a
                # surprise mid-turn; giving a tool-invoked workflow a thread is its own design
                # question (#193), not something to guess at here.
                raise ConfigError(
                    f"agent {agent.name!r} uses workflow {tool.name!r} as a tool, but it is "
                    f"durable=True. A workflow invoked as a tool gets no thread_id, which a "
                    f"durable workflow requires — set durable=False on {tool.name!r}, or call it "
                    f"as a root invocable via deck.run() where you can pass a session."
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
        engines, the event store, the session factory, the telemetry client, and connecting
        every configured MCP server (soft per-server failure, same as today). MCP status on
        every already-compiled agent is refreshed right after, since ``build()`` resolved it
        before anything connected.

        The sinks open exactly once, here, and before the Runtime exists — they are registered
        as it is assembled, so a run can never be the thing that turns observability on. That
        ordering is what #181 and #162 are both about: telemetry used to be built underneath
        this, from settings, and started by whichever run happened to come first.
        """
        if self._state == "CLOSED":
            # CLOSED is terminal: aclose()'s own idempotency guard would otherwise skip
            # draining/closing everything a second open builds fresh, on the mistaken belief
            # there was nothing left to do.
            raise ConfigError("this Deck is already closed; construct a new one to open again.")
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
        # Resolved here, not in ``build_runtime``: a settings-derived sink holds a live client,
        # so the moment it is constructed is a lifecycle decision (#181), and constructing it
        # while a Runtime was assembled is what #162's first defect was. Caller-supplied sinks
        # are taken as-is and suppress the settings-derived one entirely — a Deck told which
        # taps to open must not quietly open a Langfuse client beside them.
        sinks = resolve_sinks() if self._sinks_arg is None else tuple(self._sinks_arg)
        self._runtime = build_runtime(
            engines=self._engine_instances,
            invocables=self._invocables,
            store=store,
            sinks=sinks,
            control=resolve_control_port(),
        )
        await MCPLifecycle.startup(self._mcp_obj.config() if self._mcp_obj is not None else None)
        self._started_mcp = True
        if self._mcp_obj is not None:
            # build() compiled every agent's mcp= against MCPLifecycle before any server had
            # connected, so its tools/banner are stale the moment startup() above finishes —
            # correct the compiled agent in place before anything can run a turn against it.
            invocables = self._invocables
            assert invocables is not None  # build() just above guarantees this
            agents = list(self._agents.values())
            refresh_mcp_status({name: invocables[name].native for name in self._agents}, agents)
        self._state = "OPEN"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Flush the Runtime's sinks, close what this Deck itself opened, leave the rest.

        The ownership rule with no exemption: an ``MCP(...)`` this Deck holds is configuration
        it is the one to shut down, regardless of whether the caller built the object or a bare
        path was coerced into one. A store passed in via the private ``_store=`` seam is never
        touched — it was the caller's before this Deck ever saw it, and neither is a sink passed
        to ``sinks=``. Idempotent, and reachable from every state — a Deck built but never opened
        still has a process claim to give up.

        Every sink is told its stream has ended, including a caller's own: that is
        ``EventSinkPort.close``\'s whole job — "write out whatever is still buffered" — and
        skipping it to respect ownership would throw away exactly the audit or cost records the
        caller registered the sink to keep. Ownership decides what this Deck *constructs*, not
        who gets told the stream is over.

        Nothing shuts the Langfuse SDK down. Measured against langfuse 4.14.1, the SDK holds one
        resource manager per public key in a process-global cache and ``shutdown()`` marks it
        dead without evicting it — so the next Deck opened in this process (sequential decks are
        supported; see :func:`_refuse_second_deck`) would be handed the dead one and export
        nothing, silently. Flushing is what the deck owes; the SDK\'s own ``atexit`` stops its
        threads at the scope that resource actually has.
        """
        if self._closed:
            return
        self._closed = True
        try:
            # Draining closes the sinks, which is what finishes the Langfuse sink's open
            # observations and pushes the SDK's buffer out of the process.
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
            _release_process(self)

    @staticmethod
    def _release() -> None:
        # The test suite's safety net for the process claim: a Deck constructed by a sync test
        # and never opened has no `await deck.aclose()` to release it. Not public API.
        global _live_deck
        _live_deck = None

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

    def _root(self, name: str) -> Agent | Workflow:
        if name in self._agents:
            return self._agents[name]
        if name in self._workflows:
            return self._workflows[name]
        raise NotFoundError(
            f"No agent or workflow named {name!r}. Available: {sorted({*self._agents, *self._workflows})}."
        )

    # --- the flat run-control surface -----------------------------------------------------

    async def run(
        self,
        name: str,
        input: Any,
        *,
        session_id: str | None = None,
        namespace: str | None = None,
        run_id: str | None = None,
    ) -> TurnResult | Any:
        """Run ``name`` — an agent or a workflow, whichever this catalog holds it as — and
        return its outcome: a :class:`TurnResult` for an agent, the final state (or an
        :class:`~agentdeck.authoring.interrupts.InterruptResult`) for a workflow.
        """
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
    ) -> AsyncGenerator[Event, None]:
        """Streaming counterpart to :meth:`run`: yields the run's own canonical events."""
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

    async def resume(self, run_id: str, reason: str | None = None) -> list[Event]:
        """Continue a paused run, returning every event the continuation produced. Empty means
        nothing was resumed — this run is not paused."""
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

    async def answer(self, run_id: str, value: Any) -> Any:
        """Answer the interrupt the run named by ``run_id`` is paused on; returns the final
        state or the next interrupt. Pairs with :meth:`pending`: list the inbox, answer one run
        by the ``run_id`` it named there — the lookup this needs (invocable, thread, session)
        travels with it, so a caller supplies only the id and the value.
        """
        runtime = self._require_open()
        pending = next((run for run in await runtime.pending() if run.run_id == run_id), None)
        if pending is None:
            raise NotFoundError(f"No pending run {run_id!r}.")
        result, applied = await _workflow_result(
            runtime.resume(
                pending.invocable, pending.thread_id, value, run_id=pending.run_id, session_id=pending.session_id
            )
        )
        if not applied:
            raise NotFoundError(f"No pending run {run_id!r}.")
        return result

    async def due_resumes(self, now: datetime | None = None) -> list[InterruptResult]:
        """Timer-paused threads (``sleep_until``) whose wake time has passed.

        Listed off each workflow's own checkpointer rather than :meth:`pending`'s event log
        (see :meth:`_pending_interrupts`) — a choice, not an oversight: by default the
        checkpoint backend is durable (``sqlite``) while the event store is not (``memory``),
        so a process that restarts keeps the checkpoint's memory of a parked thread but not
        the log's. Routing this listing through the log alone would silently stop surviving a
        restart under that (default) configuration, which is exactly #22's own guarantee
        (``tests/test_workflow_timers.py::test_tick_survives_a_process_restart``).
        """
        now = _require_aware(now) if now is not None else datetime.now(UTC)
        pending = await self._pending_interrupts()
        return [p for p in pending if (wake_at := wake_at_of(p["payload"])) is not None and wake_at <= now]

    async def _pending_interrupts(self) -> list[InterruptResult]:
        """Every thread paused on an interrupt, across the whole catalog — :meth:`due_resumes`'s
        own filter, driven straight off each workflow's checkpointer rather than the Runtime's
        log (the same source :meth:`tick` reads)."""
        pending: list[InterruptResult] = []
        for workflow in self._workflows.values():
            pending.extend(await workflow.pending())
        return pending

    async def tick(self, now: datetime | None = None) -> list[Any]:
        """Resume every thread whose ``sleep_until`` timer is due.

        A thread the checkpointer lists as due may *also* be a run :meth:`pending` already
        knows about — parked by a ``Deck.run()``/HTTP call that went through the Runtime, the
        same as any other interrupt. Resuming such a thread by calling the workflow directly
        (as this used to) never told the log: the run stayed ``WAITING_HUMAN`` and kept
        holding its session claim forever, a ghost indistinguishable from the one #114 fixed
        for :meth:`answer`. So a due thread with a matching logged run resumes through the
        Runtime instead — closing that run and freeing its claim — and only a thread with no
        logged run (parked by calling a durable :class:`Workflow`'s own ``run``/``resume``
        directly, which never opens a run in the log to begin with) falls back to the direct
        checkpointer resume, since there is no log entry to reconcile.
        """
        now = _require_aware(now) if now is not None else datetime.now(UTC)
        runtime = self._require_open()
        logged = {(run.invocable, run.thread_id): run for run in await runtime.pending()}
        results = []
        for workflow in self._workflows.values():
            for pending in await workflow.pending():
                wake_at = wake_at_of(pending["payload"])
                if wake_at is None or wake_at > now:
                    continue
                logged_run = logged.get((workflow.name, pending["thread_id"]))
                if logged_run is None:
                    results.append(await workflow.resume(pending["thread_id"], wake_at))
                    continue
                result, applied = await _workflow_result(
                    runtime.resume(
                        logged_run.invocable,
                        logged_run.thread_id,
                        # The payload's own ISO string, not the parsed `wake_at`: this value
                        # also becomes the logged `run.resumed`, and a bare `datetime` fails
                        # that event's JSON validation — recorded as a lost answer, with a
                        # warning, even though the graph itself would have resumed fine.
                        pending["payload"][WAKE_AT_KEY],
                        run_id=logged_run.run_id,
                        session_id=logged_run.session_id,
                    )
                )
                # A lost race (some other caller already resumed this run) leaves nothing to
                # report — not a fallback to the direct resume, which would just re-enter a
                # thread the winner has already moved on from.
                if applied:
                    results.append(result)
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
