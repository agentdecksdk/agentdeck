"""Single entry point: one object that discovers and serves a project's
agents, workflows, and skills.

    from agentdeck import App

    app = App()                   # serves ./.agentdeck: <bundle>/agent.py,
    app.load()                    # <bundle>/workflow.py and a skills/ dir

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

import sys
import types
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import SQLiteSession

from agentdeck.agents.mcp.lifecycle import MCPLifecycle
from agentdeck.agents.registry import AgentRegistry
from agentdeck.agents.runners import HeadlessRunner, StreamDone
from agentdeck.runtime.registry import _package_dir
from agentdeck.runtime.sessions import SessionFactory
from agentdeck.runtime.settings import Settings, get_settings
from agentdeck.skills.bundle import SkillRegistry
from agentdeck.workflows.registry import WorkflowRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents.memory.session import Session

PROJECT_DIR = ".agentdeck"
_PROJECT_ALIAS = "agentdeck_project"


def _mount_project_dir() -> str:
    """Make ``./.agentdeck`` importable as package ``agentdeck_project``.

    A hidden dir can't be imported by name, so we register a synthetic parent
    package whose ``__path__`` points at it; the normal import machinery then
    resolves ``<alias>.<bundle>.agent`` as namespace packages — no
    ``__init__.py`` needed anywhere under the project dir.
    """
    root = Path(PROJECT_DIR).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project dir not found: {root}")
    module = types.ModuleType(_PROJECT_ALIAS)
    module.__path__ = [str(root)]
    module.__spec__ = ModuleSpec(_PROJECT_ALIAS, None, is_package=True)
    module.__spec__.submodule_search_locations = [str(root)]
    sys.modules[_PROJECT_ALIAS] = module
    return _PROJECT_ALIAS


@dataclass(slots=True)
class App:
    """Facade over the three plug-in registries plus settings.

    Always serves the ``./.agentdeck`` project dir of the current working
    directory: ``<bundle>/agent.py``, ``<bundle>/workflow.py``, ``skills/``.
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

    def __post_init__(self) -> None:
        package = _mount_project_dir()
        self.agents = AgentRegistry(package)
        self.workflows = WorkflowRegistry(package)
        self.skills = SkillRegistry((_package_dir(package) or Path(PROJECT_DIR)) / "skills")
        if self.session_factory is None:
            self.session_factory = SessionFactory.from_settings(self.settings.session)

    @property
    def settings(self) -> Settings:
        return get_settings()

    def load(self) -> dict[str, list[str]]:
        """Discover and *instantiate* everything; raises on the first broken bundle.

        Returns ``{"agents": [...], "workflows": [...], "skills": [...]}`` and stashes
        it on ``self.inventory`` so callers don't have to re-run the compile pass.
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
        """
        return await self.workflows.get(name).run(state, thread_id=thread_id, **runner_options)

    def session_for(self, session_id: str) -> Session:
        """Conversation memory for ``session_id`` — Redis when ``AGENTDECK_SESSION_REDIS_URL``
        is set, otherwise an in-process SQLite session (dev/test fallback, lost on exit).
        """
        if self.session_factory is not None:
            return self.session_factory.session_for(session_id)
        return self._local_sessions.setdefault(session_id, SQLiteSession(session_id))

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
        """Close the Redis session client and MCP servers. Idempotent — safe to call twice."""
        if self._closed:
            return
        self._closed = True
        try:
            if self.session_factory is not None:
                await self.session_factory.aclose()
        finally:
            # the MCP registry is process-wide: only tear it down if this App started it
            if self._started_mcp:
                self._started_mcp = False
                await MCPLifecycle.shutdown()


__all__ = ["App"]
