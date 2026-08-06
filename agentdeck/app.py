"""Single entry point: one object that discovers and serves a project's
agents, workflows, and skills.

    from agentdeck import App

    app = App()                   # serves ./.agentdeck: agents/<bundle>/agent.py,
    app.load()                    # workflows/<bundle>/workflow.py and a skills/ dir

    app.agents.get("FileAgent")                       # BaseAgent subclass
    app.workflows.get("TranslateAndSummarize")        # BaseWorkflow subclass
    app.skills.get("md-segment-translate")            # SkillBundle

    result = await app.run_agent("FileAgent", "hello")
    state  = await app.run_workflow("TranslateAndSummarize", {"source_path": p})

``load()`` eagerly imports every bundle, builds every agent, and compiles
every workflow graph, so configuration errors surface at startup instead of
mid-conversation. Everything else stays lazy and delegates to the existing
registries and runners.

For anything long-running — and for every deployment using Redis sessions or
MCP servers — prefer ``App.open()``: it runs ``load()``, starts the MCP
lifecycle, and guarantees ``aclose()`` on exit, so the Redis client and MCP
servers are never leaked::

    async with App.open() as app:
        turn = await app.chat("FileAgent", session_id="wa-123", message="hi")
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import SQLiteSession

from agentdeck.agents.mcp.lifecycle import MCPLifecycle
from agentdeck.agents.registry import AgentRegistry
from agentdeck.agents.runners import HeadlessRunner, StreamDone
from agentdeck.composition import build_runtime, v1_engines
from agentdeck.core.ports import Signal
from agentdeck.errors import ConfigError
from agentdeck.runtime.registry import PROJECT_DIR, _package_dir, mount_project_dir
from agentdeck.runtime.sessions import SessionFactory
from agentdeck.runtime.settings import Settings, get_settings
from agentdeck.skills.bundle import SkillRegistry
from agentdeck.surfaces.serve.compat import run_context
from agentdeck.workflows.registry import WorkflowRegistry
from agentdeck.workflows.timers import wake_at_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents.memory.session import Session

    from agentdeck.core.events import Event
    from agentdeck.runtime.service import Runtime
    from agentdeck.workflows.interrupts import InterruptResult


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError(f"due_resumes/tick require a timezone-aware `now`; got naive {now!r}.")
    return now


@dataclass(slots=True)
class App:
    """Facade over the three plug-in registries plus settings.

    Always serves the ``./.agentdeck`` project dir of the current working
    directory: ``agents/<bundle>/agent.py``, ``workflows/<bundle>/workflow.py``, ``skills/``.
    """

    agents: AgentRegistry = field(init=False)
    workflows: WorkflowRegistry = field(init=False)
    skills: SkillRegistry = field(init=False)
    # DI seam for tests: pass a prebuilt factory (or one wrapping fakeredis) to skip
    # `from_settings`'s real Redis client entirely.
    session_factory: SessionFactory | None = None
    inventory: dict[str, list[str]] = field(init=False, default_factory=dict)
    _local_sessions: dict[str, Session] = field(init=False, default_factory=dict)
    _closed: bool = field(init=False, default=False)
    _started_mcp: bool = field(init=False, default=False)
    _runtime: Runtime | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        package = mount_project_dir()
        self.agents = AgentRegistry(package)
        self.workflows = WorkflowRegistry(package)
        self.skills = SkillRegistry((_package_dir(package) or Path(PROJECT_DIR)) / "skills")
        if self.session_factory is None:
            self.session_factory = SessionFactory.from_settings(self.settings.session)

    @property
    def settings(self) -> Settings:
        return get_settings()

    @property
    def runtime(self) -> Runtime:
        """The wired Runtime this App composed — what the HTTP surface runs chats on.

        Built by :meth:`load`, because a Runtime over a project that cannot be discovered
        is not something to hand a surface.
        """
        if self._runtime is None:
            raise ConfigError("no Runtime yet: call App.load() (or use App.open()) first.")
        return self._runtime

    def load(self) -> dict[str, list[str]]:
        """Discover and *instantiate* everything; raises on the first broken bundle.

        Returns ``{"agents": [...], "workflows": [...], "skills": [...]}`` and stashes
        it on ``self.inventory`` so callers don't have to re-run the compile pass, and
        composes the Runtime the HTTP surface serves from.
        """
        agents = self.agents.list(refresh=True)
        workflows = self.workflows.list(refresh=True)
        skills = self.skills.list(refresh=True)
        for agent_cls in agents.values():
            agent_cls.build()
        for wf_cls in workflows.values():
            wf_cls.build()  # compiles + caches the LangGraph graph
        for bundle in skills.values():
            bundle.output_schema  # noqa: B018 — imports/validates the declared schema
        self.inventory = {
            "agents": sorted(agents),
            "workflows": sorted(workflows),
            "skills": sorted(skills),
        }
        # One assembly seam, one caller: everything this App hands a surface comes from
        # `build_runtime`, so a second front door adds a caller instead of a second wiring.
        self._runtime = build_runtime(engines=v1_engines(self.session_for))
        return self.inventory

    async def run_agent(self, name: str, message: Any = None, **runner_options: Any) -> Any:
        """One-shot headless run of a discovered agent; returns the SDK ``RunResult``."""
        agent_cls = self.agents.get(name)
        runner = HeadlessRunner.from_agent(agent_cls.build(), **runner_options)
        return await runner.run(message)

    async def run_workflow(
        self,
        name: str,
        state: Any = None,
        *,
        thread_id: str | None = None,
        **runner_options: Any,
    ) -> Any:
        """Single ``ainvoke`` of a discovered workflow; returns the final state.

        ``thread_id`` scopes LangGraph checkpointed state — required for a
        ``durable=True`` workflow (so a later call with the same id resumes it),
        ignored otherwise.

        A run that stops on ``langgraph.types.interrupt()`` returns an
        :class:`~agentdeck.workflows.interrupts.InterruptResult`
        (``{"type": "interrupt", "payload": ..., "thread_id": ...}``) instead of a final
        state; feed the human's answer back with :meth:`resume_workflow`.
        """
        return await self.workflows.get(name).run(state, thread_id=thread_id, **runner_options)

    async def resume_workflow(self, name: str, thread_id: str, value: Any, **runner_options: Any) -> Any:
        """Answer the interrupt paused on ``thread_id``; returns the final state or the next interrupt.

        The interrupted node re-runs from its start with ``interrupt()`` returning ``value``,
        so anything it did before pausing happens twice — keep interrupt nodes pure.
        """
        return await self.workflows.get(name).resume(thread_id, value, **runner_options)

    async def pending_interrupts(self, name: str | None = None) -> list[InterruptResult]:
        """Approval inbox: every thread paused on an interrupt, for one workflow or all of them."""
        workflows = [self.workflows.get(name)] if name else list(self.workflows.list().values())
        pending: list[InterruptResult] = []
        for workflow in workflows:
            pending.extend(await workflow.pending())
        return pending

    async def due_resumes(self, now: datetime | None = None) -> list[InterruptResult]:
        """Timer-paused threads (``sleep_until``) whose wake time has passed — a filtered
        view of :meth:`pending_interrupts`. ``now`` defaults to the current UTC time and
        must be timezone-aware if given.
        """
        now = _require_aware(now) if now is not None else datetime.now(UTC)
        pending = await self.pending_interrupts()
        return [p for p in pending if (wake_at := wake_at_of(p["payload"])) is not None and wake_at <= now]

    async def tick(self, now: datetime | None = None) -> list[Any]:
        """Resume every thread whose ``sleep_until`` timer is due; resume value is its wake
        timestamp. Callers own the schedule (cron, systemd timer, a loop) — agentdeck runs
        no daemon of its own.
        """
        now = _require_aware(now) if now is not None else datetime.now(UTC)
        results = []
        for workflow in self.workflows.list().values():
            for pending in await workflow.pending():
                wake_at = wake_at_of(pending["payload"])
                if wake_at is not None and wake_at <= now:
                    results.append(await workflow.resume(pending["thread_id"], wake_at))
        return results

    async def run_workflow_stream(
        self,
        name: str,
        state: Any = None,
        *,
        thread_id: str | None = None,
        **runner_options: Any,
    ) -> AsyncIterator[dict[str, Any] | InterruptResult]:
        """Streaming counterpart to :meth:`run_workflow`: a ``node_update`` event per completed
        node, a ``custom`` event per nested :class:`~agentdeck.workflows.nodes.AgentNode`'s text
        delta (or any :func:`~langgraph.config.get_stream_writer` call), then one terminal
        ``done`` event carrying the final state. Same ``thread_id`` semantics as ``run_workflow``.

        A run that pauses on ``langgraph.types.interrupt()`` ends with an
        :class:`~agentdeck.workflows.interrupts.InterruptResult` event instead of ``done``;
        answer it with :meth:`resume_workflow`.
        """
        async for event in self.workflows.get(name).run_stream(state, thread_id=thread_id, **runner_options):
            yield event

    def session_for(self, session_id: str) -> Session:
        """Conversation memory for ``session_id`` — Redis when ``AGENTDECK_SESSION_REDIS_URL``
        is set, otherwise an in-process SQLite session (dev/test fallback, lost on exit).
        """
        if self.session_factory is not None:
            return self.session_factory.session_for(session_id)
        return self._local_sessions.setdefault(session_id, SQLiteSession(session_id))

    async def pause_run(self, run_id: str, reason: str | None = None) -> bool:
        """Ask the run to stop at its next safe point, and record why.

        Returns whether the request was recorded — not whether the run has stopped, which at
        the moment of asking nobody can know: the run may be inside a tool call that has to
        return first. Watch the run's own events for ``run.paused`` to learn that it did. Both
        idempotent and race-free by construction: asking twice records one request, and asking
        after the run ended does nothing at all.
        """
        return await self.runtime.signal(run_id, Signal.PAUSE, reason)

    async def cancel_run(self, run_id: str, reason: str | None = None) -> bool:
        """Ask the run to stop for good at its next safe point. Same answer as
        :meth:`pause_run`, and the same reason for it; the run's ``run.cancelled`` is what says
        it happened. Cancellation is terminal — a cancelled run cannot be resumed."""
        return await self.runtime.signal(run_id, Signal.CANCEL, reason)

    async def resume_run(self, run_id: str, reason: str | None = None) -> list[Event]:
        """Continue a paused run, returning every event the continuation produced.

        Empty means nothing was resumed: this run is not paused — finished, cancelled, still
        running, or already picked up by somebody else. Unlike pause and cancel, resuming is
        not a signal a live run notices, because a paused run has no loop left to notice
        anything: this call plays it on, so it returns when the run does.
        """
        return [event async for event in self.runtime.resume_run(run_id, run_context(), reason)]

    async def chat(self, name: str, session_id: str, message: Any, **runner_options: Any) -> Any:
        """One conversational turn: same ``session_id`` → same history across calls."""
        agent_cls = self.agents.get(name)
        runner = HeadlessRunner.from_agent(agent_cls.build(), **runner_options)
        return await runner.run(message, session=self.session_for(session_id))

    async def chat_stream(
        self, name: str, session_id: str, message: Any, **runner_options: Any
    ) -> AsyncIterator[str | StreamDone]:
        """Streaming counterpart to :meth:`chat`: same session, text deltas followed by one
        :class:`~agentdeck.agents.runners.StreamDone` instead of a single ``RunResult`` — the
        session is passed identically, so history ends up the same whether a turn was streamed
        or not.
        """
        agent_cls = self.agents.get(name)
        runner = HeadlessRunner.from_agent(agent_cls.build(), **runner_options)
        async for delta in runner.run_streamed(message, session=self.session_for(session_id)):
            yield delta

    @classmethod
    @asynccontextmanager
    async def open(cls, *, session_factory: SessionFactory | None = None) -> AsyncIterator[App]:
        """Build, ``load()``, and start the MCP lifecycle; ``aclose()`` runs on exit (even on error).

            async with App.open() as app:
                ...

        ``session_factory`` is the DI seam for tests (e.g. a fake wrapping fakeredis).
        """
        app = cls(session_factory=session_factory)
        try:
            app.load()
            await MCPLifecycle.startup()
            app._started_mcp = True
            yield app
        finally:
            await app.aclose()

    async def aclose(self) -> None:
        """Flush the Runtime's sinks, then close the Redis session client and MCP servers.

        Idempotent — safe to call twice.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self._runtime is not None:
                # queued sink emits die with the event loop otherwise, losing the last
                # few audit/cost events of the process
                await self._runtime.drain()
            if self.session_factory is not None:
                await self.session_factory.aclose()
        finally:
            # the MCP registry is process-wide: only tear it down if this App started it
            if self._started_mcp:
                self._started_mcp = False
                await MCPLifecycle.shutdown()


__all__ = ["App"]
